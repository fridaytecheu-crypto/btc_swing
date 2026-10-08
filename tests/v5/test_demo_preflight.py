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


def _mac_today(monkeypatch: pytest.MonkeyPatch, fake: FakeBybitDemo) -> None:
    """Owner's Mac today: persistent machine, demo creds in env, NO forward runner, no systemd."""
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [])
    monkeypatch.setattr(pf, "_systemd_service_active", lambda: (False, "systemd not running"))
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", fake.api_key)
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", fake.api_secret)
    monkeypatch.delenv("BYBIT_EXECUTION_MODE", raising=False)


def _run(tmp_path: Path, fake: FakeBybitDemo, seen: list[str], **kw: Any) -> dict[str, Any]:
    return pf.run_preflight(
        load_demo_config(CFG),
        pf.EXPECTED_V5_HASH,
        FREEZE,
        tmp_path / "none.pid",
        HashChainJournal(tmp_path / "pre.jsonl", "pre"),
        None,
        probe=lambda: (200, "{}"),
        transport=fake.transport(),
        ws_connect=_connect({"op": "auth", "success": True, "conn_id": "c1"}, seen),
        **kw,
    )


def test_demo_gate_blocks_cloud_container_before_any_authenticated_request(
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
    demo = out["gates"][pf.DEMO_GATE]
    assert (
        out["status"] == demo["status"] == "BLOCKED_ENVIRONMENT"
        and out["read_only"] is None
        and out["authenticated_requests"] == 0
        and j.records() == []
    )
    ok = {c["check"]: c["ok"] for c in demo["checks"]}
    assert (
        ok["not an ephemeral cloud-session container (no order from the cloud environment)"]
        is False
    )
    assert out["gates"][pf.HOST_GATE]["status"] == "FAILED"
    assert pf.smoke_allowed(out)[0] is False
    md = pf.write_preflight(out, tmp_path / "reports")
    assert "NOT run" in md.read_text() and pf.HOST_GATE in md.read_text()


def test_demo_gate_passes_without_forward_runner_and_smoke_allowed_strategy_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBybitDemo()
    _mac_today(monkeypatch, fake)
    seen: list[str] = []
    out = _run(tmp_path, fake, seen)
    demo, host = out["gates"][pf.DEMO_GATE], out["gates"][pf.HOST_GATE]
    assert demo["status"] == "PASSED", [c for c in demo["checks"] if not c["ok"]]
    assert demo["required_for"] == ["EXECUTION_SMOKE", "STRATEGY_DEMO"]
    names = {c["check"] for c in demo["checks"]}
    for must in (
        "demo-only endpoint allowlist",
        "demo credentials present (BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET)",
        "API key authentication (signed account info)",
        "account is DEMO (key authenticates on api-demo.bybit.com; no other host contacted)",
        "UNIFIED wallet balance",
        "BTCUSDT current position",
        "BTCUSDT open orders",
        "BTCUSDT instrument rules",
        "private DEMO WebSocket authentication",
        "production authenticated endpoint never used",
    ):
        assert must in names, must
    assert not any("forward runner" in n or "systemd" in n for n in names)
    # forward-host gate fails (no runner, no systemd) but does not block EXECUTION_SMOKE
    hok = {c["check"]: c["ok"] for c in host["checks"]}
    assert host["status"] == "FAILED" and host["required_for"] == ["STRATEGY_DEMO"]
    assert not hok["forward runner running on this host"] and not hok["exactly one forward runner"]
    assert (
        hok["forward freeze present and V5 hash unchanged"] and hok["observation start unchanged"]
    )
    assert pf.smoke_allowed(out) == (True, f"{pf.DEMO_GATE} PASSED")
    # GET only, demo host only, demo WS only; secrets never journaled
    assert (
        {r["method"] for r in fake.requests} == {"GET"}
        and fake.hosts() == {"api-demo.bybit.com"}
        and seen == ["wss://stream-demo.bybit.com/v5/private"]
        and out["authenticated_requests"] > 0
    )
    text = (tmp_path / "pre.jsonl").read_text()
    assert fake.api_key not in text and fake.api_secret not in text
    req = out["requirements"]
    assert req["source"] == "LIVE instrument rules" and req["min_notional"] == 5.0
    # STRATEGY_DEMO fails closed on the same machine
    with pytest.raises(RuntimeError, match=pf.HOST_GATE):
        pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, tmp_path / "none.pid")
    md = pf.write_preflight(out, tmp_path / "reports").read_text()
    assert "EXECUTION_SMOKE allowed" in md and "STRATEGY_DEMO NOT allowed (fails closed)" in md
    assert pf.smoke_allowed(pf.latest_preflight(tmp_path / "reports"))[0] is True


