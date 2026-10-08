"""Temporary authoritative Mac host: launchd/systemd host gate, single-runner lock, authority lease,
STRATEGY_DEMO activation (immutable, host-bound, gated), pre-activation refusal, missing-data
coverage (never backfilled), archive warm-up clamp, migration of demo/authority journals."""

from __future__ import annotations

import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from btc_swing.v5.demo import activation as act
from btc_swing.v5.demo import preflight as pf
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
from btc_swing.v5.forward import host
from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
from btc_swing.v5.forward.freeze import write_freeze
from btc_swing.v5.forward.ops import coverage, export_state, integrity_compare, integrity_snapshot
from btc_swing.v5.forward.pipeline import ForwardContext
from tests.v5.fake_bybit import FakeBybitDemo
from tests.v5.test_demo_strategy import _exec, _trig

ROOT = Path(__file__).resolve().parents[2]
FREEZE = {
    "v5_config_hash": pf.EXPECTED_V5_HASH,
    "observation_start_ms": pf.EXPECTED_OBSERVATION_START_MS,
    "observation_start": "2026-10-07T14:59:22.972000+00:00",
}
LAUNCHCTL = """gui/501/com.btcswing.v5-forward = {
	active count = 1
	path = /Users/x/Library/LaunchAgents/com.btcswing.v5-forward.plist
	state = running
	program = /bin/bash
	runs = 3
	pid = 4242
	last exit code = 0
}"""


def _other_host(monkeypatch: pytest.MonkeyPatch, hid: str = "ffffffffffffffff") -> None:
    monkeypatch.setattr(
        host, "host_identity", lambda: {"host_id": hid, "hostname": "nuc", "os": "LINUX"}
    )
    monkeypatch.setattr(
        act, "host_identity", lambda: {"host_id": hid, "hostname": "nuc", "os": "LINUX"}
    )


def _ctx(tmp: Path, start: int = pf.EXPECTED_OBSERVATION_START_MS) -> ForwardContext:
    fcfg = load_forward_config(ROOT / "config" / "btc_swing_v5_forward.yaml")
    return ForwardContext(fcfg, load_frozen_v5(fcfg, ROOT), ForwardPaths(tmp, "BTCUSDT"), start)


# ---------------------------------------------------------------------------- service managers


def test_launchctl_print_parsing_and_platform_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    p = host.parse_launchctl_print(LAUNCHCTL)
    assert p == {"state": "running", "pid": 4242, "runs": 3, "last_exit": "0"}
    monkeypatch.setattr(host, "host_os", lambda: "MACOS")
    monkeypatch.setattr(host, "launchd_status", lambda: {"ok": True, "pid": 4242, "detail": "x"})
    assert host.service_manager_status()["manager"] == "launchd"
    monkeypatch.setattr(host, "host_os", lambda: "LINUX")
    monkeypatch.setattr(host, "systemd_status", lambda: {"ok": True, "pid": 7, "detail": "y"})
    assert host.service_manager_status()["manager"] == "systemd"
    monkeypatch.setattr(host, "host_os", lambda: "WINDOWS")
    assert not host.service_manager_status()["ok"]


def _host_ok(monkeypatch: pytest.MonkeyPatch, tmp: Path, osn: str, mgr: str) -> Path:
    monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
    monkeypatch.setattr(pf, "_runner_pids", lambda: [os.getpid()])
    monkeypatch.setattr(
        pf,
        "_service_manager",
        lambda: {"os": osn, "manager": mgr, "ok": True, "pid": os.getpid(), "detail": "ok"},
    )
    pid = tmp / "forward_run.pid"
    pid.write_text(str(os.getpid()))
    host.claim_authority(tmp, "test", {})
    return pid


@pytest.mark.parametrize(("osn", "mgr"), [("MACOS", "launchd"), ("LINUX", "systemd")])
def test_forward_host_gate_accepts_macos_launchd_or_linux_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, osn: str, mgr: str
) -> None:
    pid = _host_ok(monkeypatch, tmp_path, osn, mgr)
    with host.RunnerLock(tmp_path / pf.RUNNER_LOCK_NAME):
        checks = pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, pid)
    assert all(c["ok"] for c in checks) and len(checks) == 9


