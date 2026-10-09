"""V5.1 forward observation on the SAME public data stream as V5, with its own state.

Shared, read-only for V5.1: the raw Bybit files, the processor offsets and the derived 5m bars
(`<data>/btc/forward/...`, owned by the V5 pipeline, which keeps running unchanged) and the archive
trade seed. Own, under `<data>/btc/forward_v51/`: historical warm-up seeds (OI, premium, funding),
signal/outcome journals, paper ledger, cycle log, demo journals/state. The V5 journals are never
written by V5.1. The runner evaluates V5 first (it processes the raw data) and then V5.1 on the
assembled bars; the V5.1 cycle never processes raw data, extends the archive seed, or backfills."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.v5.engine import V5Engine
from btc_swing.v5.events import V5Detector, build_v5_detectors
from btc_swing.v5.features import FeatureFrame
from btc_swing.v5.forward.config import ForwardConfig, ForwardPaths
from btc_swing.v5.forward.pipeline import (
    Assembled,
    _append_jsonl,
    _clean,
    _iso,
    assemble,
    persist_new_signals,
    persist_outcomes,
    update_paper,
)
from btc_swing.v5.forward.raw import RawProcessor
from btc_swing.v5.stage_a import scan_events
from btc_swing.v51.config import V51Config
from btc_swing.v51.features import Validity, bar_validity, build_feature_frame_v51, validity_summary
from btc_swing.v51.history import split_warmup

SEED_KINDS = ("oi", "premium", "funding")


class V51Paths:
    """Same attribute names as `ForwardPaths` so every read-only helper works unchanged."""

    def __init__(self, data_dir: Path, symbol: str) -> None:
        self.v5 = ForwardPaths(data_dir, symbol)
        self.root = data_dir / "btc" / "forward_v51"
        # shared with V5 (read-only here)
        self.raw = self.v5.raw
        self.bars_dir = self.v5.bars_dir
        self.seed = self.v5.seed
        self.seed_bars = self.v5.seed_bars
        self.seed_manifest = self.v5.seed_manifest
        self.processor_state = self.v5.processor_state
        self.run_pid = self.v5.run_pid
        self.collector_pid = self.v5.collector_pid
        # own
        self.derived = self.root / "derived"
        self.signals = self.root / "signals"
        self.paper = self.root / "paper"
        self.logs = self.root / "logs"
        self.seeds = self.root / "seeds"
        self.signals_file = self.signals / "signals.jsonl"
        self.outcomes_file = self.signals / "outcomes.jsonl"
        self.paper_trades = self.paper / "paper_trades.jsonl"
        self.paper_state = self.paper / "paper_state.json"
        self.cycle_log = self.logs / "cycles.jsonl"

    def ensure(self) -> None:
        """Create the V5.1 directories (writing commands only; read-only commands never do)."""
        for d in (self.derived, self.signals, self.paper, self.logs, self.seeds):
            d.mkdir(parents=True, exist_ok=True)

    def seed_dir(self, kind: str) -> Path:
        if kind not in SEED_KINDS:
            raise ValueError(kind)
        return self.seeds / kind


@dataclass
class V51Context:
    fcfg: ForwardConfig
    cfg: V51Config
    paths: V51Paths
    start_ms: int  # V5.1 observation start (freeze time)
    v5_start_ms: int  # the data stream's observation start (V5 freeze): seeds end before it

    @property
    def processor(self) -> RawProcessor:
        # identical to the V5 processor (same raw, bars, offsets): a no-op when V5 ran first
        return RawProcessor(
            self.paths.raw,
            self.paths.bars_dir,
            self.paths.processor_state,
            self.fcfg.bars.book_depth_pct,
            self.v5_start_ms,
        )


@dataclass
class Assembled51:
    base: Assembled
    valid: np.ndarray
    seeds: dict[str, Any]


@dataclass
class Built51:
    series: MultiTfSeries
    aux: AuxSeries
    ff: FeatureFrame
    detectors: list[V5Detector]
    last_close_ms: int
    validity: Validity


def load_seed_table(paths: V51Paths, kind: str) -> pl.DataFrame:
    p = paths.seed_dir(kind) / f"{kind}.parquet"
    return pl.read_parquet(p) if p.exists() else pl.DataFrame()


def seed_verification(paths: V51Paths, kind: str) -> dict[str, Any] | None:
    p = paths.seed_dir(kind) / "verification.json"
    return json.loads(p.read_text()) if p.exists() else None


def assemble_v51(ctx: V51Context) -> Assembled51:
    """The V5 assembly (bars, gap flags, live aux rows) + warm-up seeds strictly before the V5
    observation start + the bar validity. Nothing after the V5 start is ever backfilled."""
    a = assemble(ctx)  # type: ignore[arg-type]
    valid = bar_validity(a.rows)
    seeds: dict[str, Any] = {}
    cut = ctx.v5_start_ms
    dq = ctx.cfg.data_quality
    # --- open interest
    oi = load_seed_table(ctx.paths, "oi")
    if dq.oi_seed.enabled and not oi.is_empty():
        warm, _ = split_warmup(oi, "time_ms", cut)
        seed_m = warm.select(
            "time_ms",
            "open_interest",
            pl.lit(math.nan).alias("open_interest_value"),
            pl.lit(math.nan).alias("long_short_ratio_accounts"),
            pl.lit(math.nan).alias("top_trader_long_short_ratio_positions"),
            pl.lit(math.nan).alias("taker_long_short_volume_ratio"),
        )
        live_m = a.metrics if a.metrics is not None else None
        a.metrics = (
            pl.concat([seed_m, live_m.filter(pl.col("time_ms") >= cut)], how="vertical_relaxed")
            if live_m is not None and not live_m.is_empty()
            else seed_m
        ).sort("time_ms")
        seeds["oi"] = {
            "rows": warm.height,
            "last": _iso(int(warm["time_ms"].max())) if warm.height else None,  # type: ignore[arg-type]
        }
    # --- premium (mark/index - 1 per bar)
    pr = load_seed_table(ctx.paths, "premium")
    if dq.premium_seed.enabled and not pr.is_empty():
        warm, _ = split_warmup(pr, "close_time_ms", cut)
        seed_p = warm.select("open_time_ms", "close_time_ms", "close")
        live_p = a.premium
        a.premium = (
            pl.concat(
                [seed_p, live_p.filter(pl.col("close_time_ms") >= cut)], how="vertical_relaxed"
            )
            if live_p is not None and not live_p.is_empty()
            else seed_p
        ).sort("open_time_ms")
        seeds["premium"] = {"rows": warm.height}
    # --- funding events
    fu = load_seed_table(ctx.paths, "funding")
    if dq.funding_seed.enabled and not fu.is_empty():
        warm, _ = split_warmup(fu, "time_ms", cut)
        seed_f = warm.select("time_ms", "funding_rate")
        live_f = a.funding
        a.funding = (
            pl.concat([seed_f, live_f.filter(pl.col("time_ms") >= cut)], how="vertical_relaxed")
            if live_f is not None and not live_f.is_empty()
            else seed_f
        ).sort("time_ms")
        seeds["funding"] = {"rows": warm.height}
    return Assembled51(a, valid, seeds)


def build_v51(ctx: V51Context, a51: Assembled51) -> Built51:
    a = a51.base
    aux = AuxSeries.build(a.funding, a.metrics, a.premium, a.mark, ctx.cfg.data.latency_minutes)
    series = MultiTfSeries(a.bars, ctx.cfg.indicators, 5)
    ff, val = build_feature_frame_v51(series, aux, ctx.cfg, a.extra, a51.valid)
    dets = build_v5_detectors(ctx.cfg, ff)
    return Built51(series, aux, ff, dets, int(series.base.close_ms[-1]), val)


def run_cycle_v51(
    ctx: V51Context, now_ms: int | None = None, on_result: Any | None = None
) -> dict[str, Any]:
    """One V5.1 evaluation on the already-processed bars: assemble (+seeds, validity) -> V5.1
    features -> frozen detectors -> signals -> frozen engine -> paper ledger -> outcomes."""
    t0 = time.time()
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    ctx.paths.ensure()
    out: dict[str, Any] = {"cycle_at": _iso(now), "version": "V5.1"}
    a51 = assemble_v51(ctx)
    a = a51.base
    b = build_v51(ctx, a51)
    k = b.ff.idx_at(b.last_close_ms)
    out.update(
        {
            "bars_total": a.bars.height,
            "bars_seed": a.n_seed,
            "bars_forward": a.n_forward,
            "bars_gap_filled": a.n_gap,
            "gaps": a.gaps[-5:],
            "last_bar_close": _iso(b.last_close_ms),
            "seeds": a51.seeds,
            "validity": validity_summary(b.validity, k, ctx.cfg.features.z_window_bars),
        }
    )
    events = scan_events(b.ff, b.detectors, ctx.cfg, ctx.start_ms, b.last_close_ms + 1)
    new_sigs = persist_new_signals(ctx, b, events, a.rows)  # type: ignore[arg-type]
    out["events_since_start"] = events.height
    out["new_signals"] = [
        {
            "signal_id": s["signal_id"],
            "family": s["family"],
            "side": s["side"],
            "strength": s["strength"],
        }
        for s in new_sigs
    ]
    eng = V5Engine(ctx.cfg, a.bars, a.funding, None, b.aux, b.series, b.ff)
    res = eng.run(ctx.start_ms, b.last_close_ms + 1, notes={"stream": "forward_paper_v51"})
    out["paper"] = update_paper(ctx, res.trades, b.last_close_ms, float(b.series.base.close[-1]))  # type: ignore[arg-type]
    if on_result is not None:
        try:
            out["demo"] = on_result(a, b, res)
        except Exception as e:  # never stops the observation
            out["demo"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    out["episodes_since_start"] = res.episodes.height
    out["blocked"] = res.blocked
    out["new_outcomes"] = len(persist_outcomes(ctx, b, eng))  # type: ignore[arg-type]
    out["elapsed_s"] = round(time.time() - t0, 2)
    _append_jsonl(
        ctx.paths.cycle_log,
        [
            {k2: _clean(v) for k2, v in out.items() if k2 != "paper"}
            | {
                "paper_equity_mtm": out["paper"]["equity_mtm"],
                "paper_closed": out["paper"]["closed_trades"],
                "paper_open": out["paper"]["open_position"] is not None,
            }
        ],
    )
    return out
