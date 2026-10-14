"""Generic read-only forward reporting layer: correctness on synthetic V10-style and V11-style
journals, and regression proofs that running a report changes nothing (journal hashes, state.json,
freeze manifests, strategy hashes) and makes no exchange call."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from btc_swing.forward_report import cli as report_cli
from btc_swing.forward_report.generations import spec_v10, spec_v11, spec_v11w
from btc_swing.forward_report.metrics import full_history
from btc_swing.forward_report.render import render_full_history
from btc_swing.forward_report.spec import GenerationSpec
from btc_swing.v5.demo.journal import HashChainJournal, verify_chain

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_791_900_000_000  # 2026-10-13T14:xx UTC
MIN = 60_000


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------------- synthetic roots
def _v11_style_root(root: Path, with_trades: bool = True) -> dict[str, Path]:
    j = root / "journals"
    j.mkdir(parents=True, exist_ok=True)
    cand = HashChainJournal(j / "candidates.jsonl", "t_candidates")
    ep = HashChainJournal(j / "episodes.jsonl", "t_episodes")
    sig = HashChainJournal(j / "signals.jsonl", "t_signals")
    nm = HashChainJournal(j / "nearmiss.jsonl", "t_nearmiss")
    sh = HashChainJournal(j / "shadow_trades.jsonl", "t_shadow")
    out = HashChainJournal(j / "outcomes.jsonl", "t_outcomes")
    ep.append("RUNNER_STARTED", {"pid": 1})
    for k, (side, score) in enumerate(
        [("LONG", 66.0), ("SHORT", 71.0), ("LONG", 80.0), ("LONG", 68.5)]
    ):
        cand.append("CANDIDATE", {"ts_ms": T0 + k * 5 * MIN, "side": side, "score": score})
    ep.append("ARMED", {"ts_ms": T0 + 10 * MIN, "family": "A", "side": "LONG", "score": 72.0})
    ep.append("ARMED", {"ts_ms": T0 + 12 * MIN, "family": "B", "side": "SHORT", "score": 70.5})
    sig.append(
        "SIGNAL_EXECUTED",
        {
            "signal_id": "V11S-1-AL",
            "action": "EXECUTED",
            "family": "A",
            "side": "LONG",
            "score": 78.0,
            "decision_ms": T0 + 15 * MIN,
            "stop_pct": 0.5,
            "risk_usdt": 7.5,
        },
    )
    sig.append(
        "SIGNAL_BLOCKED",
        {
            "signal_id": "V11S-2-BS",
            "action": "BLOCKED",
            "family": "B",
            "side": "SHORT",
            "score": 76.0,
            "decision_ms": T0 + 20 * MIN,
            "blockers": ["STOP_TOO_WIDE_FOR_V11"],
        },
    )
    nm.append("NEAR_MISS", {"ts_ms": T0 + 22 * MIN, "family": "C", "side": "LONG", "score": 70.0})
    if with_trades:
        sh.append("SHADOW_OPEN", {"signal_id": "V11S-1-AL", "family": "A", "side": 1})
        sh.append(
            "SHADOW_CLOSED",
            {
                "signal_id": "V11S-1-AL",
                "family": "A",
                "side": 1,
                "exit_ms": T0 + 40 * MIN,
                "exit_reason": "TP2",
                "hold_min": 25.0,
                "net_usd": 6.0,
                "net_r": 0.8,
                "gross_r": 1.0,
            },
        )
        sh.append(
            "SHADOW_CLOSED",
            {
                "signal_id": "V11S-3-BS",
                "family": "B",
                "side": -1,
                "exit_ms": T0 + 90 * MIN,
                "exit_reason": "STOP",
                "hold_min": 12.0,
                "net_usd": -7.5,
                "net_r": -1.0,
                "gross_r": -0.9,
            },
        )
    for h in (5, 15):
        out.append(
            "OUTCOME",
            {"id": "V11C-1-L", "kind": "CANDIDATE", "horizon_min": h, "return_bps": 3.0 * h},
        )
    (root / "state.json").write_text(
        json.dumps(
            {
                "observation_start_ms": T0,
                "last_bar_ms": T0 + 120 * MIN,
                "counters": {
                    "candidates": 4,
                    "armed": 2,
                    "triggers": 5,
                    "executed": 1,
                    "blocked": 1,
                    "nearmiss": 1,
                },
            }
        )
    )
    return {
        "candidates": j / "candidates.jsonl",
        "episodes": j / "episodes.jsonl",
        "signals": j / "signals.jsonl",
        "nearmiss": j / "nearmiss.jsonl",
        "shadow_trades": j / "shadow_trades.jsonl",
        "outcomes": j / "outcomes.jsonl",
    }


def _v10_style_root(root: Path) -> tuple[dict[str, Path], Path, Path]:
    j = root / "journals"
    d = root / "demo"
    j.mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    ep = HashChainJournal(j / "episodes.jsonl", "v10_episodes")
    sig = HashChainJournal(j / "signals.jsonl", "v10_signals")
    HashChainJournal(j / "nearmiss.jsonl", "v10_nearmiss")
    sh = HashChainJournal(j / "shadow_trades.jsonl", "v10_shadow_trades")
    HashChainJournal(j / "outcomes.jsonl", "v10_outcomes")
    ep.append(
        "EPISODE_ARMED",
        {
            "family": "A",
            "side": "LONG",
            "armed_ts": "2026-10-13T14:00:00+00:00",
            "conditions": {"liq": True},
        },
    )
    ep.append(
        "EPISODE_TRIGGERED",
        {"family": "A", "side": "LONG", "armed_ts": "2026-10-13T14:00:00+00:00"},
    )
    ep.append(
        "EPISODE_INVALIDATED",
        {
            "family": "B",
            "side": "SHORT",
            "detail": "price moved away",
            "conditions": {"absorb": False},
        },
    )
    sig.append(
        "SIGNAL_EXECUTED",
        {
            "signal_id": "V10S-1",
            "action": "EXECUTED",
            "family": "A",
            "side": "LONG",
            "at": "2026-10-13T14:05:00+00:00",
        },
    )
    sig.append(
        "SIGNAL_BLOCKED",
        {
            "signal_id": "V10S-2",
            "action": "BLOCKED",
            "family": "C",
            "side": "SHORT",
            "blockers": ["POSITION_OPEN"],
        },
    )
    sig.append("DAILY_SAFETY_BLOCK", {"reason": "CONSECUTIVE_LOSSES"})
    sh.append(
        "SHADOW_CLOSED",
        {
            "signal_id": "V10S-1",
            "family": "A",
            "side_sign": 1,
            "exit_ms": T0 + 30 * MIN,
            "exit_reason": "TP1",
            "hold_min": 9.0,
            "net_usd": 2.0,
            "net_r": 0.4,
            "execution_mode": "SHADOW",
        },
    )
    dj = HashChainJournal(d / "strategy_journal.jsonl", "strategy_demo_v10")
    dj.append("STRATEGY_DEMO_V10_ACTIVATED", {"host": "mac"}, "STRATEGY_DEMO_V10")
    dj.append("EXECUTION_CONFIRMED", {"signal_id": "V10S-1"}, "STRATEGY_DEMO_V10")
    (d / "demo_trades.jsonl").write_text(
        json.dumps(
            {
                "signal_id": "V10S-1",
                "family": "A",
                "side": "LONG",
                "closed_ms": T0 + 31 * MIN,
                "exit_reason": "TP1",
                "net_pnl": 1.5,
                "execution_mode": "DEMO",
            }
        )
        + "\n"
    )
    (d / "strategy_state.json").write_text(json.dumps({"mode": "STRATEGY_DEMO"}))
    (root / "state.json").write_text(
        json.dumps(
            {
                "observation_start_ms": T0,
                "last_tick_ms": T0 + 60 * MIN,
                "counters": {"armed": 1, "triggered": 1, "executed": 1},
            }
        )
    )
    return (
        {
            "episodes": j / "episodes.jsonl",
            "signals": j / "signals.jsonl",
            "nearmiss": j / "nearmiss.jsonl",
            "shadow_trades": j / "shadow_trades.jsonl",
            "outcomes": j / "outcomes.jsonl",
        },
        d / "strategy_journal.jsonl",
        d / "demo_trades.jsonl",
    )


def _spec(
    name: str, root: Path, journals: dict[str, Path], reports_dir: Path, freeze: Path, **kw: Any
) -> GenerationSpec:
    return GenerationSpec(
        name=name,
        label=f"TEST {name}",
        root=root,
        reports_dir=reports_dir,
        state=root / "state.json",
        freeze=freeze,
        strategy_hash="abc123",
        journals=journals,
        **kw,
    )


# ----------------------------------------------------------------------------- correctness
def test_v11_style_full_history_metrics(tmp_path: Path) -> None:
    root = tmp_path / "forward_x"
    journals = _v11_style_root(root)
    fz = tmp_path / "freeze.json"
    fz.write_text(
        json.dumps(
            {
                "strategy_hash": "abc123",
                "frozen_at": "2026-10-13T00:00:00+00:00",
                "observation_start": "2026-10-13T00:30:00+00:00",
                "observation_start_ms": T0,
            }
        )
    )
    rep = full_history(_spec("v11x", root, journals, Path("reports/test_x"), fz), equity=5000.0)
    f = rep["funnel"]
    assert f["candidates"] == 4 and f["armed"] == 2 and f["executed"] == 1 and f["blocked"] == 1
    assert f["near_misses"] == 1 and f["shadow_closed"] == 2 and f["outcomes"] == 2
    assert (
        f["triggers_journaled"] == "N/A" and f["triggers_counter"] == 5
    )  # V11 counts, never infers
    assert f["invalidated"] == "N/A" and f["expired"] == "N/A"
    assert (
        rep["by_family_side"]["armed"]["A-LONG"] == 1
        and rep["by_family_side"]["armed"]["B-SHORT"] == 1
    )
    assert (
        rep["by_family_side"]["executed"]["A"] == 1
        and rep["by_family_side"]["shadow_closed"]["SHORT"] == 1
    )
    assert rep["blockers"] == {"STOP_TOO_WIDE_FOR_V11": 1} and rep["failed_conditions"] == "N/A"
    sc = rep["scores"]
    assert (
        sc["candidates"]["n"] == 4
        and sc["candidates"]["max"] == 80.0
        and sc["candidates_by_side"]["LONG"]["n"] == 3
    )
    assert sc["armed"]["n"] == 2 and sc["executed"]["n"] == 1 and sc["near_misses"]["n"] == 1
    t = rep["shadow_trades"]
    assert t["n"] == 2 and t["win_rate"] == 0.5 and t["net_usd_total"] == pytest.approx(-1.5)
    assert t["profit_factor"] == pytest.approx(6.0 / 7.5) and t["max_dd_usd"] == pytest.approx(7.5)
    assert t["net_r_by_family"]["A"] == 0.8 and t["net_r_by_family"]["C"] == "N/A"
    assert t["net_r_by_side"]["SHORT"] == -1.0 and t["exit_reasons"] == {"TP2": 1, "STOP": 1}
    assert rep["outcomes"]["mean_return_bps_by_kind_horizon"]["CANDIDATE@5m"]["mean_bps"] == 15.0
    assert rep["demo"] == "N/A" and rep["execution_modes"] == ["SHADOW"]
    assert rep["freeze"]["strategy_hash"] == "abc123" and rep["duration_days"] == pytest.approx(
        120 / 1440
    )
    assert rep["rates_per_day"]["executed"] == pytest.approx(1 / (120 / 1440))
    assert set(rep["daily"]) == {"2026-10-13"} and rep["daily"]["2026-10-13"]["candidates"] == 4
    md = render_full_history(rep)
    assert "N/A" in md and "## 5. Shadow trades" in md and "SHADOW ONLY" in md


def test_v10_style_full_history_metrics_with_demo(tmp_path: Path) -> None:
    root = tmp_path / "forward_v10x"
    journals, dj, dt = _v10_style_root(root)
    fz = tmp_path / "freeze.json"
    fz.write_text(
        json.dumps({"strategy_hash": "abc123", "frozen_at": "x", "observation_start": "y"})
    )
    spec = _spec(
        "v10x",
        root,
        journals,
        Path("reports/test_v10x"),
        fz,
        demo_journal=dj,
        demo_trades=dt,
        demo_state=root / "demo" / "strategy_state.json",
        execution_modes=("SHADOW", "DEMO"),
    )
    rep = full_history(spec, equity=5000.0)
    f = rep["funnel"]
    assert f["candidates"] == "N/A"  # V10 has no candidate journal: never inferred
    assert f["armed"] == 1 and f["triggers_journaled"] == 1 and f["triggers_counter"] == 1
    assert (
        f["invalidated"] == 1 and f["expired"] == "N/A" and f["executed"] == 1 and f["blocked"] == 1
    )
    assert rep["scores"]["candidates"] == "N/A" and rep["scores"]["armed"] == "N/A"
    assert rep["failed_conditions"] == {"price moved away": 1, "cond:absorb": 1}
    assert rep["blockers"] == {"POSITION_OPEN": 1}
    assert rep["other_signal_journal_kinds"] == {"DAILY_SAFETY_BLOCK": 1}
    assert rep["shadow_trades"]["n"] == 1 and rep["by_family_side"]["shadow_closed"]["A-LONG"] == 1
    d = rep["demo"]
    assert isinstance(d, dict) and d["journal_records"] == 2
    assert d["activation_events"][0]["kind"] == "STRATEGY_DEMO_V10_ACTIVATED"
    assert (
        d["trades"]["n"] == 1
        and d["trades"]["net_usd_total"] == 1.5
        and d["trades"]["net_r_mean"] == "N/A"
    )
    md = render_full_history(rep)
    assert "## 7. Demo execution" in md and "closed demo trades" in md


def test_missing_and_empty_journals_are_na_not_errors(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    spec = _spec(
        "vx",
        root,
        {
            "episodes": root / "episodes.jsonl",
            "signals": root / "signals.jsonl",
            "nearmiss": root / "nm.jsonl",
            "shadow_trades": root / "sh.jsonl",
            "outcomes": root / "o.jsonl",
        },
        Path("reports/test_empty"),
        tmp_path / "nofreeze.json",
    )
    rep = full_history(spec, None)
    assert rep["funnel"]["armed"] == 0 and rep["funnel"]["candidates"] == "N/A"
    assert (
        rep["observation_start"] == "N/A"
        and rep["duration_days"] == "N/A"
        and rep["state_counters"] == "N/A"
    )
    assert rep["freeze"]["present"] is False and rep["shadow_trades"] == {"n": 0}
    render_full_history(rep)


# ----------------------------------------------------------------------------- immutability proofs
def test_report_changes_no_journal_state_or_freeze_and_writes_only_under_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    root = data / "forward_x"
    journals = _v11_style_root(root)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    fz = manifests / "freeze.json"
    fz.write_text(
        json.dumps(
            {
                "strategy_hash": "abc123",
                "frozen_at": "x",
                "observation_start": "y",
                "observation_start_ms": T0,
            }
        )
    )
    before_data = _tree_hashes(data)
    before_manifests = _tree_hashes(manifests)
    state_before = (root / "state.json").read_text()
    spec = _spec("vx", root, journals, Path("reports/forward_x"), fz)
    js, md, rep = report_cli.write_full_history(spec, 5000.0)
    js2, md2, rep2 = report_cli.write_full_history(spec, 5000.0)  # idempotent re-run
    assert _tree_hashes(data) == before_data  # journals, state.json, bars: byte-identical
    assert _tree_hashes(manifests) == before_manifests  # freeze untouched
    assert (root / "state.json").read_text() == state_before
    assert all(verify_chain(p)["ok"] for p in journals.values())
    created = (
        {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
        - set(f"data/{k}" for k in before_data)
        - {"manifests/freeze.json"}
    )
    assert created and all(c.startswith("reports/forward_x/") for c in created), created
    assert {
        js.name,
        md.name,
        js2.name,
        md2.name,
        "latest_full_history.json",
        "latest_full_history.md",
    } <= {Path(c).name for c in created}
    assert rep["funnel"] == rep2["funnel"] and rep["read_only"] is True
    with pytest.raises(RuntimeError):  # a reports dir outside reports/<generation>/ is refused
        report_cli.write_full_history(
            _spec("vx", root, journals, Path("data/forward_x/out"), fz), None
        )
    with pytest.raises(RuntimeError):
        report_cli.write_full_history(_spec("vx", root, journals, Path("reports"), fz), None)


def test_real_generation_specs_leave_repo_artefacts_untouched_and_make_no_exchange_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three real spec builders + report writer, with the network forbidden, must not change any
    journal, state, freeze manifest or strategy hash of the repository, and must write only under
    reports/<generation>/ (redirected to a temp cwd here)."""
    data_dir = tmp_path / "data"
    for sub in ("forward_v10", "forward_v11", "forward_v11_warm"):
        (data_dir / "btc" / sub / "journals").mkdir(parents=True)
    _v11_style_root(data_dir / "btc" / "forward_v11_warm")
    monkeypatch.setenv("BTC_DATA_DIR", str(data_dir))
    import btc_swing.core.settings as settings_mod

    if hasattr(settings_mod.get_settings, "cache_clear"):
        settings_mod.get_settings.cache_clear()  # type: ignore[attr-defined]

    def _no_net(*a: Any, **k: Any) -> None:
        raise AssertionError("network call attempted by the reporting layer")

    monkeypatch.setattr(httpx.Client, "send", _no_net)
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    freezes = [
        ROOT / "manifests" / n
        for n in (
            "v10_forward_freeze.json",
            "v11_forward_freeze.json",
            "v11_warm_forward_freeze.json",
        )
    ]
    configs = [
        ROOT / "config" / n
        for n in ("btc_swing_v10.yaml", "btc_swing_v11.yaml", "btc_swing_v11_warm.yaml")
    ]
    before = {str(p): _sha(p) for p in freezes + configs if p.exists()}
    data_before = _tree_hashes(data_dir)
    specs = []
    for build, cfg in ((spec_v10, configs[0]), (spec_v11, configs[1]), (spec_v11w, configs[2])):
        spec, eq = build(cfg, False)
        specs.append((spec, eq))
    hashes_before = [s.strategy_hash for s, _ in specs]
    # write under a temporary cwd so the repo's reports/ stays clean during the test
    monkeypatch.chdir(tmp_path)
    for spec, eq in specs:
        js, _md, rep = report_cli.write_full_history(spec, eq)
        assert js.parts[0] == "reports" and js.parts[1].startswith("forward_v1")
        assert rep["config_strategy_hash"] == spec.strategy_hash
        if spec.name == "v11w":
            assert rep["funnel"]["candidates"] == 4 and rep["shadow_trades"]["n"] == 2
        if spec.name == "v10":
            assert rep["execution_modes"] == ["SHADOW", "DEMO"] and isinstance(rep["demo"], dict)
        else:
            assert rep["demo"] == "N/A"
    assert {str(p): _sha(p) for p in freezes + configs if p.exists()} == before
    assert _tree_hashes(data_dir) == data_before
    monkeypatch.chdir(ROOT)  # the generation configs are repo-relative
    specs_after = [
        build(cfg, False)[0].strategy_hash
        for build, cfg in ((spec_v10, configs[0]), (spec_v11, configs[1]), (spec_v11w, configs[2]))
    ]
    assert specs_after == hashes_before
    if hasattr(settings_mod.get_settings, "cache_clear"):
        settings_mod.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_reporting_layer_imports_no_execution_or_exchange_code() -> None:
    src = "".join(
        p.read_text() for p in sorted((ROOT / "btc_swing" / "forward_report").glob("*.py"))
    )
    for tok in (
        "httpx",
        "websockets",
        "btc_swing.v5.demo.client",
        "BybitDemoClient",
        "btc_swing.v10.execution",
        "btc_swing.v10.activation",
        "btc_swing.v10.engine",
        "btc_swing.v10.runner",
        "btc_swing.v11.live",
        "btc_swing.v11.runner",
        "btc_swing.v11_warm.live",
        "btc_swing.v11_warm.runner",
        "place_order",
        "api-demo",
        "api.bybit",
        "BYBIT_DEMO_API",
    ):
        assert tok not in src, tok
    # the only writes in the layer are the report files
    assert src.count(".write_text(") == 4 and "open(" not in src.replace(
        'path.open("r"', ""
    ).replace('.open("r"', "")
