from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from btc_swing.v5.demo import preflight as pf
from btc_swing.v5.demo.client import BybitDemoClient, ExecutionDisabledError
from btc_swing.v5.demo.config import (
    EndpointNotAllowedError,
    ExecutionMode,
    assert_demo_url,
    load_demo_config,
)
from btc_swing.v5.demo.credentials import DemoCredentials
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.demo.requirements import leg_check, min_equity, reference_equity_report
from btc_swing.v5.demo.ws import private_ws_auth, ws_signature
from tests.v5.fake_bybit import FakeBybitDemo

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "btc_swing_v5_demo.yaml"
FREEZE = {
    "v5_config_hash": pf.EXPECTED_V5_HASH,
    "observation_start_ms": pf.EXPECTED_OBSERVATION_START_MS,
    "observation_start": "2026-10-07T14:59:22.972000+00:00",
}


class _FakeWS:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeWS:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def send(self, m: str) -> None:
        self.sent.append(json.loads(m))

    async def recv(self) -> str:
        return json.dumps(self.reply)


def _connect(reply: dict[str, Any], seen: list[str]) -> Any:
    def c(url: str, **kw: Any) -> _FakeWS:
        seen.append(url)
        return _FakeWS(reply)

    return c


def test_config_reference_equity_2000_and_ws_allowlist() -> None:
    d = load_demo_config(CFG)
    assert (
        d.mode is ExecutionMode.DISABLED
        and d.reference_equity_usdt == 2000.0
        and d.risk_per_trade == 0.0025
    )
    assert d.ws_private == "wss://stream-demo.bybit.com/v5/private"
    with pytest.raises(EndpointNotAllowedError):
        assert_demo_url("https://api.bybit.com/v5/market/time")
    from btc_swing.v5.demo.config import assert_demo_ws_url

    for bad in (
        "wss://stream.bybit.com/v5/private",
        "wss://stream-testnet.bybit.com/v5/private",
        "ws://stream-demo.bybit.com/v5/private",
    ):
        with pytest.raises(EndpointNotAllowedError):
            assert_demo_ws_url(bad)


def test_environment_gate_blocks_cloud_container_before_any_authenticated_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: True)
    j = HashChainJournal(tmp_path / "pre.jsonl", "pre")
    out = pf.run_preflight(
        load_demo_config(CFG),
        pf.EXPECTED_V5_HASH,
        FREEZE,
        tmp_path / "none.pid",
        j,
        80000.0,
        probe=lambda: (403, "configured to block access from your country"),
    )
    assert (
        out["status"] == "BLOCKED_ENVIRONMENT"
        and out["read_only"] is None
        and out["authenticated_requests"] == 0
        and j.records() == []
    )
    names = {c["check"]: c["ok"] for c in out["environment"]}
    assert names["persistent host (not an ephemeral cloud-session container)"] is False
    md = pf.write_preflight(out, tmp_path / "reports")
    assert "NOT run" in md.read_text()


def test_environment_gate_detects_changed_freeze_and_extra_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [111, 222])
    pid = tmp_path / "run.pid"
    pid.write_text(str(os.getpid()))
    bad = {**FREEZE, "observation_start_ms": 1}
    env = pf.environment_checks(bad, "x" * 64, pid, probe=lambda: (200, "{}"))
    ok = {c["check"]: c["ok"] for c in env}
    assert (
        ok["persistent host (not an ephemeral cloud-session container)"]
        and ok["api-demo.bybit.com reachable (unauthenticated probe)"]
    )
    assert (
        not ok["exactly one forward runner"]
        and not ok["observation start unchanged"]
        and not ok["forward freeze present and V5 hash unchanged"]
    )


def test_read_only_preflight_passes_on_fake_demo_with_get_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [os.getpid() + 1])
    pid = tmp_path / "run.pid"
    pid.write_text(str(os.getpid()))
    fake = FakeBybitDemo()
    seen: list[str] = []
    j = HashChainJournal(tmp_path / "pre.jsonl", "pre")
    out = pf.run_preflight(
        load_demo_config(CFG),
        pf.EXPECTED_V5_HASH,
        FREEZE,
        pid,
        j,
        None,
        probe=lambda: (200, "{}"),
        creds=DemoCredentials(fake.api_key, fake.api_secret),
        transport=fake.transport(),
        ws_connect=_connect({"op": "auth", "success": True, "conn_id": "c1"}, seen),
    )
    assert out["status"] == "PASSED", [c for c in out["read_only"]["checks"] if not c["ok"]]
    assert (
        {r["method"] for r in fake.requests} == {"GET"}
        and fake.hosts() == {"api-demo.bybit.com"}
        and seen == ["wss://stream-demo.bybit.com/v5/private"]
    )
    req = out["requirements"]
    assert (
        req["source"] == "LIVE instrument rules"
        and req["min_notional"] == 5.0
        and req["reference_equity"] == 2000.0
    )
    text = (tmp_path / "pre.jsonl").read_text()
    assert fake.api_key not in text and fake.api_secret not in text


