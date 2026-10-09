"""`btc-swing v51 ...`: BTC_V5_1_DATA_QUALITY_FIX forward observation, historical seeds, read-only
diagnostics and the owner-gated STRATEGY_DEMO activation. V5 commands are untouched."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from btc_swing.core.settings import get_settings

v51_app = typer.Typer(
    no_args_is_help=True,
    help="BTC V5.1 DATA QUALITY FIX: frozen V5 rules on valid observations only (paper / Bybit DEMO)",
)
forward51 = typer.Typer(help="V5.1 forward observation (shares the V5 collector and bars)")
seed51 = typer.Typer(help="Historical warm-up seeds from Bybit PUBLIC history (warm-up only)")
demo51 = typer.Typer(help="V5.1 STRATEGY_DEMO (separate journals; owner-gated activation)")
v51_app.add_typer(forward51, name="forward")
v51_app.add_typer(seed51, name="seed")
v51_app.add_typer(demo51, name="demo")
console = Console()

FWD_CFG = Path("config/btc_swing_v5_forward.yaml")
V51_CFG = Path("config/btc_swing_v5_1.yaml")
DEMO_CFG = Path("config/btc_swing_v5_demo.yaml")
REPORTS_V51 = Path("reports/forward_v51")
DAY_MS = 86_400_000


def _data_dir() -> Path:
    return get_settings().data_dir


def _iso_ms(x: Any) -> str | None:
    return None if x is None else datetime.fromtimestamp(int(float(x)) / 1000, tz=UTC).isoformat()


def _v5_ctx(forward_config: Path = FWD_CFG) -> Any:
    from btc_swing.v5.forward.config import ForwardPaths, load_forward_config, load_frozen_v5
    from btc_swing.v5.forward.freeze import check_freeze
    from btc_swing.v5.forward.pipeline import ForwardContext

    fcfg = load_forward_config(forward_config)
    cfg = load_frozen_v5(fcfg)
    rec = check_freeze(cfg)
    return ForwardContext(
        fcfg, cfg, ForwardPaths(_data_dir(), fcfg.symbol), int(rec["observation_start_ms"])
    )


def v51_context(
    forward_config: Path = FWD_CFG, v51_config: Path = V51_CFG, require_freeze: bool = True
) -> Any:
    from btc_swing.v5.forward.config import load_forward_config, load_frozen_v5
    from btc_swing.v5.forward.freeze import check_freeze
    from btc_swing.v51.config import assert_strategy_identical, load_v51_config
    from btc_swing.v51.forward import V51Context, V51Paths
    from btc_swing.v51.freeze import check_v51_freeze, load_v51_freeze

    fcfg = load_forward_config(forward_config)
    v5 = load_frozen_v5(fcfg)
    rec5 = check_freeze(v5)
    cfg = load_v51_config(v51_config)
    assert_strategy_identical(cfg, v5)
    rec51 = load_v51_freeze()
    if rec51 is None:
        if require_freeze:
            raise typer.BadParameter("no V5.1 freeze yet: run `btc-swing v51 forward freeze` first")
        start = int(rec5["observation_start_ms"])  # provisional: read-only commands only
    else:
        check_v51_freeze(cfg)
        start = int(rec51["observation_start_ms"])
    return V51Context(
        fcfg, cfg, V51Paths(_data_dir(), fcfg.symbol), start, int(rec5["observation_start_ms"])
    )


def v51_runtime(forward_config: Path = FWD_CFG) -> Any:
    """The V5.1 pipeline for the runner (None until the V5.1 freeze exists)."""
    from btc_swing.v5.demo.ids import V51_STRATEGY_PREFIX
    from btc_swing.v5.demo.runtime import build_executor, demo_cycle
    from btc_swing.v5.forward.runner import V51Runtime
    from btc_swing.v51.config import PARENT_V5_CONFIG_HASH
    from btc_swing.v51.forward import run_cycle_v51
    from btc_swing.v51.freeze import check_v51_freeze, load_v51_freeze

    if load_v51_freeze() is None or not V51_CFG.exists():
        return None
    ctx = v51_context(forward_config)
    return V51Runtime(
        ctx,
        REPORTS_V51,
        partial(
            build_executor,
            stream_hash=PARENT_V5_CONFIG_HASH,
            freeze_check=check_v51_freeze,
            link_prefix=V51_STRATEGY_PREFIX,
        ),
        partial(demo_cycle, freeze_check=check_v51_freeze),
        run_cycle_v51,
    )


# ----------------------------------------------------------------------------- forward
@forward51.command("freeze")
def forward_freeze(forward_config: Path = FWD_CFG, v51_config: Path = V51_CFG) -> None:
    """Create manifests/v5_1_forward_freeze.json (V5.1 hash, V5 parent hash, rule versions, the
    V5.1 observation start = now). Idempotent; never touches the V5 freeze."""
    from btc_swing.v5.forward.config import load_forward_config, load_frozen_v5
    from btc_swing.v5.forward.freeze import load_freeze
    from btc_swing.v51.config import load_v51_config
    from btc_swing.v51.freeze import write_v51_freeze

    fcfg = load_forward_config(forward_config)
    rec = write_v51_freeze(load_v51_config(v51_config), load_frozen_v5(fcfg), fcfg, load_freeze())
    console.print_json(json.dumps({k: v for k, v in rec.items() if not k.endswith("_yaml")}))


@forward51.command("cycle")
def forward_cycle(forward_config: Path = FWD_CFG) -> None:
    """One V5.1 evaluation cycle on the already-processed bars (V5 state untouched)."""
    from btc_swing.v51.forward import run_cycle_v51

    out = run_cycle_v51(v51_context(forward_config))
    console.print_json(
        json.dumps(
            {k: v for k, v in out.items() if k != "paper"}
            | {"paper": {k: v for k, v in out["paper"].items() if k != "open_position"}},
            default=str,
        )
    )


@forward51.command("coverage")
def forward_coverage(forward_config: Path = FWD_CFG) -> None:
    """Missing periods since the V5.1 start (never backfilled) and duplicate counts."""
    from btc_swing.v5.forward.ops import coverage

    console.print_json(json.dumps(coverage(v51_context(forward_config)), default=str))


@forward51.command("host-status")
def forward_host_status(forward_config: Path = FWD_CFG) -> None:
    """Authoritative-host status seen from the V5.1 pipeline (own signals/paper/demo)."""
    from btc_swing.v5.forward.ops import host_status_text

    typer.echo(host_status_text(v51_context(forward_config, require_freeze=False), _data_dir()))


@forward51.command("signal-diagnostic")
def forward_signal_diagnostic(
    as_json: bool = typer.Option(False, "--json"), forward_config: Path = FWD_CFG
) -> None:
    """READ-ONLY V5.1 diagnostic: FEATURE | VALID OBS | WARM | CURRENT | Z | QUALITY, then every
    family/side with DATA QUALITY. Writes nothing, no order, no API call."""
    from btc_swing.v51.diagnostic import render_v51, signal_diagnostic_v51

    d = signal_diagnostic_v51(v51_context(forward_config, require_freeze=False))
    typer.echo(json.dumps(d, default=str, indent=1) if as_json else render_v51(d))


@forward51.command("compare-diagnostic")
def forward_compare(
    as_json: bool = typer.Option(False, "--json"), forward_config: Path = FWD_CFG
) -> None:
    """READ-ONLY: frozen V5 diagnostic vs V5.1 diagnostic on the same latest bar, with the cause
    of every difference."""
    from btc_swing.v5.forward.diagnostic import signal_diagnostic
    from btc_swing.v51.diagnostic import (
        compare_diagnostics,
        render_comparison,
        signal_diagnostic_v51,
    )

    now = int(datetime.now(UTC).timestamp() * 1000)
    d5 = signal_diagnostic(_v5_ctx(forward_config), now)
    d51 = signal_diagnostic_v51(v51_context(forward_config, require_freeze=False), now)
    c = compare_diagnostics(d5, d51)
    typer.echo(json.dumps(c, default=str, indent=1) if as_json else render_comparison(c))


# ----------------------------------------------------------------------------- seeds
def _seed_window(ctx: Any, days: int) -> tuple[int, int]:
    now = int(datetime.now(UTC).timestamp() * 1000)
    return ctx.v5_start_ms - days * DAY_MS, now


def _live_bars(ctx: Any) -> Any:
    from btc_swing.v5.forward.raw import load_forward_bars

    return load_forward_bars(ctx.paths.bars_dir)


def _write_verification(ctx: Any, kind: str, rep: dict[str, Any]) -> None:
    (ctx.paths.seed_dir(kind) / "verification.json").write_text(
        json.dumps(rep, indent=1, sort_keys=True, default=str)
    )


@seed51.command("oi")
def seed_oi(forward_config: Path = FWD_CFG, days: int | None = None) -> None:
    """Bybit PUBLIC GET /v5/market/open-interest (intervalTime=5min), paginated, persisted as
    immutable raw pages + table with source/retrieved_at. Warm-up rows end strictly before the V5
    observation start; later rows are used only to verify timestamp/unit alignment against the
    live collector rows. No signal, trade or outage fill is ever derived from it."""
    from btc_swing.v51 import history as h

    ctx = v51_context(forward_config, require_freeze=False)
    sc = ctx.cfg.data_quality.oi_seed
    start, end = _seed_window(ctx, days or sc.days_before_v5_start)
    cl = h.BybitPublicHistory()
    store = h.SeedStore(ctx.paths.seed_dir("oi"), "oi")
    tbl = h.fetch_oi_seed(cl, store, ctx.fcfg.symbol, start, end, sc.interval)
    cl.close()
    warm, verif = h.split_warmup(tbl, "time_ms", ctx.v5_start_ms)
    rep = h.verify_oi_alignment(verif, _live_bars(ctx), sc.max_rel_diff_vs_live)
    rep |= {
        "requests": cl.requests,
        "warmup_rows": warm.height,
        "warmup_last": _iso_ms(warm["time_ms"].max()) if warm.height else None,
        "v5_observation_start_ms": ctx.v5_start_ms,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    _write_verification(ctx, "oi", rep)
    console.print_json(json.dumps(rep, default=str))
    if not rep["ok"]:
        raise typer.Exit(code=1)


@seed51.command("premium")
def seed_premium(forward_config: Path = FWD_CFG, days: int | None = None) -> None:
    """mark-price-kline + index-price-kline (5m) -> premium = mark_close/index_close - 1, the live
    row's own definition; verified against live rows on the overlap. Warm-up only."""
    from btc_swing.v51 import history as h

    ctx = v51_context(forward_config, require_freeze=False)
    sc = ctx.cfg.data_quality.premium_seed
    start, end = _seed_window(ctx, days or sc.days_before_v5_start)
    cl = h.BybitPublicHistory()
    store = h.SeedStore(ctx.paths.seed_dir("premium"), "premium")
    tbl = h.fetch_premium_seed(cl, store, ctx.fcfg.symbol, start, end, sc.interval)
    cl.close()
    warm, verif = h.split_warmup(tbl, "close_time_ms", ctx.v5_start_ms)
    rep = h.verify_premium_alignment(verif, _live_bars(ctx), sc.max_rel_diff_vs_live)
    rep |= {
        "requests": cl.requests,
        "warmup_rows": warm.height,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    _write_verification(ctx, "premium", rep)
    console.print_json(json.dumps(rep, default=str))
    if not rep["ok"]:
        raise typer.Exit(code=1)


@seed51.command("funding")
def seed_funding(forward_config: Path = FWD_CFG, days: int | None = None) -> None:
    """/v5/market/funding/history: the settled rate at the settlement time (the live pipeline's
    funding event definition). Warm-up only."""
    from btc_swing.v51 import history as h
    from btc_swing.v51.forward import assemble_v51

    ctx = v51_context(forward_config, require_freeze=False)
    sc = ctx.cfg.data_quality.funding_seed
    start, end = _seed_window(ctx, sc.days_before_v5_start if days is None else days)
    cl = h.BybitPublicHistory()
    store = h.SeedStore(ctx.paths.seed_dir("funding"), "funding")
    tbl = h.fetch_funding_seed(cl, store, ctx.fcfg.symbol, start, end)
    cl.close()
    warm, verif = h.split_warmup(tbl, "time_ms", ctx.v5_start_ms)
    live_f = assemble_v51(ctx).base.funding
    live_only = live_f.filter(live_f["time_ms"] >= ctx.v5_start_ms) if live_f is not None else None
    rep = h.verify_funding_alignment(verif, live_only)
    rep |= {
        "requests": cl.requests,
        "warmup_rows": warm.height,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    _write_verification(ctx, "funding", rep)
    console.print_json(json.dumps(rep, default=str))


@seed51.command("status")
def seed_status(forward_config: Path = FWD_CFG) -> None:
    """Seed tables and their verification reports."""
    from btc_swing.v51.forward import SEED_KINDS, load_seed_table, seed_verification

    ctx = v51_context(forward_config, require_freeze=False)
    out = {}
    for kind in SEED_KINDS:
        t = load_seed_table(ctx.paths, kind)
        v = seed_verification(ctx.paths, kind) or {}
        col = "close_time_ms" if kind == "premium" else "time_ms"
        out[kind] = {
            "rows": t.height,
            "first": _iso_ms(t[col].min()) if t.height else None,
            "last": _iso_ms(t[col].max()) if t.height else None,
            "verification_ok": v.get("ok"),
            "verified_at": v.get("verified_at"),
        }
    console.print_json(json.dumps(out, default=str))


# ----------------------------------------------------------------------------- demo
def _dcfg(demo_config: Path) -> Any:
    from btc_swing.v5.demo.config import load_demo_config

    return load_demo_config(demo_config)


@demo51.command("status")
def demo_status_cmd(demo_config: Path = DEMO_CFG, forward_config: Path = FWD_CFG) -> None:
    """Read-only V5.1 demo status (no API call)."""
    from btc_swing.v5.demo.runtime import demo_status, demo_status_lines

    rows = demo_status_lines(
        demo_status(v51_context(forward_config, require_freeze=False), _dcfg(demo_config))
    )
    w = max(len(k) for k, _ in rows)
    typer.echo("\n".join(f"{k.ljust(w)}  {v}" for k, v in rows))


def v51_activation_gates(ctx: Any, d51: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """V5.1-specific gates on top of the V5 activation gates."""
    from btc_swing.v5.demo.activation import effective_activation
    from btc_swing.v51.config import EXPECTED_V51_CONFIG_HASH
    from btc_swing.v51.features import DETECTOR_Z
    from btc_swing.v51.freeze import load_v51_freeze

    def gate(ok: bool, detail: Any) -> dict[str, Any]:
        return {"ok": bool(ok), "detail": detail}

    rec = load_v51_freeze() or {}
    ft = {r["feature"]: r for r in d51.get("feature_table") or []}
    det = [ft.get(z, {}) for z in DETECTOR_Z]
    oi = ft.get("oi_chg_1h_z", {})
    vq = (d51.get("data_quality") or {}).get("vol_1h_z") or {}
    sv = d51.get("seed_verification") or {}
    v5_act = effective_activation(ctx.paths.v5.root)
    st5 = ctx.paths.v5.root / "demo" / "strategy_state.json"
    v5_state = json.loads(st5.read_text()) if st5.exists() else {}
    blockers = [
        b for b in d51["demo"]["global_blockers"] if not b.startswith("STRATEGY_DEMO not active")
    ]
    return {
        "V5.1 freeze present, pinned hash, start after the V5 start": gate(
            rec.get("v51_config_hash") == EXPECTED_V51_CONFIG_HASH
            and int(rec.get("observation_start_ms", 0)) >= ctx.v5_start_ms,
            {
                "hash": str(rec.get("v51_config_hash", ""))[:12],
                "start": rec.get("observation_start"),
            },
        ),
        "every detector input warm on valid observations": gate(
            bool(det) and all(r.get("warm") for r in det),
            {r.get("feature"): r.get("valid_obs") for r in det},
        ),
        "oi_chg_1h_z >= 2880 valid historical/live observations": gate(
            isinstance(oi.get("valid_obs"), int) and oi["valid_obs"] >= 2880, oi.get("valid_obs")
        ),
        "vol_1h_z numerically reachable (clean baseline)": gate(
            vq.get("unreachable") is False,
            {"btc_per_hour_for_z_1.0": vq.get("btc_per_hour_needed_for_vol_1h_z_1.0")},
        ),
        "current 1h window gap-free (no INVALID NOW detector input)": gate(
            all(not str(r.get("quality", "")).startswith("INVALID NOW") for r in det)
            and d51["gap_rows_last_12_bars"] == 0,
            {"gap_rows_last_12_bars": d51["gap_rows_last_12_bars"]},
        ),
        "OI seed alignment verified against the live feed": gate(
            (not ctx.cfg.data_quality.oi_seed.enabled) or bool((sv.get("oi") or {}).get("ok")),
            sv.get("oi", {}).get("n_overlap"),
        ),
        "V5 STRATEGY_DEMO inactive on this host and V5 demo flat": gate(
            v5_act is None
            and v5_state.get("position") is None
            and not v5_state.get("reconcile_required"),
            {
                "v5_active": v5_act is not None,
                "v5_position": (v5_state.get("position") or {}).get("status"),
            },
        ),
        "no current safety blocker": gate(not blockers, blockers),
    }


@demo51.command("activate")
def demo_activate(
    note: str = typer.Option(..., help="owner's activation note"),
    demo_config: Path = DEMO_CFG,
    forward_config: Path = FWD_CFG,
) -> None:
    """Owner-only V5.1 STRATEGY_DEMO switch: full test suite, fresh DEMO_EXECUTION_PREFLIGHT and
    FORWARD_HOST_PREFLIGHT, PASSED smoke, V5.1 freeze, integrity, reconciliation, sizing, PLUS the
    V5.1 data-quality gates (inputs warm on valid observations, OI >= 2880, vol_1h_z reachable,
    gap-free current window, OI seed verified, V5 demo inactive and flat). Only if EVERY gate
    passes is STRATEGY_DEMO_ACTIVATED appended to the V5.1 strategy journal."""
    from btc_swing.v5.demo.activation import activation_gates, record_activation
    from btc_swing.v5.demo.journal import HashChainJournal, verify_chain
    from btc_swing.v5.demo.preflight import run_preflight, write_preflight
    from btc_swing.v5.demo.runtime import DemoPaths, latest_passed_smoke
    from btc_swing.v5.forward.freeze import load_freeze
    from btc_swing.v5.forward.ops import coverage
    from btc_swing.v5.forward.raw import load_forward_bars
    from btc_swing.v51.config import EXPECTED_V51_CONFIG_HASH, PARENT_V5_CONFIG_HASH
    from btc_swing.v51.diagnostic import signal_diagnostic_v51
    from btc_swing.v51.freeze import load_v51_freeze

    ctx = v51_context(forward_config)
    dcfg = _dcfg(demo_config)
    dp = DemoPaths(ctx.paths.root)
    typer.echo("== test suite (before activation)")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=False,
    )
    tests = {
        "ok": r.returncode == 0,
        "summary": (r.stdout.strip().splitlines() or ["(no output)"])[-1],
    }
    typer.echo(f"   {tests['summary']}")
    typer.echo(
        "== fresh preflight (DEMO_EXECUTION_PREFLIGHT + FORWARD_HOST_PREFLIGHT, V5 data stream)"
    )
    bars = load_forward_bars(ctx.paths.bars_dir)
    px = float(bars["close"].drop_nans()[-1]) if bars.height else None
    pf = run_preflight(
        dcfg,
        PARENT_V5_CONFIG_HASH,
        load_freeze(),
        ctx.paths.run_pid,
        HashChainJournal(dp.root / "preflight_journal.jsonl", "preflight_read_only_v51"),
        px,
    )
    typer.echo(f"   report {write_preflight(pf)}")
    typer.echo("== V5.1 read-only diagnostic")
    d51 = signal_diagnostic_v51(ctx)
    rec51 = load_v51_freeze() or {}
    state = json.loads(dp.state.read_text()) if dp.state.exists() else {}
    gates = activation_gates(
        tests=tests,
        preflight=pf,
        smoke=latest_passed_smoke(),
        v5_hash=ctx.cfg.config_hash,
        freeze={
            "v5_config_hash": rec51.get("v51_config_hash"),
            "observation_start_ms": rec51.get("observation_start_ms"),
            "observation_start": rec51.get("observation_start"),
        },
        start_ms=ctx.start_ms,
        coverage=coverage(ctx),
        chains={
            "v51_strategy_journal": verify_chain(dp.strategy_journal),
            "smoke_journal": verify_chain(DemoPaths(ctx.paths.v5.root).smoke_journal),
            "authority": verify_chain(ctx.paths.v5.root / "host" / "authority.jsonl"),
        },
        demo_state=state,
        reference_equity=dcfg.reference_equity_usdt,
        risk_per_trade=dcfg.risk_per_trade,
        frozen_risk=ctx.cfg.risk.risk_per_trade,
        config_mode=dcfg.mode.value,
        expected_v5_hash=EXPECTED_V51_CONFIG_HASH,
        expected_start_ms=int(rec51.get("observation_start_ms", -1)),
    )
    gates = {**{f"[V5.1] {k}": v for k, v in v51_activation_gates(ctx, d51).items()}, **gates}
    failed = [k for k, v in gates.items() if not v["ok"]]
    for k, v in gates.items():
        typer.echo(
            f"   [{'PASS' if v['ok'] else 'FAIL'}] {k}: {json.dumps(v['detail'], default=str)[:200]}"
        )
    if failed:
        console.print_json(
            json.dumps({"status": "NOT ACTIVATED", "failed_gates": failed, "mode": "DISABLED"})
        )
        raise typer.Exit(code=1)
    rec = record_activation(ctx.paths.root, gates, note, version="V5.1")
    console.print_json(
        json.dumps(
            {
                "status": "STRATEGY_DEMO_ACTIVATED (V5.1)",
                "activated_at": rec["activated_at"],
                "host": rec["hostname"],
                "v51_config_hash": ctx.cfg.config_hash,
                "reference_equity_usdt": dcfg.reference_equity_usdt,
                "risk_per_trade": dcfg.risk_per_trade,
                "rule": rec["rule"],
            },
            default=str,
        )
    )


@demo51.command("deactivate")
def demo_deactivate(note: str = typer.Option(...), forward_config: Path = FWD_CFG) -> None:
    """Append STRATEGY_DEMO_DEACTIVATED to the V5.1 journal (refused while a position is open)."""
    from btc_swing.v5.demo.activation import record_deactivation
    from btc_swing.v5.demo.runtime import DemoPaths

    ctx = v51_context(forward_config)
    dp = DemoPaths(ctx.paths.root)
    st = json.loads(dp.state.read_text()) if dp.state.exists() else {}
    rec = record_deactivation(ctx.paths.root, note, st.get("position") is not None)
    console.print_json(
        json.dumps({"status": "STRATEGY_DEMO_DEACTIVATED (V5.1)", **rec}, default=str)
    )


@demo51.command("reconcile-ack")
def demo_reconcile_ack(
    note: str = typer.Option(...), demo_config: Path = DEMO_CFG, forward_config: Path = FWD_CFG
) -> None:
    """Clear a V5.1 reconciliation-required flag after a human check (journaled)."""
    rt = v51_runtime(forward_config)
    if rt is None:
        raise typer.BadParameter("no V5.1 freeze")
    ex = rt.build(rt.ctx, _dcfg(demo_config))
    ex.acknowledge_reconciliation(note)
    typer.echo("reconciliation acknowledged (V5.1)")
