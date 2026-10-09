"""V5.1 runtime: separate orderLinkId namespace, executor built with V5.1 parameters, runner hook
with the V5.1 build/cycle, read-only V5.1 diagnostic with the feature-quality table, V5-vs-V5.1
comparison, restart/recovery of a V5.1 executor."""

from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
import pytest

from btc_swing.v5.demo import activation as act
from btc_swing.v5.demo import preflight as pf
from btc_swing.v5.demo.ids import (
    STRATEGY_PREFIX,
    V51_STRATEGY_PREFIX,
    is_strategy_link_id,
    strategy_link_id,
)
from btc_swing.v5.forward import runner
from btc_swing.v5.forward.diagnostic import signal_diagnostic
from btc_swing.v51 import diagnostic as dg51
from btc_swing.v51 import forward as fw
from btc_swing.v51 import history as h
from btc_swing.v51.config import PARENT_V5_CONFIG_HASH
from tests.v5.fake_bybit import FakeBybitDemo
from tests.v5.test_demo_strategy import _exec, _trig
from tests.v51.fake_bybit_history import FakeBybitHistory
from tests.v51.helpers import START, synthetic_bars, v5_ctx, v51_cfg

MS_5M = 300_000
DAY = 86_400_000
N = 4300
OUT = (3200, 3260)


def test_v51_link_ids_are_a_separate_namespace() -> None:
    a = strategy_link_id("FAM|LONG|1", "EN")
    b = strategy_link_id("FAM|LONG|1", "EN", V51_STRATEGY_PREFIX)
    assert a.startswith("V5D-") and b.startswith("V51D-") and a[4:] == b[5:]
    assert len(b) <= 36
    assert is_strategy_link_id(a) and not is_strategy_link_id(a, V51_STRATEGY_PREFIX)
    assert is_strategy_link_id(b, V51_STRATEGY_PREFIX) and not is_strategy_link_id(
        b, STRATEGY_PREFIX
    )
    with pytest.raises(ValueError):
        strategy_link_id("x", "EN", "V6D-")


def test_v51_executor_uses_its_prefix_and_recovers_after_restart(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    ex = _exec(tmp_path, fake, activated_at_ms=0)
    ex.link_prefix = V51_STRATEGY_PREFIX
    t = _trig(fake)
    assert ex.enter(t, [])["action"] == "ENTERED"
    links = [o["orderLinkId"] for o in fake.orders.values()]
    assert links and all(lk.startswith("V51D-") for lk in links)
    # a restarted V5.1 executor adopts its own orders; a V5 executor would not see them as its own
    ex2 = _exec(tmp_path, fake, activated_at_ms=0)
    ex2.link_prefix = V51_STRATEGY_PREFIX
    out = ex2.recover()
    assert out["ok"] and ex2.state["position"]["status"] == "ACTIVE"
    assert len(fake.orders) == len(links)  # nothing re-sent
    ex5 = _exec(tmp_path / "v5", fake, activated_at_ms=0)
    r5 = ex5.recover()
    assert r5["strategy_open_orders"] == []  # V51D- orders are not V5 strategy orders


def test_build_executor_v51_checks_parent_stream_freeze_and_own_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from btc_swing.v5.demo import runtime
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config

    seen: dict[str, Any] = {}

    def fake_host(freeze: Any, v5_hash: str, pid: Path) -> list[Any]:
        seen["hash"] = v5_hash
        return []

    monkeypatch.setattr(runtime, "require_forward_host", fake_host)
    monkeypatch.setattr(runtime, "load_freeze", lambda: {"v5_config_hash": PARENT_V5_CONFIG_HASH})
    built: list[Any] = []
    monkeypatch.setattr(runtime, "BybitDemoClient", lambda *a, **k: built.append(a))
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(config_hash="v51hash"),
        paths=SimpleNamespace(run_pid=tmp_path / "x.pid", root=tmp_path),
    )
    dcfg = load_demo_config(Path("config/btc_swing_v5_demo.yaml"), ExecutionMode.STRATEGY_DEMO)
    checks: list[Any] = []

    def own_freeze(cfg: Any) -> None:
        checks.append(cfg.config_hash)
        raise RuntimeError("no V5.1 freeze")

    build = partial(
        runtime.build_executor,
        stream_hash=PARENT_V5_CONFIG_HASH,
        freeze_check=own_freeze,
        link_prefix=V51_STRATEGY_PREFIX,
    )
    with pytest.raises(RuntimeError, match=r"no V5\.1 freeze"):
        build(ctx, dcfg)  # type: ignore[arg-type]
    assert seen["hash"] == PARENT_V5_CONFIG_HASH and checks == ["v51hash"] and built == []


