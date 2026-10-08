from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from btc_swing.v5.demo.client import (
    BybitDemoClient,
    DemoApiError,
    DemoTransportError,
    ExecutionDisabledError,
    fmt_price,
    fmt_qty,
    sign,
)
from btc_swing.v5.demo.config import (
    SMOKE_TAG,
    STRATEGY_TAG,
    EndpointNotAllowedError,
    ExecutionMode,
    assert_demo_url,
    load_demo_config,
)
from btc_swing.v5.demo.credentials import (
    DemoCredentials,
    DemoCredentialsMissingError,
    load_demo_credentials,
)
from btc_swing.v5.demo.ids import is_smoke_link_id, smoke_link_id, strategy_link_id
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v5.demo.smoke import render_smoke, run_execution_smoke
from tests.v5.fake_bybit import FakeBybitDemo

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "config" / "btc_swing_v5_demo.yaml"


def _ws_ok() -> dict[str, object]:
    return {"ok": True, "conn_id": "fake"}


def _client(
    tmp: Path,
    fake: FakeBybitDemo,
    mode: ExecutionMode = ExecutionMode.EXECUTION_SMOKE,
    tag: str = SMOKE_TAG,
) -> BybitDemoClient:
    cfg = load_demo_config(CFG_PATH, mode)
    j = HashChainJournal(tmp / "journal.jsonl", "smoke")
    return BybitDemoClient(
        cfg, cfg.mode, j, tag, DemoCredentials(fake.api_key, fake.api_secret), fake.transport()
    )


def test_config_defaults_disabled_and_demo_host_only() -> None:
    cfg = load_demo_config(CFG_PATH)
    assert cfg.mode is ExecutionMode.DISABLED and cfg.rest_base == "https://api-demo.bybit.com"
    for bad in (
        "https://api.bybit.com",
        "https://api.bytick.com",
        "https://api-testnet.bybit.com",
        "http://api-demo.bybit.com",
        "https://api-demo.bybit.com.evil.io",
        "https://api-demo.bybit.com:8443",
    ):
        with pytest.raises(EndpointNotAllowedError):
            assert_demo_url(bad)
    raw = CFG_PATH.read_text().replace("https://api-demo.bybit.com", "https://api.bybit.com")
    tmp = ROOT / "data" / "_tmp_bad_demo.yaml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(raw)
    try:
        with pytest.raises(Exception, match=r"only https://api-demo\.bybit\.com"):
            load_demo_config(tmp)
    finally:
        tmp.unlink()