def test_demo_gate_fails_if_execution_mode_env_not_disabled_or_creds_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBybitDemo()
    _mac_today(monkeypatch, fake)
    monkeypatch.setenv("BYBIT_EXECUTION_MODE", "EXECUTION_SMOKE")
    out = _run(tmp_path / "a", fake, [])
    assert out["status"] == "BLOCKED_ENVIRONMENT" and out["authenticated_requests"] == 0
    monkeypatch.delenv("BYBIT_EXECUTION_MODE")
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET")
    out = _run(tmp_path / "b", fake, [])
    assert out["status"] == "BLOCKED_ENVIRONMENT" and not fake.requests
    assert not pf.smoke_allowed(out)[0]
    assert not pf.smoke_allowed({"status": "PASSED"})[0]  # older single-gate format refused
    assert not pf.smoke_allowed(None)[0]


def test_demo_gate_fails_if_a_production_host_is_contacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBybitDemo()
    _mac_today(monkeypatch, fake)
    j = HashChainJournal(tmp_path / "pre.jsonl", "pre")
    # a request record to a production host in the preflight journal must fail the gate
    j.append(
        "api_call",
        {"request": {"host": "api.bybit.com", "method": "GET", "path": "/v5/x", "auth": True}},
        pf.PREFLIGHT_TAG,
    )
    ro = pf.read_only_checks(
        load_demo_config(CFG),
        j,
        DemoCredentials(fake.api_key, fake.api_secret),
        fake.transport(),
        _connect({"op": "auth", "success": True}, []),
    )
    ok = {c["check"]: c["ok"] for c in ro["checks"]}
    assert ok["API key authentication (signed account info)"]
    assert ok["production authenticated endpoint never used"] is False
    # a WS endpoint outside the demo allowlist is rejected by the allowlist check itself
    bad = load_demo_config(CFG).model_copy(
        update={"ws_private": "wss://stream.bybit.com/v5/private"}
    )
    env = {c["check"]: c["ok"] for c in pf.demo_environment_checks(bad, lambda: (200, "{}"))}
    assert env["demo-only endpoint allowlist"] is False


def test_forward_host_gate_detects_changed_freeze_and_extra_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [111, 222])
    monkeypatch.setattr(pf, "_systemd_service_active", lambda: (True, "active"))
    pid = tmp_path / "run.pid"
    pid.write_text(str(os.getpid()))
    bad = {**FREEZE, "observation_start_ms": 1}
    ok = {c["check"]: c["ok"] for c in pf.forward_host_checks(bad, "x" * 64, pid)}
    assert ok["persistent host (not an ephemeral cloud-session container)"]
    assert ok["forward runner running on this host"] and ok["systemd deployment active"]
    assert (
        not ok["exactly one forward runner"]
        and not ok["observation start unchanged"]
        and not ok["forward freeze present and V5 hash unchanged"]
    )
    with pytest.raises(RuntimeError, match="exactly one forward runner"):
        pf.require_forward_host(bad, "x" * 64, pid)
    # authoritative host: one runner, systemd active, freeze unchanged -> passes
    monkeypatch.setattr(pf, "_runner_pids", lambda: [os.getpid()])
    checks = pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, pid)
    assert all(c["ok"] for c in checks)


def test_strategy_demo_executor_fails_closed_off_the_forward_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from btc_swing.v5.demo import runtime

    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [])
    monkeypatch.setattr(pf, "_systemd_service_active", lambda: (False, "systemd not running"))
    monkeypatch.setattr(runtime, "load_freeze", lambda: FREEZE)
    built: list[Any] = []
    monkeypatch.setattr(runtime, "BybitDemoClient", lambda *a, **k: built.append(a))
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(config_hash=pf.EXPECTED_V5_HASH),
        paths=SimpleNamespace(run_pid=tmp_path / "none.pid", root=tmp_path),
    )
    dcfg = load_demo_config(CFG, mode_override=ExecutionMode.STRATEGY_DEMO)
    with pytest.raises(RuntimeError, match=pf.HOST_GATE):
        runtime.build_executor(ctx, dcfg)  # type: ignore[arg-type]
    assert built == []  # no client (and so no authenticated request) was created


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