def test_runner_hook_runs_the_v51_build_and_cycle(tmp_path: Path) -> None:
    calls: list[str] = []
    root = tmp_path / "forward_v51"
    root.mkdir()
    ctx = SimpleNamespace(paths=SimpleNamespace(root=root))
    hook = runner._demo_hook(
        ctx,
        lambda c, d: calls.append("build") or SimpleNamespace(recover=lambda: "ok"),
        lambda ex, c, a, b, r: calls.append("cycle") or {"ran": True},
        "STRATEGY_DEMO (V5.1)",
    )
    assert hook is not None
    assert hook(None, None, None)["mode"] == "DISABLED" and calls == []  # not activated
    act.record_activation(root, {"g": {"ok": True}}, "owner", version="V5.1")
    assert hook(None, None, None) == {"ran": True} and calls == ["build", "cycle"]
    assert hook(None, None, None) == {"ran": True} and calls == ["build", "cycle", "cycle"]
    recs = [
        json.loads(x) for x in (root / "demo" / "strategy_journal.jsonl").read_text().splitlines()
    ]
    assert recs[-1]["kind"] == "STRATEGY_DEMO_ACTIVATED" and recs[-1]["data"]["version"] == "V5.1"
    rt = runner.V51Runtime(ctx, tmp_path, lambda c, d: None, lambda *a: None, lambda *a: None)
    assert rt.reports_dir == tmp_path


def _fingerprint(root: Path) -> str:
    hh = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            hh.update(f"{p.relative_to(root)}|{p.stat().st_size}|{p.stat().st_mtime_ns}".encode())
    return hh.hexdigest()


def test_v51_diagnostic_is_read_only_and_reports_feature_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    bars = synthetic_bars(N, outages=[OUT])
    v5_start = START + 1000 * MS_5M
    ctx5 = v5_ctx(tmp_path, bars, v5_start)
    ctx51 = fw.V51Context(
        ctx5.fcfg, v51_cfg(), fw.V51Paths(tmp_path, "BTCUSDT"), v5_start, v5_start
    )  # type: ignore[arg-type]
    fake = FakeBybitHistory(START - 35 * DAY, v5_start)
    cl = h.BybitPublicHistory(transport=fake.transport(), sleep=lambda s: None)
    h.fetch_oi_seed(
        cl,
        h.SeedStore(ctx51.paths.seed_dir("oi"), "oi"),
        "BTCUSDT",
        START - 35 * DAY,
        v5_start,
        "5min",
    )
    now = int(bars["close_time_ms"][-1]) + 30_000
    before = _fingerprint(tmp_path)
    d51 = dg51.signal_diagnostic_v51(ctx51, now)
    d5 = signal_diagnostic(ctx5, now)
    assert _fingerprint(tmp_path) == before  # nothing written by either diagnostic
    assert d51["variant"].startswith("V5.1") and d51["feature_table"]
    ft = {r["feature"]: r for r in d51["feature_table"]}
    assert ft["vol_1h_z"]["excluded_in_window"] >= 20  # partial-outage rows (exact zeros are NaN)
    assert ft["vol_1h_z"]["quality"] == "clean" and ft["atr_1h (clean bars)"]["quality"] == "clean"
    assert all(
        r["warm"]
        for f, r in ft.items()
        if f in ("ret_1h_z", "vol_1h_z", "imbalance_1h_z", "cvd_slope_1h_z")
    )
    # V5.1 excludes every gap-contaminated observation from its baselines
    q = d51["data_quality"]
    assert all(
        q[c]["window_obs_from_gap_rows"] == 0
        for c in ("vol_1h_z", "ret_1h_z", "imbalance_1h_z", "cvd_slope_1h_z")
    )
    assert q["vol_1h_z"]["unreachable"] is False
    assert all(f["logic_check"] == "matches frozen detector mask" for f in d51["families"])
    assert all(f["data_quality"] == "clean" for f in d51["families"])
    text = dg51.render_v51(d51)
    assert "FEATURE | VALID OBS | WARM | CURRENT | Z | QUALITY" in text and "DATA QUALITY" in text
    comp = dg51.compare_diagnostics(d5, d51)
    assert len(comp["families"]) == 8 and comp["features"]["vol_1h_z"]["v51_obs_from_gap_rows"] == 0
    assert comp["features"]["vol_1h_z"]["v5_obs_from_gap_rows"] > 0
    assert "CAUSE" in dg51.render_comparison(comp)