def test_disabled_mode_makes_no_request(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    cfg = load_demo_config(CFG_PATH)
    with pytest.raises(ExecutionDisabledError):
        BybitDemoClient(
            cfg,
            cfg.mode,
            HashChainJournal(tmp_path / "j.jsonl", "x"),
            SMOKE_TAG,
            DemoCredentials("k", "s"),
            fake.transport(),
        )
    assert fake.requests == []


def test_credentials_fail_closed_and_never_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_demo_config(CFG_PATH, ExecutionMode.EXECUTION_SMOKE)
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s3cr3t-value")
    with pytest.raises(DemoCredentialsMissingError, match="BYBIT_DEMO_API_KEY") as ei:
        load_demo_credentials(cfg)
    assert "s3cr3t-value" not in str(ei.value)
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k3y-value")
    c = load_demo_credentials(cfg)
    assert "k3y-value" not in repr(c) and "s3cr3t-value" not in str(c)


def test_signature_matches_bybit_v5_scheme() -> None:
    # HMAC_SHA256(secret, ts + key + recv_window + payload)
    assert (
        sign("secret", 1700000000000, "key", 5000, "category=linear")
        == __import__("hmac")
        .new(b"secret", b"1700000000000key5000category=linear", "sha256")
        .hexdigest()
    )


def test_ids_and_formatting() -> None:
    a, b = (
        strategy_link_id("FLOW_OI_CONTINUATION|LONG|1791400000000", "EN"),
        strategy_link_id("FLOW_OI_CONTINUATION|LONG|1791400000000", "EN"),
    )
    assert a == b and len(a) <= 36 and a.startswith("V5D-") and not is_smoke_link_id(a)
    assert strategy_link_id("X|LONG|1", "EN") != strategy_link_id("X|LONG|2", "EN")
    assert is_smoke_link_id(smoke_link_id("261008190000", "MKT"))
    assert fmt_qty(0.00199, 0.001) == "0.001" and fmt_qty(0.0005, 0.001) == "0.000"
    assert fmt_price(60000.04, 0.1, -1) == "60000.0" and fmt_price(60000.01, 0.1, 1) == "60000.1"


def test_smoke_passes_on_fake_demo_and_never_leaves_demo_host(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    cl = _client(tmp_path, fake)
    res = run_execution_smoke(cl, cl.cfg, sleep=lambda s: None, ws_auth=_ws_ok)
    assert res.status == "PASSED", res.steps[-1]
    assert [s["step"] for s in res.steps][-1] == "recovery" and all(s["ok"] for s in res.steps)
    rec = res.steps[-1]["detail"]
    assert rec["duplicate_order_link_id_rejected"] and rec["position_flat"]
    assert all(v["match"] for v in rec["journal_vs_executions"].values())
    assert fake.hosts() == {"api-demo.bybit.com"}
    assert fake.position["size"] == 0 and not [
        o for o in fake.orders.values() if o["orderStatus"] in ("New", "PartiallyFilled")
    ]
    assert all(is_smoke_link_id(lk) for lk in res.orders_sent)
    chain = verify_chain(tmp_path / "journal.jsonl")
    assert chain["ok"] and chain["records"] > 10
    text = (tmp_path / "journal.jsonl").read_text()
    assert fake.api_key not in text and fake.api_secret not in text and "X-BAPI-SIGN" not in text
    assert all(
        r["tag"] == SMOKE_TAG
        for r in HashChainJournal(tmp_path / "journal.jsonl", "smoke").records()
    )
    md = render_smoke(res.as_dict() | {"journal": "j", "journal_chain": chain})
    for label in (
        "DEMO AUTH",
        "DEMO ACCOUNT CONFIRMED",
        "WALLET",
        "INSTRUMENT RULES",
        "PRIVATE WS",
        "LIMIT CREATE/CANCEL",
        "MARKET FILL",
        "STOP",
        "TAKE PROFIT",
        "CLOSE POSITION",
        "FEES/FILLS RETRIEVED",
        "RECOVERY/RECONCILIATION",
    ):
        assert f"| {label} | PASS |" in md, label


def test_smoke_blocked_by_geo_restriction_sends_no_authenticated_request(tmp_path: Path) -> None:
    fake = FakeBybitDemo(geo_blocked=True)
    cl = _client(tmp_path, fake)
    res = run_execution_smoke(cl, cl.cfg, sleep=lambda s: None, ws_auth=_ws_ok)
    assert res.status == "BLOCKED" and res.steps[0]["geo_blocked"] and len(res.steps) == 1
    assert all(
        "X-BAPI-SIGN" not in {k.upper(): v for k, v in r["headers"].items()}
        and "x-bapi-sign" not in r["headers"]
        for r in fake.requests
    )
    assert res.orders_sent == []


def test_smoke_aborts_untouched_on_foreign_position(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    fake.position.update({"side": "Buy", "size": 0.01, "avg": 59000.0})
    cl = _client(tmp_path, fake)
    res = run_execution_smoke(cl, cl.cfg, sleep=lambda s: None, ws_auth=_ws_ok)
    assert res.status == "FAILED" and res.steps[-1]["step"] == "position_before"
    assert fake.position["size"] == 0.01 and res.cleanup == [] and not fake.orders


def test_smoke_failure_mid_run_cleans_up_position(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    fake.fail_next["/v5/position/trading-stop"] = 10001
    cl = _client(tmp_path, fake)
    res = run_execution_smoke(cl, cl.cfg, sleep=lambda s: None, ws_auth=_ws_ok)
    assert res.status == "FAILED" and res.steps[-1]["step"] == "attach_stop"
    assert fake.position["size"] == 0 and any(c["action"] == "close" for c in res.cleanup)


def test_api_error_and_bad_signature_raise(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    cfg = load_demo_config(CFG_PATH, ExecutionMode.EXECUTION_SMOKE)
    cl = BybitDemoClient(
        cfg,
        cfg.mode,
        HashChainJournal(tmp_path / "j.jsonl", "x"),
        SMOKE_TAG,
        DemoCredentials(fake.api_key, "wrong-secret"),
        fake.transport(),
    )
    with pytest.raises(DemoApiError) as ei:
        cl.wallet_balance()
    assert ei.value.ret_code == 10004
    fake2 = FakeBybitDemo()
    cl2 = _client(tmp_path / "b", fake2)
    fake2.drop_ack_next_create = True
    with pytest.raises(DemoTransportError):
        cl2.create_order("Buy", "0.001", "Market", "SMOKE-x-1")
    assert (
        fake2.orders["SMOKE-x-1"]["orderStatus"] == "Filled"
    )  # processed although the ack was lost


def test_strategy_journal_refuses_smoke_records(tmp_path: Path) -> None:
    j = HashChainJournal(tmp_path / "strategy.jsonl", "strategy", forbid_tags=(SMOKE_TAG,))
    j.append("decision", {"x": 1}, STRATEGY_TAG)
    with pytest.raises(ValueError):
        j.append("api", {"x": 2}, SMOKE_TAG)
    assert verify_chain(tmp_path / "strategy.jsonl")["ok"]
    # tampering breaks the chain
    lines = (tmp_path / "strategy.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["data"]["x"] = 99
    (tmp_path / "strategy.jsonl").write_text(json.dumps(rec) + "\n")
    assert not verify_chain(tmp_path / "strategy.jsonl")["ok"]


def test_no_network_to_real_bybit_in_tests() -> None:
    # sanity: the fake transport answers in-process; httpx would need a transport to reach any host
    fake = FakeBybitDemo()
    r = httpx.Client(transport=fake.transport()).get("https://api-demo.bybit.com/v5/market/time")
    assert r.status_code == 200 and fake.hosts() == {"api-demo.bybit.com"}