def test_forward_host_gate_not_weakened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _host_ok(monkeypatch, tmp_path, "MACOS", "launchd")
    lock = host.RunnerLock(tmp_path / pf.RUNNER_LOCK_NAME)
    lock.acquire()
    try:
        for bad, name in (
            ({"os": "MACOS", "manager": "launchd", "ok": False, "pid": None}, "service-manager"),
            ({"os": "WINDOWS", "manager": None, "ok": True, "pid": os.getpid()}, "service-manager"),
            ({"os": "MACOS", "manager": "launchd", "ok": True, "pid": 1}, "service-managed"),
        ):
            monkeypatch.setattr(pf, "_service_manager", lambda b=bad: b)
            with pytest.raises(RuntimeError, match=name):
                pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, pid)
        monkeypatch.setattr(
            pf,
            "_service_manager",
            lambda: {"os": "MACOS", "manager": "launchd", "ok": True, "pid": os.getpid()},
        )
        monkeypatch.setattr(pf, "is_cloud_session_container", lambda: True)
        with pytest.raises(RuntimeError, match="persistent host"):
            pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, pid)
        monkeypatch.setattr(pf, "is_cloud_session_container", lambda: False)
        with pytest.raises(RuntimeError, match="observation start"):
            pf.require_forward_host({**FREEZE, "observation_start_ms": 2}, pf.EXPECTED_V5_HASH, pid)
        _other_host(monkeypatch)
        monkeypatch.setattr(pf, "host_identity", host.host_identity)
        with pytest.raises(RuntimeError, match="authority lease held by this host"):
            pf.require_forward_host(FREEZE, pf.EXPECTED_V5_HASH, pid)
    finally:
        lock.release()


# ---------------------------------------------------------------------------- lock and lease


def test_single_runner_lock_prevents_a_second_runner(tmp_path: Path) -> None:
    p = tmp_path / "forward_run.lock"
    assert not host.lock_held(p)
    a = host.RunnerLock(p)
    a.acquire()
    assert host.lock_held(p)
    with pytest.raises(host.RunnerLockedError):
        host.RunnerLock(p).acquire()
    a.release()
    assert not host.lock_held(p)


def test_authority_lease_claim_release_and_copied_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    assert host.runner_may_start(root)[0]  # pre-lease state
    host.release_authority(root, "cloud collector stopped", {}, legacy=True)
    assert not host.runner_may_start(root)[0]  # released: must be claimed first
    mac = host.claim_authority(root, "mac", {})
    assert host.claim_authority(root, "again", {})["host_id"] == mac["host_id"]  # idempotent
    assert host.runner_may_start(root)[0]
    real = host.host_identity
    _other_host(monkeypatch)
    ok, why = host.runner_may_start(root)  # a copy of the Mac state on another machine
    assert not ok and "another host" in why
    with pytest.raises(RuntimeError, match="held by host"):
        host.claim_authority(root, "nuc", {})
    monkeypatch.setattr(host, "host_identity", real)
    host.release_authority(root, "migrate to nuc", {})
    _other_host(monkeypatch)
    assert host.claim_authority(root, "nuc", {})["hostname"] == "nuc"
    assert verify_chain(host.authority_path(root))["ok"]
    assert [r["kind"] for r in HashChainJournal(host.authority_path(root), "a").records()] == [
        "AUTHORITY_RELEASED",
        "AUTHORITY_CLAIMED",
        "AUTHORITY_RELEASED",
        "AUTHORITY_CLAIMED",
    ]


def test_runner_refuses_without_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from btc_swing.v5.forward import runner

    ctx = _ctx(tmp_path)
    host.release_authority(ctx.paths.root, "x", {}, legacy=True)
    with pytest.raises(RuntimeError, match="forward runner refused"):
        runner.run_forward(ctx, tmp_path / "reports", 1.0)


# ---------------------------------------------------------------------------- activation


