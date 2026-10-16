"""V11-LITE-WARM Bybit DEMO execution layer. Offline only: an in-process fake of the Bybit DEMO REST API
(httpx.MockTransport) behind the REAL demo-only client; synthetic shadow replays through the REAL live
`_step`. Proves: the shadow's decisions / journals are identical with and without the Demo observer and
whatever the Demo does (blocks, fails, mutates its copies); V10 positions / orders, the 24 h cap, the daily
stop, the 3-loss halt and reconciliation block Demo entries only; LITE never touches a V10 order; no
production endpoint exists; no secret reaches any file."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from btc_swing.v5.demo.client import BybitDemoClient, ExecutionDisabledError, fmt_qty
from btc_swing.v5.demo.config import ExecutionMode, load_demo_config
from btc_swing.v5.demo.credentials import DemoCredentials
from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v11.engine import MS_1M
from btc_swing.v11_lite_warm.config import lite_strategy_hash, load_v11lw_config
from btc_swing.v11_lite_warm.demo.activation import (
    RECON_ACK,
    RESUMED,
    demo_dir,
    demo_journal,
    effective_activation,
    record_activation,
    record_deactivation,
    record_owner_event,
)
from btc_swing.v11_lite_warm.demo.config import (
    LiteDemoConfig,
    exec_config,
    load_lite_demo_config,
)
from btc_swing.v11_lite_warm.demo.conflict import (
    EXCHANGE_NOT_FLAT,
    FOREIGN_ORDERS,
    UNPROVEN,
    V10_ACTIVE,
    V10_RECON,
    conflict_blockers,
)
from btc_swing.v11_lite_warm.demo.executor import BarCtx, LiteDemoExecutor, lite_link_id
from btc_swing.v11_lite_warm.demo.freeze import check_demo_freeze, write_demo_freeze
from btc_swing.v11_lite_warm.demo.gates import demo_gates
from btc_swing.v11_lite_warm.demo.report import demo_report, render_demo_report
from btc_swing.v11_lite_warm.demo.worker import DemoSupervisor, DemoWorker, snapshot_item
from btc_swing.v11_lite_warm.live import V11LiteWarmLive
from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths
from btc_swing.v11_warm.bootstrap import write_bootstrap_bars
from tests.v5.fake_bybit import FakeBybitDemo
from tests.v11_lite_warm.test_v11_lite_warm import (
    CFG,
    ROOT,
    _frame_and_scores,
    _protected_hashes,
)
from tests.v11_warm.test_v11_warm import _bars_df, synth_rows

DEMO_CFG = ROOT / "config" / "btc_swing_v11_lite_warm_demo.yaml"
LITE_STRATEGY_HASH = "c66ef46a493faa73ca589ad51732cefe11cb4505f50b2c5042e6978a2eff5a0b"
SHADOW_JOURNALS = (
    "candidates",
    "episodes",
    "signals",
    "shadow_trades",
    "control_twin",
    "nearmiss",
    "outcomes",
)
KEY, SECRET = "lite-demo-key-XYZ987", "lite-demo-secret-ABC654"


# ----------------------------------------------------------------------------- helpers
class FakeAccount(FakeBybitDemo):
    """The shared fake plus the Bybit user id of the key (isolation proof)."""

    def __init__(self, user_id: int = 1001, **kw: Any) -> None:
        super().__init__(api_key=KEY, api_secret=SECRET, **kw)
        self.user_id = user_id

    def handle(self, req: Any) -> Any:
        r = super().handle(req)
        if req.url.path == "/v5/user/query-api" and r.status_code == 200:
            d = json.loads(r.content)
            d["result"]["userID"] = self.user_id
            return self._ok(d["result"])
        return r

    def posts(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]


def _lc(mode: str = "isolated") -> LiteDemoConfig:
    lc = load_lite_demo_config(DEMO_CFG)
    return lc.model_copy(update={"account_mode": mode})


def _executor(
    root: Path, fake: FakeAccount, lc: LiteDemoConfig, activated_at_ms: int, v10_root: Path
) -> LiteDemoExecutor:
    _, v11 = load_v11lw_config(CFG)
    dcfg = exec_config(lc, ExecutionMode.STRATEGY_DEMO)
    journal = demo_journal(root)
    client = BybitDemoClient(
        dcfg,
        ExecutionMode.STRATEGY_DEMO,
        journal,
        "STRATEGY_DEMO_V11LW",
        DemoCredentials(fake.api_key, fake.api_secret),
        transport=fake.transport(),
        clock=lambda: fake.now_ms / 1000,
    )
    return LiteDemoExecutor(
        lc, v11, client, journal, demo_dir(root), v10_root, activated_at_ms, sleep=lambda s: None
    )


def _bar(t_close: int, price: float, i: int = 99) -> BarCtx:
    n = 64
    return BarCtx(
        i=i,
        j=n - 1,
        t_close=t_close,
        x={
            "high": np.full(n, price * 1.0002),
            "low": np.full(n, price * 0.9998),
            "close": np.full(n, price),
            "atr5": np.full(n, price * 0.002),
        },
        score_long=80.0,
        score_short=20.0,
    )


def _rec(sid: str, decision_ms: int, side: int = 1, ref: float = 60000.0) -> dict[str, Any]:
    d = 300.0
    return {
        "signal_id": sid,
        "action": "EXECUTED",
        "family": "A",
        "side": "LONG" if side > 0 else "SHORT",
        "side_sign": side,
        "score": 80.0,
        "entry_ref": ref,
        "stop": ref - side * d,
        "tp1": ref + side * 0.6 * d,
        "tp2": ref + side * 1.2 * d,
        "decision_ms": decision_ms,
    }


def _kinds(root: Path) -> list[str]:
    return [r["kind"] for r in demo_journal(root).records()]


def _v10_state(v10_root: Path, **st: Any) -> None:
    (v10_root / "demo").mkdir(parents=True, exist_ok=True)
    (v10_root / "demo" / "strategy_state.json").write_text(
        json.dumps({"position": None, "reconcile_required": False} | st)
    )
    (v10_root / "demo" / "strategy_journal.jsonl").touch()


# ----------------------------------------------------------------------------- config / manifest
def test_demo_config_is_the_owner_spec_and_never_changes_the_lite_strategy(tmp_path: Path) -> None:
    lc, v11 = load_v11lw_config(CFG)
    assert lite_strategy_hash(v11, lc) == LITE_STRATEGY_HASH
    dc = _lc()
    assert dc.account_mode == "isolated" and not dc.shares_v10_account
    assert dc.risk.risk_usdt == pytest.approx(5.0) and dc.risk.leverage == 1
    assert dc.safety.max_trades_rolling_24h == 10 and dc.safety.daily_stop_usdt == 25.0
    assert dc.safety.max_consecutive_full_losses == 3
    assert dc.order_prefix == "V11LWD-" and dc.v10_order_prefix == "V10D-"
    assert dc.creds.api_key == "BYBIT_DEMO_V11LW_API_KEY"
    assert _lc("shared").creds.api_key == "BYBIT_DEMO_API_KEY"
    raw = json.loads(dc.model_dump_json())
    for bad in (
        {"risk": raw["risk"] | {"risk_per_trade": 0.0015}},
        {"risk": raw["risk"] | {"leverage": 3}},
        {"safety": raw["safety"] | {"max_trades_rolling_24h": 11}},
        {"order_prefix": "V10D-"},
        {"credentials_env": raw["credentials_env"] | {"isolated": raw["v10_credentials_env"]}},
    ):
        with pytest.raises(ValueError):
            LiteDemoConfig.model_validate(raw | bad)
    base = load_demo_config(dc.demo_exec_config, ExecutionMode.DISABLED)
    assert base.mode.value == "DISABLED"
    rec = write_demo_freeze(lc, v11, dc, tmp_path / "m.json")
    assert rec["lite_strategy_hash"] == LITE_STRATEGY_HASH
    assert rec["v11_strategy_hash"] == v11.strategy_hash
    assert check_demo_freeze(lc, v11, dc, tmp_path / "m.json")["demo_config_hash"] == dc.config_hash
    with pytest.raises(RuntimeError):
        check_demo_freeze(lc, v11, _lc("shared"), tmp_path / "m.json")


# ----------------------------------------------------------------------------- the full Demo trade
def test_entry_protection_tp1_breakeven_and_close_accounting(tmp_path: Path) -> None:
    fake = FakeAccount()
    root = tmp_path / "btc" / "forward_v11_lite_warm"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "btc" / "forward_v10")
    ex.recover()
    t0 = fake.now_ms
    out = ex.on_shadow_decision(_rec("V11LWS-1", t0 - 1_000), _bar(t0, 60000.0))
    assert out["action"] == "ENTERED"
    assert fake.position["size"] == pytest.approx(0.016)  # floor(5 / 300, step 0.001)
    assert fake.position["sl"] == pytest.approx(59700.0)
    tps = {o["orderLinkId"]: o for o in fake.orders.values() if o["orderType"] == "Limit"}
    assert set(tps) == {lite_link_id("V11LWD-", "V11LWS-1", r) for r in ("T1", "T2")}
    assert all(o["reduceOnly"] and o["qty"] == pytest.approx(0.006) for o in tps.values())
    fake.move_price(60200.0)  # TP1 fills
    ex.manage(fake.now_ms, _bar(fake.now_ms, 60200.0, i=100))
    assert ex.state["position"]["tp1_done"]
    assert fake.position["sl"] == pytest.approx(60084.0)  # frozen V11 BE: entry_ref + 14 bps
    fake.move_price(60080.0)  # the exchange stop closes the rest (linkless execution)
    res = ex.manage(fake.now_ms, _bar(fake.now_ms, 60080.0, i=101))
    assert res["action"] == "closed"
    t = res["trade"]
    assert t["exit_reason"] == "TP1_THEN_STOP" and t["qty"] == pytest.approx(0.016)
    assert {x["kind"] for x in t["exits"]} == {"T1", "EXCHANGE_STOP"}
    assert t["gross_pnl"] == pytest.approx(0.006 * 180 + 0.010 * 80, abs=1e-6)
    assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["fees"])
    assert ex.state["position"] is None and ex.state["closed_trades"] == 1
    k = _kinds(root)
    for kind in ("ENTRY_SUBMIT", "ENTRY_ACKED", "EXECUTION_CONFIRMED", "ENTRY_FILLED", "STOP_SET",
                 "TP_PLACED", "TP_FILLED", "STOP_MOVED", "TRADE_CLOSED"):  # fmt: skip
        assert kind in k, kind
    assert all(
        str(p["body"].get("orderLinkId", "V11LWD-")).startswith("V11LWD-") for p in fake.posts()
    )
    assert verify_chain(root / "demo" / "strategy_journal.jsonl")["ok"]
    rep = demo_report(root)
    assert rep["performance"]["closed_trades"] == 1 and rep["events"]["ENTRY_FILLED"] == 1
    assert "Shadow vs Demo" in render_demo_report(rep)


def test_time_exit_on_the_frozen_clock_and_runner_trail(tmp_path: Path) -> None:
    fake = FakeAccount()
    root = tmp_path / "lite"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    t0 = fake.now_ms
    ex.on_shadow_decision(_rec("V11LWS-T", t0 - 1_000), _bar(t0, 60000.0))
    dec = t0 - 1_000
    ex.manage(dec + 59 * MS_1M, _bar(dec + 59 * MS_1M, 60010.0, i=150))
    assert ex.state["position"] is not None
    res = ex.manage(dec + 60 * MS_1M, _bar(dec + 60 * MS_1M, 60010.0, i=151))
    assert res["action"] == "closed" and res["trade"]["exit_reason"] == "TIME"
    assert "TIME_CLOSE" in _kinds(root)


# ----------------------------------------------------------------------------- test 3 / 4: V10 blocks Demo
def test_v10_position_blocks_lite_demo_entry_in_shared_mode(tmp_path: Path) -> None:
    fake = FakeAccount()
    v10 = tmp_path / "forward_v10"
    _v10_state(v10, position={"status": "ACTIVE", "signal_id": "V10S-1"})
    ex = _executor(tmp_path / "lite", fake, _lc("shared"), fake.now_ms - 60_000, v10)
    out = ex.on_shadow_decision(_rec("V11LWS-2", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "BLOCKED" and V10_ACTIVE in out["blockers"]
    assert not fake.posts()  # nothing sent to the exchange


def test_v10_open_orders_or_reconciliation_block_lite_demo_entry(tmp_path: Path) -> None:
    fake = FakeAccount()
    v10 = tmp_path / "forward_v10"
    _v10_state(v10)
    fake.orders["V10D-abc-T1"] = {
        "orderId": "o-v10", "orderLinkId": "V10D-abc-T1", "side": "Sell", "orderType": "Limit",
        "qty": 0.01, "price": 61000.0, "orderStatus": "New", "cumExecQty": 0.0, "avgPrice": 0.0,
        "cumExecFee": 0.0, "reduceOnly": True, "timeInForce": "GTC", "createdTime": fake.now_ms,
    }  # fmt: skip
    ex = _executor(tmp_path / "lite", fake, _lc("shared"), fake.now_ms - 60_000, v10)
    out = ex.on_shadow_decision(_rec("V11LWS-3", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "BLOCKED" and V10_ACTIVE in out["blockers"]
    assert not fake.posts() and fake.orders["V10D-abc-T1"]["orderStatus"] == "New"
    _v10_state(v10, reconcile_required=True)
    b, _ = conflict_blockers(
        shared=True, v10_root=v10, exchange_position={"size": 0.0}, open_orders=[],
        own_prefix="V11LWD-", v10_prefix="V10D-", own_position_open=False,
    )  # fmt: skip
    assert b == [V10_RECON]
    b, _ = conflict_blockers(
        shared=True, v10_root=v10, exchange_position={"size": 0.02}, open_orders=[{"orderLinkId": "x"}],
        own_prefix="V11LWD-", v10_prefix="V10D-", own_position_open=False,
    )  # fmt: skip
    assert set(b) == {V10_RECON, UNPROVEN, FOREIGN_ORDERS}  # unattributable position / order
    b, _ = conflict_blockers(
        shared=False, v10_root=v10, exchange_position={"size": 0.02}, open_orders=[],
        own_prefix="V11LWD-", v10_prefix="V10D-", own_position_open=False,
    )  # fmt: skip
    assert b == [EXCHANGE_NOT_FLAT]


# ----------------------------------------------------------------------------- test 5 / 6: caps
def test_rolling_24h_cap_and_daily_stop_block_demo_entries(tmp_path: Path) -> None:
    fake = FakeAccount()
    ex = _executor(tmp_path / "lite", fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    ex.state["executed_ms"] = [fake.now_ms - 3_600_000] * 10
    out = ex.on_shadow_decision(_rec("V11LWS-4", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "BLOCKED" and "DEMO_CAP_24H" in out["blockers"]
    ex.state["executed_ms"] = [fake.now_ms - 86_400_001] * 10  # outside the rolling window
    ex.state["day_realised_usd"] = -25.0
    ex._roll(fake.now_ms)
    ex.state["day_realised_usd"] = -25.0
    out = ex.on_shadow_decision(_rec("V11LWS-5", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["blockers"] == ["DAILY_STOP"]
    assert not fake.posts()
    ex.state["day_realised_usd"] = -24.0
    out = ex.on_shadow_decision(_rec("V11LWS-6", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "ENTERED"
    late = ex.safety_blockers(fake.now_ms)
    assert "DEMO_POSITION_OPEN" in late  # one LITE Demo position at most: no pyramiding


def test_three_full_losses_halt_until_the_owner_resumes(tmp_path: Path) -> None:
    fake = FakeAccount()
    root = tmp_path / "lite"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    for k in range(3):
        out = ex.on_shadow_decision(
            _rec(f"V11LWS-L{k}", fake.now_ms - 1_000), _bar(fake.now_ms, 60000.0)
        )
        assert out["action"] == "ENTERED", out
        fake.move_price(59690.0)  # exchange stop
        res = ex.manage(fake.now_ms, _bar(fake.now_ms, 59690.0))
        assert res["action"] == "closed" and res["trade"]["R_net_vs_planned"] <= -0.8
        fake.move_price(60000.0)
    assert ex.state["halted"] and "DEMO_HALTED" in _kinds(root)
    out = ex.on_shadow_decision(_rec("V11LWS-L3", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "BLOCKED" and out["blockers"] == ["HALTED:3_CONSECUTIVE_FULL_LOSSES"]
    record_owner_event(root, RESUMED, "reviewed")
    out = ex.on_shadow_decision(_rec("V11LWS-L4", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert out["action"] == "ENTERED" and ex.state["consecutive_full_losses"] == 0


# ----------------------------------------------------------------------------- test 7: reconciliation
def test_size_mismatch_requires_reconciliation_and_blocks_new_entries(tmp_path: Path) -> None:
    fake = FakeAccount()
    root = tmp_path / "lite"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    ex.on_shadow_decision(_rec("V11LWS-7", fake.now_ms - 1_000), _bar(fake.now_ms, 60000.0))
    record_owner_event(root, RECON_ACK, "an acknowledgement BEFORE the mismatch clears nothing")
    fake.position["size"] += 0.005  # something else changed the exchange position
    n_posts = len(fake.posts())
    res = ex.manage(fake.now_ms, _bar(fake.now_ms, 60000.0))
    assert res["action"] == "reconcile_required" and ex.state["reconcile_required"]
    ex.manage(fake.now_ms + 1_000, _bar(fake.now_ms + 1_000, 60000.0))
    assert len(fake.posts()) == n_posts  # an unproven position is never touched
    assert ex.state["reconcile_required"]
    out = ex.on_shadow_decision(_rec("V11LWS-8", fake.now_ms - 1_000), _bar(fake.now_ms, 6e4))
    assert "RECONCILIATION_REQUIRED" in out["blockers"] and len(fake.posts()) == n_posts
    record_owner_event(root, RECON_ACK, "checked")
    assert ex.apply_owner_events() == [RECON_ACK] and not ex.state["reconcile_required"]
    ex.manage(fake.now_ms, _bar(fake.now_ms, 60000.0))
    assert ex.state["reconcile_required"]  # still mismatched: detected again


def test_restart_with_unknown_exchange_position_requires_reconciliation(tmp_path: Path) -> None:
    fake = FakeAccount()
    fake.position.update({"side": "Buy", "size": 0.01, "avg": 60000.0})
    ex = _executor(tmp_path / "lite", fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    ex.recover()
    assert ex.state["reconcile_required"]
    assert not fake.posts()


# ----------------------------------------------------------------------------- test 8: never V10's
def test_lite_never_cancels_or_accounts_v10_orders_and_executions(tmp_path: Path) -> None:
    fake = FakeAccount()
    root = tmp_path / "lite"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    t0 = fake.now_ms
    ex.on_shadow_decision(_rec("V11LWS-9", t0 - 1_000), _bar(t0, 60000.0))
    fake.orders["V10D-zzz-T1"] = {
        "orderId": "o-v10", "orderLinkId": "V10D-zzz-T1", "side": "Sell", "orderType": "Limit",
        "qty": 0.01, "price": 99000.0, "orderStatus": "New", "cumExecQty": 0.0, "avgPrice": 0.0,
        "cumExecFee": 0.0, "reduceOnly": False, "timeInForce": "GTC", "createdTime": t0,
    }  # fmt: skip
    fake.executions.append({
        "execId": "x-v10", "orderLinkId": "V10D-zzz-EN", "orderId": "o-v10e", "execPrice": "1",
        "execQty": "0.5", "execFee": "9", "execTime": str(t0 + 5), "execType": "Trade", "side": "Sell",
        "_hidden": 0,
    })  # fmt: skip
    res = ex.manage(t0 - 1_000 + 60 * MS_1M, _bar(t0 - 1_000 + 60 * MS_1M, 60000.0))
    assert res["action"] == "closed"
    assert fake.orders["V10D-zzz-T1"]["orderStatus"] == "New"
    for p in fake.posts():
        link = p["body"].get("orderLinkId")
        assert link is None or str(link).startswith("V11LWD-"), p
    assert res["trade"]["fees"] < 2 and abs(res["trade"]["gross_pnl"]) < 1  # V10's fee 9 not taken
    src = (ROOT / "btc_swing" / "v11_lite_warm" / "demo" / "executor.py").read_text()
    assert "cancel-all" not in src and "cancel_all" not in src and "V10D-" not in src
    assert "btc_swing.v10" not in "".join(
        p.read_text() for p in (ROOT / "btc_swing" / "v11_lite_warm" / "demo").glob("*.py")
    )


# ----------------------------------------------------------------------------- test 9: demo host only
def test_no_real_money_endpoint_or_path_exists(tmp_path: Path) -> None:
    dc = _lc()
    for mode in (ExecutionMode.DISABLED, ExecutionMode.STRATEGY_DEMO):
        assert exec_config(dc, mode).rest_base == "https://api-demo.bybit.com"
    bad = tmp_path / "prod.yaml"
    bad.write_text(
        (ROOT / "config" / "btc_swing_v5_demo.yaml")
        .read_text()
        .replace("https://api-demo.bybit.com", "https://api.bybit.com")
    )
    with pytest.raises(Exception, match=r"(?i)demo|not allowed"):
        load_demo_config(bad, ExecutionMode.STRATEGY_DEMO)
    raw = json.loads(dc.model_dump_json()) | {"demo_exec_config": str(bad)}
    with pytest.raises(Exception, match=r"(?i)demo|not allowed"):
        exec_config(LiteDemoConfig.model_validate(raw), ExecutionMode.STRATEGY_DEMO)
    fake = FakeAccount()
    with pytest.raises(ExecutionDisabledError):
        BybitDemoClient(
            exec_config(dc, ExecutionMode.DISABLED), ExecutionMode.DISABLED,
            demo_journal(tmp_path), "X", DemoCredentials(KEY, SECRET), transport=fake.transport(),
        )  # fmt: skip
    ex = _executor(tmp_path / "lite", fake, dc, fake.now_ms - 60_000, tmp_path / "v10")
    ex.on_shadow_decision(_rec("V11LWS-10", fake.now_ms - 1_000), _bar(fake.now_ms, 60000.0))
    assert fake.hosts() == {"api-demo.bybit.com"}
    src = (
        "".join(p.read_text() for p in (ROOT / "btc_swing" / "v11_lite_warm").rglob("*.py"))
        + DEMO_CFG.read_text()
    )
    for tok in ("api.bybit.com", "api.bytick.com", "stream.bybit.com/v5/private", "mainnet"):
        assert tok not in src, tok


# ----------------------------------------------------------------------------- test 10: no secret
def test_no_secret_reaches_any_file_or_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BYBIT_DEMO_V11LW_API_KEY", KEY)
    monkeypatch.setenv("BYBIT_DEMO_V11LW_API_SECRET", SECRET)
    fake = FakeAccount()
    root = tmp_path / "lite"
    ex = _executor(root, fake, _lc(), fake.now_ms - 60_000, tmp_path / "v10")
    fake.drop_ack_next_create = True  # an error path journals the scrubbed request too
    caplog.set_level("DEBUG")
    ex.on_shadow_decision(_rec("V11LWS-11", fake.now_ms - 1_000), _bar(fake.now_ms, 60000.0))
    fake.move_price(59600.0)
    ex.manage(fake.now_ms, _bar(fake.now_ms, 59600.0))
    demo_report(root)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files
    for p in files:
        body = p.read_bytes()
        assert KEY.encode() not in body and SECRET.encode() not in body, p
    assert KEY not in caplog.text and SECRET not in caplog.text
    assert KEY not in repr(ex.client.creds) and SECRET not in str(ex.client.creds)


# ----------------------------------------------------------------------------- tests 1 / 2: the shadow is untouched
@pytest.fixture(scope="module")
def replay() -> tuple[Any, Any, Any, int]:
    rows = synth_rows(25, seed=11)
    fr, sc = _frame_and_scores(rows)
    return rows, fr, sc, 22 * 1440


def _shadow_run(
    tmp: Path, replay: tuple[Any, Any, Any, int], sink: Any = None, n_steps: int = 1_550
) -> tuple[dict[str, list[tuple[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    rows, fr, sc, n_hist = replay
    lc, v11 = load_v11lw_config(CFG)
    paths = V11LiteWarmPaths(tmp, "BTCUSDT", verification=True)
    paths.ensure()
    write_bootstrap_bars(_bars_df(rows[:n_hist]), paths.bars_1m)
    obs = int(rows["open_time_ms"][n_hist])
    live = V11LiteWarmLive(
        v11, paths, obs, lc.warm.model_copy(update={"live_warmup_min": 0}), lc.lite
    )
    live.demo_sink = sink
    recs = []
    for i in range(n_hist, n_hist + n_steps):
        r = live._step(fr, sc, i)
        if r is not None and r["action"] == "EXECUTED":
            recs.append(json.loads(json.dumps(r, default=str)))
    out: dict[str, list[tuple[str, Any]]] = {}
    for n in SHADOW_JOURNALS:
        p = getattr(paths, n)
        out[n] = (
            [(json.loads(x)["kind"], json.loads(x)["data"]) for x in p.read_text().splitlines()]
            if p.exists()
            else []
        )
        if p.exists():
            assert verify_chain(p)["ok"], n
    st = {k: live.state[k] for k in ("counters", "trades", "cum_net_usd", "wins", "losses")}
    return out, st, recs


@pytest.fixture(scope="module")
def baseline(
    replay: tuple[Any, Any, Any, int], tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, list[tuple[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    return _shadow_run(tmp_path_factory.mktemp("base"), replay)


def _attached_sink(ex: LiteDemoExecutor, fake: FakeAccount) -> Any:
    w = DemoWorker(ex)

    def sink(cols: Any, i: int, j: int, t_close: int, sl: float, ss: float, rec: Any) -> None:
        item = snapshot_item(cols, i, j, t_close, sl, ss, rec)
        fake.now_ms = t_close + 500
        fake.move_price(float(cols["close"][j]), advance_ms=0)
        w.process(item)  # synchronous here (deterministic); a thread in the runner

    return sink


@pytest.mark.parametrize(
    "case",
    [
        "isolated_executes",
        "v10_position",
        "v10_orders",
        "cap",
        "daily_stop",
        "reconcile",
        "hostile",
    ],
)
def test_shadow_decisions_and_journals_are_identical_whatever_the_demo_does(
    case: str,
    tmp_path: Path,
    replay: tuple[Any, Any, Any, int],
    baseline: tuple[dict[str, list[tuple[str, Any]]], dict[str, Any], list[dict[str, Any]]],
) -> None:
    before = _protected_hashes()
    base_j, base_st, base_recs = baseline
    assert len(base_recs) >= 3, "the replay must contain shadow EXECUTED decisions"
    _rows, fr, _sc, n_hist = replay
    lite_root = tmp_path / "btc" / "forward_v11_lite_warm"
    v10 = tmp_path / "btc" / "forward_v10"
    fake = FakeAccount(price=float(fr.c["close"][n_hist]))
    obs = int(fr.t_open[n_hist])
    mode = "shared" if case in ("v10_position", "v10_orders") else "isolated"
    sink: Any
    if case == "hostile":

        def sink(cols: Any, i: int, j: int, t_close: int, sl: float, ss: float, rec: Any) -> None:
            cols["close"][j] = 0.0  # an observer that corrupts what it is handed ...
            if rec is not None:
                rec["action"], rec["stop"] = "BLOCKED", -1.0
                rec.setdefault("context", {}).clear()
            raise RuntimeError("... and then fails")

        got_j, got_st, got_recs = _shadow_run(tmp_path / "shadow", replay, sink)
        assert got_j == base_j and got_st == base_st and got_recs == base_recs
        assert float(fr.c["close"][n_hist + 10]) != 0.0
        return
    ex = _executor(lite_root, fake, _lc(mode), obs, v10)
    if case == "v10_position":
        _v10_state(v10, position={"status": "ACTIVE", "signal_id": "V10S-9"})
    elif case == "v10_orders":
        _v10_state(v10)
        fake.orders["V10D-q-T2"] = {
            "orderId": "o9", "orderLinkId": "V10D-q-T2", "side": "Sell", "orderType": "Limit",
            "qty": 0.01, "price": 1e9, "orderStatus": "New", "cumExecQty": 0.0, "avgPrice": 0.0,
            "cumExecFee": 0.0, "reduceOnly": False, "timeInForce": "GTC", "createdTime": 0,
        }  # fmt: skip
    elif case == "cap":
        ex.state["executed_ms"] = [int(fr.t_open[-1]) + 10 * MS_1M] * 10
    elif case == "daily_stop":
        ex.state["day_realised_usd"], ex.state["day"] = -30.0, "PINNED"
        ex._roll = lambda now_ms: None  # type: ignore[method-assign]
    elif case == "reconcile":
        ex.alert("TEST_MISMATCH", {}, reconcile=True)
    got_j, got_st, got_recs = _shadow_run(tmp_path / "shadow", replay, _attached_sink(ex, fake))
    assert got_j == base_j, "the shadow journals must be byte-identical in content"
    assert got_st == base_st and got_recs == base_recs
    dj = demo_journal(lite_root).records()
    blocked = [r["data"] for r in dj if r["kind"] == "DEMO_ENTRY_BLOCKED"]
    submitted = [r["data"] for r in dj if r["kind"] == "ENTRY_SUBMIT"]
    expect = {
        "v10_position": V10_ACTIVE,
        "v10_orders": V10_ACTIVE,
        "cap": "DEMO_CAP_24H",
        "daily_stop": "DAILY_STOP",
        "reconcile": "RECONCILIATION_REQUIRED",
    }
    if case == "isolated_executes":
        assert submitted and {s["signal_id"] for s in submitted} <= {
            r["signal_id"] for r in base_recs
        }
        for s in submitted:  # the Demo traded the shadow's own geometry
            r = next(x for x in base_recs if x["signal_id"] == s["signal_id"])
            assert s["shadow_stop"] == r["stop"] and s["shadow_entry_ref"] == r["entry_ref"]
        assert len(submitted) + len(blocked) == len(base_recs)
    else:
        assert not submitted and len(blocked) == len(base_recs)
        assert all(expect[case] in b["blockers"] for b in blocked)
        assert not [p for p in fake.posts() if p["path"] == "/v5/order/create"]
    assert verify_chain(lite_root / "demo" / "strategy_journal.jsonl")["ok"]
    assert _protected_hashes() == before


def test_the_observer_sink_never_blocks_the_shadow_thread() -> None:
    class Slow:
        def __init__(self) -> None:
            self.seen = 0

        def manage(self, now: int, bar: Any) -> dict[str, Any]:
            time.sleep(0.3)
            self.seen += 1
            return {}

        def has_position(self) -> bool:
            return False

    ex = Slow()
    w = DemoWorker(ex, poll_s=0.05)
    w.start()
    cols = {k: np.arange(100, dtype=float) for k in ("high", "low", "close", "atr5")}
    t = time.perf_counter()
    for i in range(70, 80):
        w.sink(cols, i, i, i * MS_1M, 70.0, 30.0, None)
    assert time.perf_counter() - t < 0.2  # ten bars enqueued while the Demo is busy
    cols["close"][79] = -1.0
    item = w.q.queue[-1]
    assert item["bar"].x["close"][79] == 79.0  # a copy: later changes never reach the Demo
    w.stop_ev.set()
    w.join(timeout=10)


# ----------------------------------------------------------------------------- activation / supervisor
def test_activation_gates_isolated_and_shared(tmp_path: Path) -> None:
    dc = _lc()
    base = dict(
        dc=dc, v10_root=tmp_path / "v10", shared_flag=False, runner_alive=True, feed_age_s=3.0,
        warm={"ready": True}, in_window=True, freeze_ok=(True, "ok"), demo_freeze_ok=(True, "ok"),
        chains={"signals": {"ok": True}}, demo_state={}, lease_ok=(True, "ok"), cloud=False,
        base_mode="DISABLED",
    )  # fmt: skip
    flat = {"credentials": True, "reachable": True, "position": {"size": 0.0}, "open_orders": []}
    g = demo_gates(**base, lite_snapshot=flat | {"user_id": 1}, v10_snapshot={"user_id": 2})  # type: ignore[arg-type]
    assert all(v["ok"] for v in g.values()), [k for k, v in g.items() if not v["ok"]]
    g = demo_gates(**base, lite_snapshot=flat | {"user_id": 1}, v10_snapshot={"user_id": 1})  # type: ignore[arg-type]
    assert not g["isolated account proven (different Bybit user id than V10)"]["ok"]
    with pytest.raises(RuntimeError):
        record_activation(tmp_path / "lite", g, "x", "isolated")
    sh = base | {"dc": _lc("shared")}
    _v10_state(tmp_path / "v10", position={"status": "ACTIVE"})
    g = demo_gates(**sh, lite_snapshot=flat, v10_snapshot=None)  # type: ignore[arg-type]
    assert not g["shared account explicitly acknowledged (--shared-account)"]["ok"]
    assert not g["V10 conflict gate clear (no V10 position / orders / reconciliation)"]["ok"]
    _v10_state(tmp_path / "v10")
    g = demo_gates(**(sh | {"shared_flag": True}), lite_snapshot=flat, v10_snapshot=None)  # type: ignore[arg-type]
    assert all(v["ok"] for v in g.values())
    for bad in ({"cloud": True}, {"runner_alive": False}, {"in_window": False},
                {"warm": {"ready": False}}, {"demo_state": {"reconcile_required": True}},
                {"feed_age_s": 600.0}, {"base_mode": "STRATEGY_DEMO"}):  # fmt: skip
        g = demo_gates(
            **(base | bad), lite_snapshot=flat | {"user_id": 1}, v10_snapshot={"user_id": 2}
        )  # type: ignore[arg-type]
        assert not all(v["ok"] for v in g.values()), bad


def test_supervisor_attaches_only_while_activated_and_journal_chain_stays_valid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lite"
    sinks: list[Any] = []

    class Stub:
        def has_position(self) -> bool:
            return False

    built: list[Any] = []

    def build(act: dict[str, Any]) -> Any:
        if len(built) < 2:  # fails twice with the same message, then succeeds
            built.append(1)
            raise RuntimeError("demo credentials missing: set BYBIT_DEMO_V11LW_API_KEY")
        return Stub()

    sup = DemoSupervisor(root, sinks.append, build)
    assert sup.check() == "unchanged" and effective_activation(root) is None
    record_activation(root, {"all": {"ok": True, "detail": None}}, "owner", "isolated")
    assert sup.check() == "attach_failed" and sup.check() == "attach_failed"
    assert _kinds(root).count("DEMO_ATTACH_FAILED") == 1
    assert sup.check() == "attached" and callable(sinks[-1])
    record_owner_event(root, RECON_ACK, "concurrent writer")
    record_deactivation(root, "owner", position_open=False)
    assert sup.check() == "detached" and sinks[-1] is None
    assert verify_chain(root / "demo" / "strategy_journal.jsonl")["ok"]
    assert not any(k.startswith("STRATEGY_DEMO_V10") for k in _kinds(root))


def test_shadow_package_modules_stay_exchange_free(tmp_path: Path) -> None:
    """Every exchange reference lives in the `demo` sub-package; the shadow modules only expose the
    copy-only observer sink."""
    for p in sorted((ROOT / "btc_swing" / "v11_lite_warm").glob("*.py")):
        src = p.read_text()
        for tok in ("BybitDemoClient", "create_order", "cancel_order", "V11LWD-", "api-demo",
                    "BYBIT_DEMO_API", "forward_v10\"", "STRATEGY_DEMO"):  # fmt: skip
            assert tok not in src, (p.name, tok)
    assert fmt_qty(5 / 300, 0.001) == "0.016"