def test_v51_diagnostic_marks_invalid_now_right_after_an_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    bars = synthetic_bars(N, outages=[(N - 15, N - 8)])  # the current 1h window touches it
    v5_start = START + 1000 * MS_5M
    ctx5 = v5_ctx(tmp_path, bars, v5_start)
    ctx51 = fw.V51Context(
        ctx5.fcfg, v51_cfg(), fw.V51Paths(tmp_path, "BTCUSDT"), v5_start, v5_start
    )  # type: ignore[arg-type]
    d51 = dg51.signal_diagnostic_v51(ctx51, int(bars["close_time_ms"][-1]) + 30_000)
    ft = {r["feature"]: r for r in d51["feature_table"]}
    assert ft["ret_1h_z"]["quality"].startswith("INVALID NOW") and ft["ret_1h_z"]["z"] is None
    assert all(not f["stage_a_event_now"] for f in d51["families"])
    assert all(f["data_quality"].startswith("INVALID NOW") for f in d51["families"])
    assert np.isnan(ft["atr_1h (clean bars)"]["current"])


def test_activation_gates_v51_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from btc_swing.v51.cli import v51_activation_gates

    v5root = tmp_path / "forward"
    (v5root / "demo").mkdir(parents=True)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(v5=SimpleNamespace(root=v5root)),
        v5_start_ms=1_000,
        cfg=SimpleNamespace(data_quality=SimpleNamespace(oi_seed=SimpleNamespace(enabled=True))),
    )
    monkeypatch.setattr("btc_swing.v51.freeze.load_v51_freeze", lambda: None)
    d51 = {
        "feature_table": [
            {"feature": z, "warm": False, "valid_obs": 10, "quality": "warming"}
            for z in ("ret_1h_z", "vol_1h_z", "imbalance_1h_z", "cvd_slope_1h_z", "oi_chg_1h_z")
        ],
        "data_quality": {"vol_1h_z": {"unreachable": True}},
        "seed_verification": {},
        "demo": {"global_blockers": ["COLLECTOR_STALE", "STRATEGY_DEMO not active on this host"]},
        "gap_rows_last_12_bars": 3,
    }
    g = v51_activation_gates(ctx, d51)
    assert not any(v["ok"] for k, v in g.items() if "V5 STRATEGY_DEMO" not in k)
    assert g["V5 STRATEGY_DEMO inactive on this host and V5 demo flat"]["ok"]
    assert g["no current safety blocker"]["detail"] == ["COLLECTOR_STALE"]
    with pytest.raises(RuntimeError, match="not every gate passed"):
        act.record_activation(tmp_path / "forward_v51", g, "x", version="V5.1")
    assert not (tmp_path / "forward_v51" / "demo" / "strategy_journal.jsonl").exists()
    assert pl.DataFrame({"a": [1]}).height == 1