def _gates(**over: Any) -> dict[str, dict[str, Any]]:
    kw: dict[str, Any] = {
        "tests": {"ok": True, "summary": "all passed"},
        "preflight": {
            "run_id": "p1",
            "gates": {
                pf.DEMO_GATE: {"status": "PASSED", "checks": []},
                pf.HOST_GATE: {"status": "PASSED", "checks": []},
            },
        },
        "smoke": {"run_id": "s1", "status": "PASSED"},
        "v5_hash": pf.EXPECTED_V5_HASH,
        "freeze": FREEZE,
        "start_ms": pf.EXPECTED_OBSERVATION_START_MS,
        "coverage": {"duplicates": {"bars": 0, "signals": 0, "paper_trades": 0}},
        "chains": {"strategy": {"ok": True, "records": 0}},
        "demo_state": {"position": None, "reconcile_required": False},
        "reference_equity": 5000.0,
        "risk_per_trade": 0.0025,
        "frozen_risk": 0.0025,
        "config_mode": "DISABLED",
        "expected_v5_hash": pf.EXPECTED_V5_HASH,
        "expected_start_ms": pf.EXPECTED_OBSERVATION_START_MS,
    }
    kw.update(over)
    return act.activation_gates(**kw)


@pytest.mark.parametrize(
    "over",
    [
        {"tests": {"ok": False, "summary": "1 failed"}},
        {
            "preflight": {
                "gates": {
                    pf.DEMO_GATE: {"status": "PASSED", "checks": []},
                    pf.HOST_GATE: {"status": "FAILED", "checks": []},
                }
            }
        },
        {
            "preflight": {
                "gates": {
                    pf.DEMO_GATE: {"status": "FAILED", "checks": []},
                    pf.HOST_GATE: {"status": "PASSED", "checks": []},
                }
            }
        },
        {"smoke": None},
        {"v5_hash": "x" * 64},
        {"start_ms": 1},
        {"coverage": {"duplicates": {"bars": 1}}},
        {"chains": {"strategy": {"ok": False}}},
        {"demo_state": {"position": {"status": "ACTIVE"}}},
        {"demo_state": {"position": None, "reconcile_required": True}},
        {"reference_equity": 2000.0},
        {"risk_per_trade": 0.005},
        {"config_mode": "STRATEGY_DEMO"},
    ],
)
def test_any_failing_gate_blocks_activation(tmp_path: Path, over: dict[str, Any]) -> None:
    g = _gates(**over)
    assert not all(v["ok"] for v in g.values())
    with pytest.raises(RuntimeError, match="not every gate passed"):
        act.record_activation(tmp_path, g, "owner")
    assert act.activation_state(tmp_path)["status"] == "NEVER_ACTIVATED"


def test_activation_is_immutable_host_bound_and_deactivation_needs_flat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    g = _gates()
    assert all(v["ok"] for v in g.values())
    rec = act.record_activation(tmp_path, g, "owner")
    assert rec["activated_at_ms"] > 0 and act.effective_activation(tmp_path) is not None
    with pytest.raises(RuntimeError, match="already active"):
        act.record_activation(tmp_path, g, "again")
    real = act.host_identity
    _other_host(monkeypatch)
    assert act.effective_activation(tmp_path) is None  # migrated state: NUC must re-activate
    monkeypatch.setattr(act, "host_identity", real)
    with pytest.raises(RuntimeError, match="position is open"):
        act.record_deactivation(tmp_path, "stop", position_open=True)
    act.record_deactivation(tmp_path, "stop", position_open=False)
    assert act.effective_activation(tmp_path) is None
    j = act.strategy_journal_path(tmp_path)
    assert verify_chain(j)["ok"]
    assert [r["kind"] for r in HashChainJournal(j, "s").records()] == [
        act.ACTIVATED,
        act.DEACTIVATED,
    ]