def test_read_only_client_refuses_state_changes_while_disabled(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    d = load_demo_config(CFG)
    cl = BybitDemoClient(
        d,
        ExecutionMode.DISABLED,
        HashChainJournal(tmp_path / "j.jsonl", "x"),
        "PREFLIGHT_READ_ONLY",
        DemoCredentials(fake.api_key, fake.api_secret),
        fake.transport(),
        read_only=True,
    )
    assert cl.wallet_balance()["total_equity"] > 0
    calls: list[Any] = [
        lambda: cl.create_order("Buy", "0.001", "Market", "X-1"),
        lambda: cl.cancel_order("X-1"),
        lambda: cl.set_leverage(2),
        lambda: cl.trading_stop(stop_loss="1"),
    ]
    for call in calls:
        with pytest.raises(ExecutionDisabledError):
            call()
    assert all(r["method"] == "GET" for r in fake.requests) and not fake.orders
    with pytest.raises(ExecutionDisabledError):
        BybitDemoClient(
            d,
            ExecutionMode.DISABLED,
            HashChainJournal(tmp_path / "k.jsonl", "x"),
            "t",
            DemoCredentials("a", "b"),
            fake.transport(),
        )


def test_private_ws_auth_message_and_failure(tmp_path: Path) -> None:
    d = load_demo_config(CFG)
    seen: list[str] = []
    ws_reply = {"op": "auth", "success": False, "ret_msg": "Params Error"}
    r = private_ws_auth(d, DemoCredentials("k-abc", "s-xyz"), connect=_connect(ws_reply, seen))
    assert not r["ok"] and r["ret_msg"] == "Params Error"
    assert (
        ws_signature("s", 123)
        == __import__("hmac").new(b"s", b"GET/realtime123", "sha256").hexdigest()
    )

    def boom(url: str, **kw: Any) -> Any:
        raise OSError("connection failed for s-xyz")

    r2 = private_ws_auth(d, DemoCredentials("k-abc", "s-xyz"), connect=boom)
    assert not r2["ok"] and "s-xyz" not in r2["ret_msg"]


def test_reference_equity_requirements() -> None:
    inst = {"qty_step": 0.001, "min_qty": 0.001, "min_notional": 5.0}
    r = reference_equity_report(2000.0, 0.0025, 80000.0, inst, stops={"median": 1.40, "p90": 2.53})
    med, p90 = r["by_stop"]["median"], r["by_stop"]["p90"]
    assert r["risk_usdt"] == pytest.approx(5.0)
    assert med["qty"] == pytest.approx(0.004) and med["all_legs_ok"] and not med["fractions_exact"]
    assert (med["tp1_qty"], med["tp2_qty"], med["remainder_qty"]) == pytest.approx(
        (0.001, 0.001, 0.002)
    )
    assert not p90["all_legs_ok"]
    # all three legs need 4 lots (0.004): equity = 0.004 * price * stop / risk
    assert med["min_equity_all_legs"]["equity"] == pytest.approx(0.004 * 80000 * 0.014 / 0.0025)
    # exact 40/30/30 at a 0.001 step needs 10 lots (0.004 / 0.003 / 0.003)
    assert med["min_equity_exact_fractions"]["qty"] == pytest.approx(0.010) and med[
        "min_equity_exact_fractions"
    ]["legs"] == pytest.approx((0.004, 0.003, 0.003))
    assert leg_check(0.0099, 0.001, 0.001, 5.0, 80000.0, 0.4, 0.3)["qty"] == pytest.approx(
        0.009
    )  # floored, never rounded up
    assert (
        min_equity(0.001, 0.001, 200.0, 80000.0, 1.4, 0.0025, 0.4, 0.3, exact=False)["qty"] >= 0.009
    )  # min notional binds
