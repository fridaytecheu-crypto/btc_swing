"""Phase 2 validation runner: frozen pre-registered defaults, two chronological segments, leverage
comparison under the same risk methodology, geometry-matched null benchmark, forward labels for
every episode, PIT audit, extended account statistics. Produces the inputs of
`reports/BTC_SWING_V1_PHASE2_VALIDATION.md` (rendered by `phase2_report.py`)."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.backtest.engine import BacktestEngine, BacktestResult
from btc_swing.core.config import BtcStrategyConfig
from btc_swing.core.enums import Timeframe
from btc_swing.core.timeframes import tf_ms
from btc_swing.features.context import AuxSeries
from btc_swing.features.resample import resample_completed
from btc_swing.features.view import MultiTfSeries
from btc_swing.providers.base import Dataset
from btc_swing.research.labels import label_episodes
from btc_swing.research.metrics import compute_metrics, drawdown
from btc_swing.research.null_benchmark import run_null
from btc_swing.storage.bar_store import BarStore

log = logging.getLogger(__name__)
DAY_MS = 86_400_000


def _f(x: object) -> float:
    """Polars scalar aggregate -> float (NaN when missing)."""
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


@dataclass(frozen=True)
class Segment:
    name: str
    start_ms: int
    end_ms: int


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp() * 1000)


DEFAULT_SEGMENTS = [
    Segment("dev_2022_2023", _ms("2022-01-01"), _ms("2024-01-01")),
    Segment("val_2024", _ms("2024-01-01"), _ms("2025-01-01")),
]


@dataclass
class Phase2Inputs:
    bars: pl.DataFrame
    funding: pl.DataFrame | None
    metrics: pl.DataFrame | None
    premium: pl.DataFrame | None
    mark: pl.DataFrame | None
    natives: dict[Timeframe, pl.DataFrame]
    hashes: dict[str, str]
    coverage: dict[str, Any] = field(default_factory=dict)


def load_inputs(cfg: BtcStrategyConfig, data_dir: Path, start_ms: int, end_ms: int) -> Phase2Inputs:
    bs = BarStore(data_dir)
    sym = cfg.instrument.symbol

    def _scan(ds: Dataset, tf: Timeframe | None, tcol: str) -> pl.DataFrame:
        df = bs.scan(ds, sym, tf, time_col=tcol)
        return df if df.is_empty() else df.filter(pl.col(tcol) < end_ms)

    bars = _scan(Dataset.PERP_KLINES, Timeframe.M5, "open_time_ms")
    funding = _scan(Dataset.FUNDING, None, "time_ms")
    metrics = _scan(Dataset.METRICS, None, "time_ms")
    premium = _scan(Dataset.PREMIUM_INDEX, Timeframe.M5, "open_time_ms")
    mark = _scan(Dataset.MARK_PRICE, Timeframe.M5, "open_time_ms")
    natives = {
        tf: _scan(Dataset.PERP_KLINES, tf, "open_time_ms")
        for tf in cfg.data.ingest_native_timeframes
    }
    hashes = {
        "perp_klines_5m": bs.series_hash(Dataset.PERP_KLINES, sym, Timeframe.M5),
        "funding": bs.series_hash(Dataset.FUNDING, sym, None),
        "metrics": bs.series_hash(Dataset.METRICS, sym, None),
        "premium_index_5m": bs.series_hash(Dataset.PREMIUM_INDEX, sym, Timeframe.M5),
        "mark_price_5m": bs.series_hash(Dataset.MARK_PRICE, sym, Timeframe.M5),
    }
    inp = Phase2Inputs(bars, funding, metrics, premium, mark, natives, hashes)
    inp.coverage = _coverage(inp, start_ms, end_ms)
    return inp


def _coverage(inp: Phase2Inputs, start_ms: int, end_ms: int) -> dict[str, Any]:
    def rng(df: pl.DataFrame | None, col: str, step_ms: int | None) -> dict[str, Any]:
        if df is None or df.is_empty():
            return {"rows": 0}
        t = df[col]
        d: dict[str, Any] = {
            "rows": df.height,
            "first": datetime.fromtimestamp(int(_f(t.min())) / 1000, tz=UTC).isoformat(),
            "last": datetime.fromtimestamp(int(_f(t.max())) / 1000, tz=UTC).isoformat(),
        }
        if step_ms:
            w = df.filter((pl.col(col) >= start_ms) & (pl.col(col) < end_ms))
            expected = (end_ms - start_ms) // step_ms
            d["rows_in_study_window"] = w.height
            d["expected_in_study_window"] = expected
            d["coverage_pct"] = 100.0 * w.height / expected if expected else math.nan
        return d

    return {
        "perp_klines_5m": rng(inp.bars, "open_time_ms", tf_ms(Timeframe.M5)),
        "funding": rng(inp.funding, "time_ms", 8 * 3_600_000),
        "metrics_5min": rng(inp.metrics, "time_ms", tf_ms(Timeframe.M5)),
        "premium_index_5m": rng(inp.premium, "open_time_ms", tf_ms(Timeframe.M5)),
        "mark_price_5m": rng(inp.mark, "open_time_ms", tf_ms(Timeframe.M5)),
        **{
            f"native_{tf.value}": rng(df, "open_time_ms", tf_ms(tf))
            for tf, df in inp.natives.items()
        },
    }


# --------------------------------------------------------------------------- PIT audit
def resample_oracle(bars: pl.DataFrame, natives: dict[Timeframe, pl.DataFrame]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    last_close = int(bars["open_time_ms"][-1]) + tf_ms(Timeframe.M5)
    for tf, nat in natives.items():
        if nat.is_empty():
            continue
        ours = resample_completed(bars, tf, last_close)
        j = ours.join(nat, on="open_time_ms", how="inner", suffix="_nat").with_columns(
            d=pl.max_horizontal(
                *[(pl.col(c) - pl.col(c + "_nat")).abs() for c in ("open", "high", "low", "close")]
            )
        )
        bad = j.filter(pl.col("d") > 0.05)
        out[tf.value] = {
            "bars_compared": j.height,
            "bars_differing": bad.height,
            "exact_match_pct": 100.0 * (1 - bad.height / max(j.height, 1)),
            "differing_times": [
                datetime.fromtimestamp(int(x) / 1000, tz=UTC).isoformat()
                for x in bad["open_time_ms"].to_list()[:20]
            ],
        }
    return out


def truncation_audit(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    aux: AuxSeries,
    full: BacktestResult,
    start_ms: int,
    cut_ms: int,
) -> dict[str, Any]:
    trunc = inp.bars.filter(pl.col("open_time_ms") + tf_ms(Timeframe.M5) <= cut_ms)
    res_t = BacktestEngine(cfg, trunc, inp.funding, inp.hashes, aux).run(start_ms, cut_ms)
    cols = ["t_ms", "regime", "episode_state", "family", "position_open"]
    a = full.decisions.filter(pl.col("t_ms") <= cut_ms).select(cols)
    b = res_t.decisions.filter(pl.col("t_ms") <= cut_ms).select(cols)
    return {
        "cut": datetime.fromtimestamp(cut_ms / 1000, tz=UTC).isoformat(),
        "rows_compared": a.height,
        "identical": a.height == b.height and a.equals(b),
    }


# --------------------------------------------------------------------------- extended stats
def account_stats(
    trades: pl.DataFrame, daily: pl.DataFrame, initial_equity: float, start_ms: int, end_ms: int
) -> dict[str, Any]:
    out: dict[str, Any] = {"initial_equity": initial_equity}
    if trades.is_empty():
        return out
    t = trades.sort("entry_ms")
    pnl = t["POSITION_PNL"].to_numpy().astype(float)
    eq = initial_equity + np.cumsum(pnl)
    final = float(eq[-1])
    years = (end_ms - start_ms) / (365.25 * DAY_MS)
    dd_frac, dd_cur = drawdown(np.concatenate([[initial_equity], eq]))
    # drawdown window on the trade-sequence curve
    curve = np.concatenate([[initial_equity], eq])
    peak = np.maximum.accumulate(curve)
    trough_i = int(np.argmin(curve - peak))
    peak_i = int(np.argmax(curve[: trough_i + 1]))
    window_trades = t.slice(peak_i, trough_i - peak_i)  # trades between peak and trough
    dd_contrib = (
        window_trades.group_by("family")
        .agg(pl.col("POSITION_PNL").sum().alias("pnl_in_dd_window"), pl.len().alias("n"))
        .sort("pnl_in_dd_window")
        .to_dicts()
        if window_trades.height
        else []
    )
    # streaks
    longest_loss = cur = 0
    longest_win = curw = 0
    for x in pnl:
        if x <= 0:
            cur += 1
            curw = 0
        else:
            curw += 1
            cur = 0
        longest_loss, longest_win = max(longest_loss, cur), max(longest_win, curw)
    # daily series (mtm)
    sharpe = sortino = math.nan
    if daily.height > 3:
        e = daily["equity_mtm"].to_numpy().astype(float)
        r = np.diff(e) / e[:-1]
        if r.std(ddof=1) > 0:
            sharpe = float(r.mean() / r.std(ddof=1) * math.sqrt(365))
        neg = r[r < 0]
        if len(neg) > 1 and neg.std(ddof=1) > 0:
            sortino = float(
                r.mean() / math.sqrt(np.mean(np.square(np.minimum(r, 0)))) * math.sqrt(365)
            )
    gaps_h = np.diff(t["entry_ms"].to_numpy().astype(float)) / 3_600_000.0
    # overlap check (sequential account: at most one position)
    ent, ex = t["entry_ms"].to_numpy(), t["exit_ms"].to_numpy()
    overlaps = int(np.sum(ent[1:] < ex[:-1]))
    out.update(
        {
            "final_equity": final,
            "total_return": final / initial_equity - 1.0,
            "years": years,
            "cagr": (final / initial_equity) ** (1 / years) - 1.0
            if years > 0.25 and final > 0
            else math.nan,
            "max_drawdown_frac_trade_curve": dd_frac,
            "max_drawdown_currency": dd_cur,
            "max_drawdown_frac_daily_mtm": drawdown(
                np.concatenate([[initial_equity], daily["equity_mtm"].to_numpy().astype(float)])
            )[0]
            if daily.height
            else math.nan,
            "sharpe_daily_annualised": sharpe,
            "sortino_daily_annualised": sortino,
            "longest_losing_streak": longest_loss,
            "longest_winning_streak": longest_win,
            "median_hours_between_entries": float(np.median(gaps_h)) if len(gaps_h) else math.nan,
            "trades_per_month": trades.height / max(years * 12, 1e-9),
            "trades_per_week": trades.height / max(years * 52.18, 1e-9),
            "trades_per_day": trades.height / max(years * 365.25, 1e-9),
            "max_simultaneous_positions": 1 if overlaps == 0 else 2,
            "overlapping_trade_pairs": overlaps,
            "drawdown_window_family_contribution": dd_contrib,
            "drawdown_window_n_trades": int(window_trades.height),
        }
    )
    return out


def family_table(trades: pl.DataFrame, min_n: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if trades.is_empty():
        return rows
    for fam in sorted(trades["family"].unique().to_list()):
        t = trades.filter(pl.col("family") == fam)
        pnl = t["POSITION_PNL"].to_numpy().astype(float)
        r = t["R_MULTIPLE"].to_numpy().astype(float)
        gw, gl = float(pnl[pnl > 0].sum()), float(-pnl[pnl <= 0].sum())
        rows.append(
            {
                "family": fam,
                "side": t["side"][0],
                "n": t.height,
                "win_rate": float((pnl > 0).mean()),
                "expectancy_R": float(r.mean()),
                "median_R": float(np.median(r)),
                "profit_factor": gw / gl if gl > 0 else math.inf,
                "mean_MFE_R": _f((t)["MFE_R"].mean()),
                "mean_MAE_R": _f((t)["MAE_R"].mean()),
                "median_holding_hours": _f((t)["holding_hours"].median()),
                "sum_account_return": _f((t)["ACCOUNT_RETURN"].sum()),
                "reliable": t.height >= min_n,
            }
        )
    return rows


def cost_impact(trades: pl.DataFrame) -> dict[str, Any]:
    if trades.is_empty():
        return {}
    gross = _f((trades)["gross_pnl"].sum())
    slip = _f((trades)["slippage"].sum())
    fees = _f((trades)["fees"].sum())
    fund = _f((trades)["funding"].sum())
    net = _f((trades)["POSITION_PNL"].sum())
    risk = _f((trades)["risk_amount"].sum())
    return {
        "gross_before_slippage": gross + slip,
        "slippage": -slip,
        "gross_after_slippage": gross,
        "fees": -fees,
        "funding": fund,
        "net": net,
        "expectancy_R_before_costs": (gross + slip) / risk if risk else math.nan,
        "expectancy_R_net": net / risk if risk else math.nan,
        "cost_drag_R_per_trade": (slip + fees - fund) / risk if risk else math.nan,
        "fees_pct_of_gross": 100 * fees / abs(gross + slip) if gross + slip else math.nan,
        "n_funding_events": int(trades["funding_events"].sum()),
    }


# --------------------------------------------------------------------------- leverage comparison
def leverage_comparison(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    aux: AuxSeries,
    series: MultiTfSeries,
    start_ms: int,
    end_ms: int,
    levels: list[float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lv in levels:
        allowed = [x for x in cfg.risk.allowed_leverage if x <= lv] or [lv]
        c2 = cfg.model_copy(
            update={
                "risk": cfg.risk.model_copy(
                    update={"max_leverage": lv, "allowed_leverage": allowed}
                )
            }
        )
        res = BacktestEngine(c2, inp.bars, inp.funding, inp.hashes, aux, series).run(
            start_ms, end_ms
        )
        t = res.trades
        acct = account_stats(t, res.daily_equity, cfg.risk.initial_equity, start_ms, end_ms)
        rows.append(
            {
                "max_leverage": lv,
                "n_trades": t.height,
                "n_risk_rejected": int(
                    res.episodes.filter(pl.col("outcome_class") == "RISK_REJECTED").height
                )
                if not res.episodes.is_empty()
                else 0,
                "pct_trades_full_risk": 100 * _f((~t["risk_capped"]).cast(pl.Float64).mean())
                if t.height
                else math.nan,
                "mean_leverage_used": _f((t)["leverage"].mean()) if t.height else math.nan,
                "mean_margin_pct_equity": 100 * _f(((t)["margin"] / t["equity_at_entry"]).mean())
                if t.height
                else math.nan,
                "mean_account_risk_pct": 100 * _f((t)["risk_frac"].mean())
                if t.height
                else math.nan,
                "min_stop_to_liq_ratio": _f((t)["stop_to_liquidation_ratio"].min())
                if t.height
                else math.nan,
                "min_liq_distance_pct": 100 * _f((t)["liquidation_distance_pct"].min())
                if t.height
                else math.nan,
                "liquidations": int((t["exit_reason"] == "LIQUIDATION").sum()) if t.height else 0,
                "expectancy_R": _f((t)["R_MULTIPLE"].mean()) if t.height else math.nan,
                "total_return_pct": 100 * acct.get("total_return", math.nan),
                "max_drawdown_pct": 100 * acct.get("max_drawdown_frac_trade_curve", math.nan),
                "sum_BTC_RETURN_pct": 100 * _f((t)["BTC_RETURN"].sum()) if t.height else math.nan,
                "mean_RETURN_ON_MARGIN_pct": 100 * _f((t)["RETURN_ON_MARGIN"].mean())
                if t.height
                else math.nan,
                "result_hash": res.result_hash,
            }
        )
    return rows


# --------------------------------------------------------------------------- runner
@dataclass
class Phase2Result:
    manifest: dict[str, Any]
    baseline: BacktestResult
    episodes_labelled: pl.DataFrame
    metrics_all: dict[str, Any]
    segments: dict[str, dict[str, Any]]
    leverage: list[dict[str, Any]]
    null: dict[str, Any]
    null_samples: pl.DataFrame
    pit_audit: dict[str, Any]
    coverage: dict[str, Any]
    account: dict[str, Any]
    costs: dict[str, Any]
    families: list[dict[str, Any]]


def run_phase2(
    cfg: BtcStrategyConfig,
    inp: Phase2Inputs,
    segments: list[Segment],
    leverage_levels: list[float],
    null_k: int,
    seed: int,
    out_dir: Path,
) -> Phase2Result:
    start_ms, end_ms = segments[0].start_ms, segments[-1].end_ms
    aux = AuxSeries.build(inp.funding, inp.metrics, inp.premium, inp.mark, cfg.data.latency_minutes)
    series = MultiTfSeries(inp.bars, cfg.indicators, cfg.regime.trend_slope_bars)
    log.info("baseline run %s -> %s", segments[0].name, segments[-1].name)
    eng = BacktestEngine(cfg, inp.bars, inp.funding, inp.hashes, aux, series)
    base = eng.run(start_ms, end_ms, notes={"phase": "2", "segments": [s.name for s in segments]})
    rerun = BacktestEngine(cfg, inp.bars, inp.funding, inp.hashes, aux, series).run(
        start_ms, end_ms
    )
    deterministic = rerun.result_hash == base.result_hash
    episodes = label_episodes(base.episodes, series, cfg.exits.max_hold_hours)
    metrics_all = compute_metrics(
        base.trades, base.episodes, base.daily_equity, base.manifest, cfg.research
    )
    seg_out: dict[str, dict[str, Any]] = {}
    for sg in segments:
        tr = (
            base.trades.filter(
                (pl.col("entry_ms") >= sg.start_ms) & (pl.col("entry_ms") < sg.end_ms)
            )
            if not base.trades.is_empty()
            else base.trades
        )
        ep = (
            episodes.filter(
                (pl.col("detected_at_ms") >= sg.start_ms) & (pl.col("detected_at_ms") < sg.end_ms)
            )
            if not episodes.is_empty()
            else episodes
        )
        dl = base.daily_equity.filter(
            (pl.col("t_ms") > sg.start_ms) & (pl.col("t_ms") <= sg.end_ms)
        )
        dec = base.decisions.filter((pl.col("t_ms") >= sg.start_ms) & (pl.col("t_ms") < sg.end_ms))
        man = {
            **base.manifest,
            "n_days": (sg.end_ms - sg.start_ms) / DAY_MS,
            "initial_equity": cfg.risk.initial_equity,
            "final_equity": cfg.risk.initial_equity
            + (_f((tr)["POSITION_PNL"].sum()) if tr.height else 0.0),
        }
        seg_out[sg.name] = {
            "start": datetime.fromtimestamp(sg.start_ms / 1000, tz=UTC).date().isoformat(),
            "end": datetime.fromtimestamp(sg.end_ms / 1000, tz=UTC).date().isoformat(),
            "metrics": compute_metrics(tr, ep, dl, man, cfg.research),
            "families": family_table(tr, cfg.research.min_cell_n),
            "account": account_stats(tr, dl, cfg.risk.initial_equity, sg.start_ms, sg.end_ms),
            "regime_bar_counts": dec.group_by("regime").len().sort("regime").to_dicts()
            if dec.height
            else [],
            "episode_outcomes": ep.group_by("family", "outcome_class")
            .len()
            .sort("family", "outcome_class")
            .to_dicts()
            if ep.height
            else [],
            "null": run_null(eng, tr, base.decisions, sg.start_ms, sg.end_ms, null_k, seed).summary
            if tr.height
            else {"n_trades": 0},
        }
    log.info("leverage comparison")
    lev = leverage_comparison(cfg, inp, aux, series, start_ms, end_ms, leverage_levels)
    log.info("null benchmark (combined)")
    null = run_null(eng, base.trades, base.decisions, start_ms, end_ms, null_k, seed)
    log.info("PIT audit")
    pit = {
        "visibility_rule": "bar visible iff close_time <= t; aux features iff time + latency <= t; funding applied in (prev close, t]",
        "determinism_identical": deterministic,
        "resample_oracle": resample_oracle(inp.bars, inp.natives),
        "truncation": truncation_audit(
            cfg, inp, aux, base, start_ms, start_ms + (end_ms - start_ms) // 2
        ),
        "look_ahead_guard": "MarketView raises LookAheadError on negative offsets; indicators causal (tests/btc)",
        "liquidation_basis": base.trades["liquidation_basis"][0] if base.trades.height else "n/a",
    }
    account = account_stats(
        base.trades, base.daily_equity, cfg.risk.initial_equity, start_ms, end_ms
    )
    manifest = {
        **base.manifest,
        "phase": "2",
        "segments": [
            {"name": s.name, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in segments
        ],
        "leverage_levels": leverage_levels,
        "null_k": null_k,
        "seed": seed,
        "deterministic": deterministic,
        "config_yaml": cfg.canonical_yaml(),
    }
    res = Phase2Result(
        manifest,
        base,
        episodes,
        metrics_all,
        seg_out,
        lev,
        null.summary,
        null.samples,
        pit,
        inp.coverage,
        account,
        cost_impact(base.trades),
        family_table(base.trades, cfg.research.min_cell_n),
    )
    _persist(res, out_dir)
    return res


def _persist(res: Phase2Result, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(res.manifest, indent=1, sort_keys=True, default=str)
    )
    summary = {
        "metrics_all": res.metrics_all,
        "segments": res.segments,
        "leverage": res.leverage,
        "null": res.null,
        "pit_audit": res.pit_audit,
        "coverage": res.coverage,
        "account": res.account,
        "costs": res.costs,
        "families": res.families,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str)
    )
    for name, df in (
        ("trades", res.baseline.trades),
        ("episodes", res.episodes_labelled),
        ("decisions", res.baseline.decisions),
        ("daily_equity", res.baseline.daily_equity),
        ("shadow_detections", res.baseline.shadow),
        ("null_samples", res.null_samples),
    ):
        if not df.is_empty():
            df.write_parquet(out_dir / f"{name}.parquet")