def test_executor_trades_only_signals_after_activation(tmp_path: Path) -> None:
    fake = FakeBybitDemo()
    t = _trig(fake)
    ex = _exec(tmp_path / "a", fake, activated_at_ms=t.t_ms)  # bar closed AT activation
    out = ex.enter(t, [])
    assert out["action"] == "REFUSED_BEFORE_ACTIVATION" and not fake.orders
    assert any(r["kind"] == "PRE_ACTIVATION_SIGNAL_REFUSED" for r in ex.journal.records())
    ex2 = _exec(tmp_path / "b", FakeBybitDemo(), activated_at_ms=None)  # no activation at all
    assert ex2.enter(_trig(FakeBybitDemo()), [])["action"] == "REFUSED_BEFORE_ACTIVATION"
    fake3 = FakeBybitDemo()
    t3 = _trig(fake3)
    ex3 = _exec(tmp_path / "c", fake3, activated_at_ms=t3.t_ms - 1)
    assert ex3.enter(t3, [])["action"] == "ENTERED"
    assert ex3.state["position"]["status"] == "ACTIVE"
    # one position maximum: a second genuine trigger is blocked
    fake3.move_price(fake3.price, advance_ms=300_000)
    t4 = _trig(fake3)
    out4 = ex3.step([t4], [], None, fake3.now_ms)
    assert out4["entries"][0]["action"] == "BLOCKED"
    assert "POSITION_OPEN" in out4["entries"][0]["blockers"]


def test_build_executor_refuses_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from btc_swing.v5.demo import runtime
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config

    monkeypatch.setattr(runtime, "require_forward_host", lambda *a: [])
    monkeypatch.setattr(runtime, "load_freeze", lambda: FREEZE)
    built: list[Any] = []
    monkeypatch.setattr(runtime, "BybitDemoClient", lambda *a, **k: built.append(a))
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(config_hash=pf.EXPECTED_V5_HASH),
        paths=SimpleNamespace(run_pid=tmp_path / "x.pid", root=tmp_path),
    )
    dcfg = load_demo_config(ROOT / "config" / "btc_swing_v5_demo.yaml", ExecutionMode.STRATEGY_DEMO)
    with pytest.raises(RuntimeError, match="no STRATEGY_DEMO_ACTIVATED"):
        runtime.build_executor(ctx, dcfg)  # type: ignore[arg-type]
    assert built == []


def test_runner_demo_hook_is_disabled_until_activation(tmp_path: Path) -> None:
    from btc_swing.v5.forward.runner import _demo_hook

    ctx = _ctx(tmp_path)
    hook = _demo_hook(ctx)
    assert hook is not None
    assert hook(None, None, None)["mode"] == "DISABLED"  # no activation event: no API call


def test_demo_config_reference_equity_5000_mode_disabled() -> None:
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config

    d = load_demo_config(ROOT / "config" / "btc_swing_v5_demo.yaml")
    assert d.mode is ExecutionMode.DISABLED and d.reference_equity_usdt == 5000.0
    assert d.risk_per_trade == 0.0025


# ---------------------------------------------------------------------------- coverage / seed


def _bars(paths: ForwardPaths, rows: list[tuple[int, int]]) -> None:
    df = pl.DataFrame(
        {
            "open_time_ms": [t for t, _ in rows],
            "close_time_ms": [t + 300_000 for t, _ in rows],
            "close": [100.0 if n else float("nan") for _, n in rows],
            "trades": [n for _, n in rows],
        }
    )
    df.write_parquet(paths.bars_dir / "2026-10-07.parquet")


def test_coverage_reports_every_missing_period_and_never_fills(tmp_path: Path) -> None:
    start = int(datetime(2026, 10, 7, 15, 0, tzinfo=UTC).timestamp() * 1000)
    ctx = _ctx(tmp_path, start)
    m = 300_000
    # live 15:10-15:20, collector down 15:20-15:30 (zero-trade rows), live 15:30-15:35
    _bars(
        ctx.paths,
        [
            (start + 2 * m, 50),
            (start + 3 * m, 40),
            (start + 4 * m, 0),
            (start + 5 * m, 0),
            (start + 6 * m, 7),
        ],
    )
    c = coverage(ctx, now_ms=start + 9 * m)
    kinds = [(g["kind"].split(" ")[0], g["bars"]) for g in c["missing_periods"]]
    assert kinds == [("before_first_forward_bar", 2), ("collector_down", 2), ("since_last_bar", 2)]
    assert c["live_bars"] == 3 and c["missing_bars_to_now"] == 6 and c["expected_bars_to_now"] == 9
    assert c["duplicates"] == {"bars": 0, "signals": 0, "paper_trades": 0}
    again = coverage(ctx, now_ms=start + 9 * m)
    assert again["forward_rows"] == 5  # coverage only reads; nothing was written or filled


