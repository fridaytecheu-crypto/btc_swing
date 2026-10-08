"""Forward observation pipeline: raw -> 5m rows -> the FROZEN V5 feature frame, detectors and
engine -> prospective signal snapshots, matured outcomes and a virtual paper ledger.

Every cycle rebuilds the (small) forward series from the immutable raw data and re-runs the frozen,
deterministic, point-in-time engine from the observation start. Because every decision at T only
sees rows <= T, re-running on appended data reproduces the past exactly (the V5 truncation audit);
signal snapshots and closed paper trades are therefore APPENDED once and never rewritten, and the
pipeline asserts that a re-derived closed trade equals the stored one. No order is ever placed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.core.enums import Side
from btc_swing.features.context import AuxSeries
from btc_swing.features.view import MultiTfSeries
from btc_swing.v5.config import V5Config
from btc_swing.v5.engine import V5Engine, simulate_entry
from btc_swing.v5.events import V5Detector, build_v5_detectors
from btc_swing.v5.features import FEATURE_COLUMNS, FeatureFrame, V5Inputs, build_feature_frame
from btc_swing.v5.forward.config import ForwardConfig, ForwardPaths
from btc_swing.v5.forward.raw import MS_5M, RawProcessor, load_forward_bars
from btc_swing.v5.forward.seed import BybitSeedIngestor, load_seed_bars, seed_days
from btc_swing.v5.ingest import FLOW_COLUMNS
from btc_swing.v5.stage_a import scan_events

log = logging.getLogger(__name__)
DAY_MS = 86_400_000
BAR_COLS = [
    "open_time_ms",
    "close_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
]
CONTEXT_COLS = [
    "atr",
    "ema21_1h",
    "close_1h",
    "align_1h",
    "trend_4h",
    "swing_low_1h",
    "swing_high_1h",
    "rv_24h_z",
    "range_pos_48h",
]


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, np.generic):
        return _clean(v.item())
    return v


def _iso(ms: float | int) -> str:
    return datetime.fromtimestamp(float(ms) / 1000, tz=UTC).isoformat()


@dataclass
class ForwardContext:
    fcfg: ForwardConfig
    cfg: V5Config
    paths: ForwardPaths
    start_ms: int

    @property
    def processor(self) -> RawProcessor:
        return RawProcessor(
            self.paths.raw,
            self.paths.bars_dir,
            self.paths.processor_state,
            self.fcfg.bars.book_depth_pct,
            self.start_ms,
        )


@dataclass
class Assembled:
    bars: pl.DataFrame  # seed + forward, contiguous 5m grid, gap rows flagged
    rows: pl.DataFrame  # the wide forward table aligned to bars (NaN where seed-only)
    extra: V5Inputs
    funding: pl.DataFrame | None
    metrics: pl.DataFrame | None
    premium: pl.DataFrame | None
    mark: pl.DataFrame | None
    gaps: list[dict[str, Any]]
    n_seed: int
    n_forward: int
    n_gap: int


# ----------------------------------------------------------------------------- assembly
def _gap_fill(df: pl.DataFrame, max_gap: int) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """Contiguous 5m grid; missing bars up to `max_gap` carry the previous close with zero volume
    and gap_filled = 1 (row-based lags in the frozen feature frame need a regular grid). Longer
    gaps are left open and reported; nothing after such a gap is evaluated until warm again."""
    gaps: list[dict[str, Any]] = []
    if df.is_empty():
        return df, gaps
    t = df["open_time_ms"].to_numpy().astype(np.int64)
    out_rows: list[dict[str, Any]] = []
    prev = df.row(0, named=True)
    for i in range(1, len(t)):
        cur = df.row(i, named=True)
        missing = (t[i] - t[i - 1]) // MS_5M - 1
        if missing > 0:
            gaps.append(
                {
                    "from": _iso(t[i - 1] + MS_5M),
                    "to": _iso(t[i]),
                    "bars": int(missing),
                    "filled": bool(missing <= max_gap),
                }
            )
            if missing <= max_gap:
                for k in range(1, missing + 1):
                    r: dict[str, Any] = {
                        c: (
                            math.nan
                            if isinstance(prev[c], float)
                            else (0 if isinstance(prev[c], int) else None)
                        )
                        for c in df.columns
                    }
                    r.update(
                        {
                            "open_time_ms": int(t[i - 1] + k * MS_5M),
                            "close_time_ms": int(t[i - 1] + (k + 1) * MS_5M),
                            "open": prev["close"],
                            "high": prev["close"],
                            "low": prev["close"],
                            "close": prev["close"],
                            "volume": 0.0,
                            "quote_volume": 0.0,
                            "trades": 0,
                            "taker_buy_volume": 0.0,
                            "taker_buy_quote_volume": 0.0,
                            "gap_filled": 1.0,
                            "source": "gap",
                        }
                    )
                    for c in (
                        "n_buy",
                        "n_sell",
                        "buy_qty",
                        "sell_qty",
                        "buy_notional",
                        "sell_notional",
                        "big_buy_qty",
                        "big_sell_qty",
                        "big_buy_notional_100k",
                        "big_sell_notional_100k",
                        "max_trade_qty",
                    ):
                        if c in r:
                            r[c] = 0 if c in ("n_buy", "n_sell") else 0.0
                    out_rows.append(r)
        prev = cur
    if not out_rows:
        return df, gaps
    filled = pl.DataFrame(out_rows, schema=df.schema)
    return pl.concat([df, filled], how="vertical_relaxed").sort("open_time_ms"), gaps


def assemble(ctx: ForwardContext) -> Assembled:
    fwd = load_forward_bars(ctx.paths.bars_dir)
    fwd_first = int(fwd["open_time_ms"].min()) if fwd.height else None  # type: ignore[arg-type]
    seed = load_seed_bars(ctx.paths.seed, before_ms=fwd_first)
    # bars with no trades inside the collector window are kept but flagged (never invented)
    frames = [s for s in (seed, fwd) if not s.is_empty()]
    if not frames:
        raise RuntimeError("no bars yet (seed empty and no forward bars)")
    allb = (
        pl.concat(
            [f.with_columns(pl.lit(i).alias("_prio")) for i, f in enumerate(frames)],
            how="diagonal_relaxed",
        )
        .sort("open_time_ms", "_prio")
        .unique(subset=["open_time_ms"], keep="last", maintain_order=True)
        .drop("_prio")
    )
    allb = allb.with_columns(pl.col("gap_filled").fill_null(0.0))
    # a forward bar with zero trades carries the previous close (flagged) so the grid stays regular
    closes = allb["close"].to_numpy().astype(float)
    if np.isnan(closes).any():
        ff_close = (
            pl.Series(closes).fill_nan(None).fill_null(strategy="forward").to_numpy().astype(float)
        )
        allb = allb.with_columns(
            pl.when(pl.col("close").is_nan() | pl.col("close").is_null())
            .then(1.0)
            .otherwise(pl.col("gap_filled"))
            .alias("gap_filled"),
            *[
                pl.when(pl.col(c).is_nan() | pl.col(c).is_null())
                .then(pl.Series(ff_close))
                .otherwise(pl.col(c))
                .alias(c)
                for c in ("open", "high", "low", "close")
            ],
        )
    allb, gaps = _gap_fill(allb, ctx.fcfg.bars.max_gap_fill_bars)
    bars = allb.select(BAR_COLS).with_columns(pl.col("trades").cast(pl.Int64))
    flow = allb.with_columns(pl.col("trades").cast(pl.UInt32).alias("n_trades")).select(
        [c for c in FLOW_COLUMNS if c in allb.columns or c == "n_trades"]
    )
    rows = allb
    # order book: forward rows only (archive has no book); long format for the frozen loader
    book_rows = (
        rows.filter(pl.col("book_valid") == 1.0) if "book_valid" in rows.columns else pl.DataFrame()
    )
    if not book_rows.is_empty():
        book = pl.concat(
            [
                book_rows.select(
                    pl.col("close_time_ms").alias("time_ms"),
                    pl.lit(p, dtype=pl.Int64).alias("percentage"),
                    pl.col(c).alias("depth"),
                    pl.lit(0.0).alias("notional"),
                )
                for p, c in (
                    (-1, "book_bid_depth_pct"),
                    (1, "book_ask_depth_pct"),
                    (-5, "book_bid_depth_all"),
                    (5, "book_ask_depth_all"),
                )
            ]
        ).sort("time_ms", "percentage")
    else:
        book = pl.DataFrame()
    has_ws = "oi_last" in rows.columns
    metrics = (
        rows.filter(pl.col("oi_last").is_not_null() & pl.col("oi_last").is_not_nan()).select(
            pl.col("close_time_ms").alias("time_ms"),
            pl.col("oi_last").alias("open_interest"),
            pl.col("oi_value_last").alias("open_interest_value"),
            pl.lit(math.nan).alias("long_short_ratio_accounts"),
            pl.lit(math.nan).alias("top_trader_long_short_ratio_positions"),
            pl.lit(math.nan).alias("taker_long_short_volume_ratio"),
        )
        if has_ws
        else None
    )
    funding = None
    if has_ws:
        fr = (
            rows.filter(
                pl.col("next_funding_ms").is_not_null() & pl.col("next_funding_ms").is_not_nan()
            )
            .select("close_time_ms", "next_funding_ms", "funding_rate_last")
            .sort("close_time_ms")
        )
        ev: list[dict[str, Any]] = []
        prev_nf, prev_rate = None, math.nan
        for r in fr.iter_rows(named=True):
            nf = int(r["next_funding_ms"])
            if prev_nf is not None and nf != prev_nf and r["close_time_ms"] >= prev_nf:
                ev.append({"time_ms": prev_nf, "funding_rate": prev_rate})
            prev_nf, prev_rate = nf, float(r["funding_rate_last"])
        funding = (
            pl.DataFrame(ev, schema={"time_ms": pl.Int64, "funding_rate": pl.Float64})
            if ev
            else pl.DataFrame(schema={"time_ms": pl.Int64, "funding_rate": pl.Float64})
        )
    mark = (
        rows.filter(pl.col("mark_close").is_not_null() & pl.col("mark_close").is_not_nan()).select(
            "open_time_ms",
            "close_time_ms",
            pl.col("mark_open").alias("open"),
            pl.col("mark_high").alias("high"),
            pl.col("mark_low").alias("low"),
            pl.col("mark_close").alias("close"),
        )
        if has_ws
        else None
    )
    index = (
        rows.filter(
            pl.col("index_close").is_not_null() & pl.col("index_close").is_not_nan()
        ).select(
            "open_time_ms",
            pl.col("index_open").alias("open"),
            pl.col("index_high").alias("high"),
            pl.col("index_low").alias("low"),
            pl.col("index_close").alias("close"),
        )
        if has_ws
        else pl.DataFrame()
    )
    premium = (
        rows.filter(
            pl.col("mark_close").is_not_null()
            & pl.col("index_close").is_not_null()
            & pl.col("mark_close").is_not_nan()
            & pl.col("index_close").is_not_nan()
        ).select(
            "open_time_ms",
            "close_time_ms",
            (pl.col("mark_close") / pl.col("index_close") - 1.0).alias("close"),
        )
        if has_ws
        else None
    )
    n_gap = int((allb["gap_filled"] == 1.0).sum())
    return Assembled(
        bars,
        rows,
        V5Inputs(flow, book, index),
        funding,
        metrics,
        premium,
        mark,
        gaps,
        seed.height,
        fwd.height,
        n_gap,
    )


@dataclass
class Built:
    series: MultiTfSeries
    aux: AuxSeries
    ff: FeatureFrame
    detectors: list[V5Detector]
    last_close_ms: int


def build(ctx: ForwardContext, a: Assembled) -> Built:
    aux = AuxSeries.build(a.funding, a.metrics, a.premium, a.mark, ctx.cfg.data.latency_minutes)
    series = MultiTfSeries(a.bars, ctx.cfg.indicators, 5)
    ff = build_feature_frame(series, aux, ctx.cfg, a.extra)
    dets = build_v5_detectors(ctx.cfg, ff)
    return Built(series, aux, ff, dets, int(series.base.close_ms[-1]))


# ----------------------------------------------------------------------------- persistence helpers
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in rows:
            f.write(
                json.dumps({k: _clean(v) for k, v in r.items()}, sort_keys=True, default=str) + "\n"
            )


# ----------------------------------------------------------------------------- signals
def _plan(ctx: ForwardContext, ff: FeatureFrame, k: int, side: Side) -> dict[str, Any]:
    """The frozen Stage B geometry evaluated at the event bar: zone, structural stop, volatility
    floor and the hypothetical reference entry (event close). The executable fill is the next 5m
    open after the trigger, which is not known at snapshot time."""
    cfg = ctx.cfg
    x, s = cfg.execution, float(side.sign)
    c, atr = ff.v("close", k), ff.v("atr", k)
    struct = ff.v("lo_struct", k) if s > 0 else ff.v("hi_struct", k)
    if math.isnan(struct):
        struct = c
    structural_stop = struct - s * x.struct_buffer_atr * atr
    floor = c - s * x.vol_floor_atr * atr
    stop = structural_stop if s * (c - structural_stop) >= x.vol_floor_atr * atr else floor
    if s * (c - stop) > x.max_stop_atr * atr:
        stop = c - s * x.max_stop_atr * atr
    dist = s * (c - stop)
    risk_amt = ctx.fcfg.paper.initial_equity * ctx.fcfg.paper.risk_per_trade
    cc = cfg.costs
    rt_cost_pct = (2 * cc.taker_fee_bps + cc.entry_slippage_bps + cc.stop_slippage_bps) / 10_000.0
    return {
        "event_close": c,
        "atr_1h": atr,
        "entry_zone_low": min(c - s * x.zone_below_atr * atr, c + s * x.zone_above_atr * atr),
        "entry_zone_high": max(c - s * x.zone_below_atr * atr, c + s * x.zone_above_atr * atr),
        "confirmation": "first 15m close beyond the previous 15m close in the trade direction within 2 h; then a 5m close inside the zone within 6 bars; fill next 5m open",
        "structural_stop": structural_stop,
        "vol_floor_stop": floor,
        "stop_price_at_event_close": stop,
        "stop_source": "STRUCTURAL"
        if stop == structural_stop
        else ("CAP" if abs(dist - x.max_stop_atr * atr) < 1e-9 else "VOL_FLOOR"),
        "stop_distance_pct": dist / c * 100.0 if c else math.nan,
        "stop_distance_atr": dist / atr if atr else math.nan,
        "hypothetical_entry": c,
        "tp1": c + s * cfg.exits.tp1_r * dist,
        "tp2": c + s * cfg.exits.tp2_r * dist,
        "risk_amount_usdt": risk_amt,
        "hypothetical_qty_btc": risk_amt / dist if dist > 0 else math.nan,
        "expected_round_trip_cost_pct": rt_cost_pct * 100.0,
        "expected_cost_pct_of_stop": rt_cost_pct / (dist / c) * 100.0
        if dist > 0 and c
        else math.nan,
        "exits": "TP1 +1R 40% (breakeven), TP2 +2R 30%, 1H swing trail - 0.5 ATR after TP1, 24 h cap",
    }


def persist_new_signals(
    ctx: ForwardContext, b: Built, events: pl.DataFrame, rows: pl.DataFrame
) -> list[dict[str, Any]]:
    existing = {r["signal_id"] for r in _read_jsonl(ctx.paths.signals_file)}
    out: list[dict[str, Any]] = []
    if events.is_empty():
        return out
    gap = (
        dict(zip(rows["close_time_ms"].to_list(), rows["gap_filled"].to_list(), strict=True))
        if "gap_filled" in rows.columns
        else {}
    )
    gf = b.ff.cols.get("flow_source")
    for ev in events.sort("t_ms").iter_rows(named=True):
        sid = f"{ev['family']}|{ev['side']}|{ev['t_ms']}"
        if sid in existing:
            continue
        k = int(ev["k"])
        side = Side(ev["side"])
        feats = {c: _clean(float(b.ff.cols[c][k])) for c in FEATURE_COLUMNS}
        ctxd = {c: _clean(float(b.ff.cols[c][k])) for c in CONTEXT_COLS}
        after_gap = any(
            gap.get(int(b.ff.close_ms[j]), 0.0) == 1.0 for j in range(max(0, k - 12), k + 1)
        )
        rec = {
            "signal_id": sid,
            "t_ms": int(ev["t_ms"]),
            "t": _iso(ev["t_ms"]),
            "family": ev["family"],
            "side": ev["side"],
            "strength": float(ev["strength"]),
            "first_in_cluster": bool(ev["first_in_cluster"]),
            "regime": ev["regime"],
            "context_1h_4h": ctxd,
            "features": feats,
            "flow_source": "bybit_ws" if gf is not None and gf[k] == 1.0 else "kline_fallback",
            "after_gap_12_bars": after_gap,
            "plan": _plan(ctx, b.ff, k, side),
            "snapshot_created_at": datetime.now(UTC).isoformat(),
            "frozen_v5_config_hash": ctx.cfg.config_hash,
        }
        rec["snapshot_sha256"] = hashlib.sha256(
            json.dumps(
                {kk: _clean(vv) for kk, vv in rec.items()}, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        out.append(rec)
    _append_jsonl(ctx.paths.signals_file, out)
    return out


# ----------------------------------------------------------------------------- outcomes
def persist_outcomes(ctx: ForwardContext, b: Built, eng: V5Engine) -> list[dict[str, Any]]:
    """For snapshots whose 12 h horizon has elapsed: forward returns, MFE/MAE and a hypothetical
    trade (entry at the next 5m open after the event with the snapshot's stop geometry, frozen
    exits and costs). Written once; the snapshot itself is never touched."""
    done = {r["signal_id"] for r in _read_jsonl(ctx.paths.outcomes_file)}
    sigs = [s for s in _read_jsonl(ctx.paths.signals_file) if s["signal_id"] not in done]
    hz = ctx.cfg.stage_a.horizons_hours
    mm = ctx.cfg.stage_a.mfe_mae_hours * 12
    c, hi, lo = b.ff.cols["close"], b.ff.cols["high"], b.ff.cols["low"]
    out: list[dict[str, Any]] = []
    for s in sigs:
        k = b.ff.idx_at(int(s["t_ms"]))
        if k < 0 or int(b.ff.close_ms[k]) != int(s["t_ms"]) or k + mm >= len(c):
            continue
        sgn = 1.0 if s["side"] == "LONG" else -1.0
        c0, atr = float(c[k]), float(s["plan"]["atr_1h"] or math.nan)
        rec: dict[str, Any] = {
            "signal_id": s["signal_id"],
            "t_ms": s["t_ms"],
            "family": s["family"],
            "side": s["side"],
            "matured_at": datetime.now(UTC).isoformat(),
        }
        for h in hz:
            st = round(h * 12)
            rec[f"fwd_{h:g}h"] = sgn * (float(c[k + st]) / c0 - 1.0) if k + st < len(c) else None
        fav = (
            (float(np.max(hi[k + 1 : k + 1 + mm])) - c0)
            if sgn > 0
            else (c0 - float(np.min(lo[k + 1 : k + 1 + mm])))
        )
        adv = (
            (c0 - float(np.min(lo[k + 1 : k + 1 + mm])))
            if sgn > 0
            else (float(np.max(hi[k + 1 : k + 1 + mm])) - c0)
        )
        rec["mfe_atr"] = fav / atr if atr else None
        rec["mae_atr"] = -adv / atr if atr else None
        stop_pct = float(s["plan"]["stop_distance_pct"] or 0.0) / 100.0
        pos = (
            simulate_entry(
                eng, k + 1, Side(s["side"]), stop_pct, atr, ctx.fcfg.paper.initial_equity
            )
            if stop_pct > 0
            else None
        )
        if pos is not None:
            row = pos.to_row()
            rec.update(
                {
                    "hyp_entry_ms": row["entry_ms"],
                    "hyp_entry_price": row["entry_price"],
                    "hyp_exit_reason": row["exit_reason"],
                    "hyp_holding_hours": row["holding_hours"],
                    "hyp_gross_R": row["R_MULTIPLE_GROSS"],
                    "hyp_net_R": row["R_MULTIPLE"],
                    "hyp_fees": row["fees"],
                    "hyp_slippage": row["slippage"],
                    "hyp_funding": row["funding"],
                    "hyp_net_pnl": row["POSITION_PNL"],
                }
            )
        out.append(rec)
    _append_jsonl(ctx.paths.outcomes_file, out)
    return out


# ----------------------------------------------------------------------------- paper ledger
def update_paper(
    ctx: ForwardContext, trades: pl.DataFrame, last_close_ms: int, last_close: float
) -> dict[str, Any]:
    stored = _read_jsonl(ctx.paths.paper_trades)
    keys = {(r["family"], r["side"], int(r["entry_ms"])) for r in stored}
    new: list[dict[str, Any]] = []
    open_pos: dict[str, Any] | None = None
    integrity: list[str] = []
    if trades.height:
        for r in trades.sort("entry_ms").iter_rows(named=True):
            if r["exit_reason"] == "END_OF_DATA":
                open_pos = {k: _clean(v) for k, v in r.items()}
                open_pos["status"] = "OPEN (marked at the last completed 5m close)"
                continue
            key = (r["family"], r["side"], int(r["entry_ms"]))
            rec = {k: _clean(v) for k, v in r.items()}
            rec["closed_recorded_at"] = datetime.now(UTC).isoformat()
            if key in keys:
                old = next(x for x in stored if (x["family"], x["side"], int(x["entry_ms"])) == key)
                if abs(float(old["POSITION_PNL"]) - float(rec["POSITION_PNL"])) > 1e-6:
                    integrity.append(
                        f"closed trade {key} re-derived with a different P&L ({old['POSITION_PNL']} vs {rec['POSITION_PNL']})"
                    )
                continue
            new.append(rec)
    _append_jsonl(ctx.paths.paper_trades, new)
    allt = stored + new
    pnl = [float(r["POSITION_PNL"]) for r in allt]
    r_net = [float(r["R_MULTIPLE"]) for r in allt]
    r_gross = [float(r["R_MULTIPLE_GROSS"]) for r in allt]
    eq = ctx.fcfg.paper.initial_equity + sum(pnl)
    curve = np.cumsum([ctx.fcfg.paper.initial_equity, *pnl])
    dd = float(np.max(np.maximum.accumulate(curve) - curve)) if len(curve) else 0.0
    state = {
        "updated_at": datetime.now(UTC).isoformat(),
        "last_bar_close": _iso(last_close_ms),
        "last_close": last_close,
        "initial_equity": ctx.fcfg.paper.initial_equity,
        "equity_realised": eq,
        "unrealised_pnl": _clean(open_pos.get("POSITION_PNL")) if open_pos else 0.0,
        "equity_mtm": eq + (float(open_pos.get("POSITION_PNL") or 0.0) if open_pos else 0.0),
        "closed_trades": len(allt),
        "new_closed_this_cycle": len(new),
        "cumulative_net_pnl": sum(pnl),
        "mean_net_R": float(np.mean(r_net)) if r_net else None,
        "mean_gross_R": float(np.mean(r_gross)) if r_gross else None,
        "win_rate": float(np.mean([p > 0 for p in pnl])) if pnl else None,
        "max_drawdown_usdt": dd,
        "max_drawdown_frac": dd / ctx.fcfg.paper.initial_equity,
        "open_position": open_pos,
        "integrity_errors": integrity,
    }
    ctx.paths.paper_state.write_text(
        json.dumps({k: _clean(v) for k, v in state.items()}, indent=1, sort_keys=True, default=str)
    )
    return state


# ----------------------------------------------------------------------------- seed maintenance
def extend_seed(ctx: ForwardContext, now_ms: int) -> dict[str, Any]:
    """Fetch archive days up to yesterday that are not yet in the seed (the archive lags one day);
    forward WS bars always take precedence where both exist."""
    yesterday = (now_ms // DAY_MS - 1) * DAY_MS
    fwd = load_forward_bars(ctx.paths.bars_dir)
    if fwd.height:  # never use archive days on or after the collector's first day (keeps the
        # warm-up series fixed once forward bars exist, so re-derived decisions never change)
        first_day = int(fwd["open_time_ms"].min()) // DAY_MS * DAY_MS  # type: ignore[arg-type]
        yesterday = min(yesterday, first_day - DAY_MS)
    first_needed = ctx.start_ms - ctx.fcfg.seed.days_before_start * DAY_MS
    days = [
        d for d in seed_days(yesterday + DAY_MS, int((yesterday + DAY_MS - first_needed) // DAY_MS))
    ]
    ing = BybitSeedIngestor(ctx.paths.seed, ctx.fcfg.symbol)
    missing = [d for d in days if d not in ing._done]
    return (
        ing.ingest_days(missing)
        if missing
        else {"fetched": 0, "skipped": len(days), "unavailable": [], "rows": 0}
    )


# ----------------------------------------------------------------------------- cycle
def run_cycle(
    ctx: ForwardContext,
    now_ms: int | None = None,
    extend: bool = True,
    on_result: Any | None = None,
) -> dict[str, Any]:
    """`on_result(a, b, res)` (optional, demo execution only) runs after the paper ledger update;
    its failure never stops the observation."""
    t0 = time.time()
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    out: dict[str, Any] = {"cycle_at": _iso(now)}
    if extend:
        try:
            out["seed_extension"] = extend_seed(ctx, now)
        except Exception as e:
            out["seed_extension"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    out["new_bars"] = ctx.processor.process(now, ctx.fcfg.schedule.cycle_grace_seconds * 1000)
    a = assemble(ctx)
    b = build(ctx, a)
    out.update(
        {
            "bars_total": a.bars.height,
            "bars_seed": a.n_seed,
            "bars_forward": a.n_forward,
            "bars_gap_filled": a.n_gap,
            "gaps": a.gaps[-5:],
            "last_bar_close": _iso(b.last_close_ms),
        }
    )
    k = b.ff.idx_at(b.last_close_ms)
    out["warmup"] = {
        c: _clean(float(b.ff.cols[c][k])) is not None
        for c in (
            "atr",
            "trend_4h",
            "ret_1h_z",
            "imbalance_1h_z",
            "cvd_slope_1h_z",
            "vol_1h_z",
            "oi_chg_1h_z",
            "fund_z",
            "prem_z",
            "basis_z",
            "book_imb_1",
        )
    }
    events = scan_events(b.ff, b.detectors, ctx.cfg, ctx.start_ms, b.last_close_ms + 1)
    new_sigs = persist_new_signals(ctx, b, events, a.rows)
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
    res = eng.run(ctx.start_ms, b.last_close_ms + 1, notes={"stream": "forward_paper"})
    out["paper"] = update_paper(ctx, res.trades, b.last_close_ms, float(b.series.base.close[-1]))
    if on_result is not None:
        try:
            out["demo"] = on_result(a, b, res)
        except Exception as e:
            log.exception("demo execution step failed")
            out["demo"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    out["episodes_since_start"] = res.episodes.height
    out["blocked"] = res.blocked
    out["new_outcomes"] = len(persist_outcomes(ctx, b, eng))
    out["elapsed_s"] = round(time.time() - t0, 2)
    _append_jsonl(
        ctx.paths.cycle_log,
        [
            {k2: v for k2, v in out.items() if k2 != "paper"}
            | {
                "paper_equity_mtm": out["paper"]["equity_mtm"],
                "paper_closed": out["paper"]["closed_trades"],
                "paper_open": out["paper"]["open_position"] is not None,
            }
        ],
    )
    return out