def test_archive_seed_never_reaches_the_observation_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from btc_swing.v5.forward import pipeline

    asked: list[list[str]] = []

    class Ing:
        def __init__(self, *a: Any) -> None:
            self._done: dict[str, Any] = {}

        def ingest_days(self, days: list[str]) -> dict[str, Any]:
            asked.append(days)
            return {"fetched": len(days)}

    monkeypatch.setattr(pipeline, "BybitSeedIngestor", Ing)
    ctx = _ctx(tmp_path)  # fresh host: no forward bars at all
    now = int(datetime(2026, 10, 10, 12, 0, tzinfo=UTC).timestamp() * 1000)
    pipeline.extend_seed(ctx, now)
    assert asked and max(asked[0]) == "2026-10-06"  # never on/after the start day 2026-10-07


# ---------------------------------------------------------------------------- migration


def test_export_carries_demo_and_authority_journals_with_prefix_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path / "src")
    root = ctx.paths.root
    host.claim_authority(root, "mac", {})
    act.record_activation(root, _gates(), "owner")
    before = integrity_snapshot(ctx)
    assert before["hash_chained"]["host_authority"]["lines"] == 1
    freeze = tmp_path / "manifests" / "v5_forward_freeze.json"
    write_freeze(ctx.cfg, ctx.fcfg, freeze)
    cfgs = [ROOT / "config" / "btc_swing_v5_forward.yaml", ROOT / "config" / "btc_swing_v5.yaml"]
    lock = host.RunnerLock(root / "forward_run.lock")
    lock.acquire()
    with pytest.raises(RuntimeError, match="stop it before a cold export"):
        export_state(ctx, tmp_path / "e" / "s.tar.gz", freeze, cfgs)
    lock.release()
    man = export_state(ctx, tmp_path / "e" / "s.tar.gz", freeze, cfgs)
    assert "forward/host/authority.jsonl" in man["files"]
    assert "forward/demo/strategy_journal.jsonl" in man["files"]
    man2 = export_state(ctx, tmp_path / "e2" / "s.tar.gz", freeze, cfgs, exclude_dirs=("demo",))
    assert not any(k.startswith("forward/demo/") for k in man2["files"])
    # tampering with an earlier strategy-journal record breaks prefix identity
    j = act.strategy_journal_path(root)
    j.write_text(j.read_text().replace("owner", "OWNER"))
    res = integrity_compare(before, integrity_snapshot(ctx), ctx)
    bad = [c["check"] for c in res["checks"] if not c["ok"]]
    assert "demo_strategy_journal: earlier records intact (prefix identity)" in bad
    assert "demo_strategy_journal: hash chain valid" in bad


def test_macos_launchd_templates_are_valid_and_secret_free() -> None:
    d = ROOT / "deploy" / "macos"
    run = plistlib.loads((d / "com.btcswing.v5-forward.plist.template").read_bytes())
    assert run["Label"] == host.LAUNCHD_LABEL and run["KeepAlive"] is True
    assert run["RunAtLoad"] is True and run["ExitTimeOut"] == 90
    assert run["ProgramArguments"][-1].endswith("deploy/macos/run_forward.sh")
    hp = plistlib.loads((d / "com.btcswing.v5-forward-health.plist.template").read_bytes())
    assert hp["Label"] == host.LAUNCHD_HEALTH_LABEL and hp["StartInterval"] == 300
    for f in d.iterdir():
        txt = f.read_text()
        assert "BYBIT_DEMO_API_SECRET=" not in txt.replace('"$CONF/demo.env"', "")
    wrapper = (d / "run_forward.sh").read_text()
    assert "caffeinate -i -m -s -w $$" in wrapper and "exec " in wrapper
    assert "unset BYBIT_EXECUTION_MODE" in wrapper and "must be 600" in wrapper
