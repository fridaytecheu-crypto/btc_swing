"""N1 / N2 / N4 research run: inventory v2 -> preregistration manifest (written BEFORE any outcome is
computed) -> screens -> event census -> gross markouts -> cost screen -> chronological blocks -> overlap
replay -> E4 collection audit -> SUMMARY.md with two verdicts (EDGE, CADENCE) per engine.
Files only; nothing order-capable; frozen runners never imported."""

from __future__ import annotations

import hashlib
import json
import math
import platform
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.intraday_portfolio import ORDER_CAPABLE, RESEARCH_ONLY
from btc_swing.research.intraday_portfolio import metrics as mx
from btc_swing.research.intraday_portfolio import ncensus as nc
from btc_swing.research.intraday_portfolio import nengines as ne
from btc_swing.research.intraday_portfolio.audit import e4_collection_audit
from btc_swing.research.intraday_portfolio.data import HOLDOUT_START, load_bybit_bars
from btc_swing.research.intraday_portfolio.inventory import build_inventory
from btc_swing.research.intraday_portfolio.ledger import (
    BASE_COST_BPS,
    COST_SCENARIOS_BPS,
    STRESS_COST_BPS,
)
from btc_swing.research.intraday_portfolio.primitives import (
    ATR_PERIOD,
    ER_BARS,
    efficiency_ratio,
    wilder_atr,
)
from btc_swing.research.intraday_portfolio.run import _f, _package_hashes, _write_table
from btc_swing.research.intraday_portfolio.spot import DATASET as SPOT_DATASET
from btc_swing.research.intraday_portfolio.spot import load_spot_5m

EDGE_VERDICTS = ("REJECT", "INCONCLUSIVE", "CONTINUE RESEARCH", "ELIGIBLE FOR FULL POLICY REPLAY")
CADENCE_VERDICTS = ("TOO LOW", "INCONCLUSIVE", "PLAUSIBLE CORE CADENCE", "SATELLITE ONLY")


@dataclass
class NRunResult:
    run_id: str
    out_dir: Path
    verdicts: dict[str, dict[str, Any]]
    files: list[str] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- data loading
def _month_files(root: Path, cap: date) -> list[Path]:
    return sorted(p for p in root.glob("*.parquet") if p.stem < cap.isoformat()[:7])


def _day_files(root: Path, cap: date) -> list[Path]:
    return sorted(p for p in root.glob("*.parquet") if p.stem < cap.isoformat())


def load_perp_5m(data_dir: Path) -> pl.DataFrame | None:
    files = _month_files(
        data_dir / "btc" / "datasets" / "perp_klines" / "BTCUSDT" / "5m", HOLDOUT_START
    )
    if not files:
        return None
    cols = [
        "open_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ]
    return (
        pl.concat([pl.read_parquet(f, columns=cols) for f in files])
        .sort("open_time_ms")
        .unique(subset=["open_time_ms"], keep="first", maintain_order=True)
    )


def load_oi(data_dir: Path) -> dict[int, float]:
    files = _day_files(data_dir / "btc" / "datasets" / "metrics" / "BTCUSDT" / "na", HOLDOUT_START)
    if not files:
        return {}
    df = pl.concat([pl.read_parquet(f, columns=["time_ms", "open_interest"]) for f in files])
    df = df.drop_nulls().sort("time_ms")
    return {
        int(t): float(v)
        for t, v in zip(df["time_ms"].to_list(), df["open_interest"].to_list(), strict=True)
    }


def _kline_checks(df: pl.DataFrame | None, name: str) -> dict[str, Any]:
    if df is None or df.height == 0:
        return {"status": "MISSING", "dataset": name}
    t = df["open_time_ms"].to_numpy()
    d = np.diff(t)
    return {
        "status": "AVAILABLE",
        "dataset": name,
        "rows": int(df.height),
        "first_open_time_utc": datetime.fromtimestamp(int(t[0]) / 1000, tz=UTC).isoformat(),
        "last_open_time_utc": datetime.fromtimestamp(int(t[-1]) / 1000, tz=UTC).isoformat(),
        "timestamp_unit": "ms" if int(t.max()) < 10**13 else "NOT_MS",
        "all_on_5m_utc_grid": bool(np.all(t % 300000 == 0)),
        "strictly_increasing": bool(np.all(d > 0)),
        "gaps_gt_5m": int(np.sum(d != 300000)),
        "max_gap_minutes": float(d.max() / 60000) if len(d) else None,
        "fields": {
            "total_quote_volume": "AVAILABLE" if "quote_volume" in df.columns else "MISSING",
            "total_base_volume": "AVAILABLE" if "volume" in df.columns else "MISSING",
            "taker_buy_quote_volume": "AVAILABLE"
            if "taker_buy_quote_volume" in df.columns
            else "MISSING",
            "taker_buy_base_volume": "AVAILABLE" if "taker_buy_volume" in df.columns else "MISSING",
        },
        "taker_buy_quote_nonzero_share": (
            float((df["taker_buy_quote_volume"] > 0).sum()) / df.height
            if "taker_buy_quote_volume" in df.columns
            else None
        ),
    }


def inventory_v2(
    data_dir: Path,
    spot: pl.DataFrame | None,
    perp: pl.DataFrame | None,
    b5_open_ms: np.ndarray,
    oi_n: int,
    audit: dict[str, Any],
) -> dict[str, Any]:
    base = build_inventory(data_dir)
    spot_set = set(spot["open_time_ms"].to_list()) if spot is not None else set()
    perp_set = set(perp["open_time_ms"].to_list()) if perp is not None else set()
    bybit = [int(x) for x in b5_open_ms.tolist()]
    sync = {
        "bybit_5m_bars": len(bybit),
        "share_with_binance_spot_row": sum(1 for t in bybit if t in spot_set) / len(bybit)
        if bybit
        else None,
        "share_with_binance_perp_row": sum(1 for t in bybit if t in perp_set) / len(bybit)
        if bybit
        else None,
        "alignment_key": "open_time_ms on the shared UTC 5m grid (both venues publish UTC ms open times)",
    }
    spot_manifest = (
        Path(__file__).resolve().parents[3] / "manifests" / "binance_spot_klines_5m_manifest.jsonl"
    )
    v2 = {
        "schema": "intraday_portfolio_inventory-2",
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "holdout_locked_never_read_from": HOLDOUT_START.isoformat(),
        "minute_ohlcv": {
            "bybit_1m_trade_built": base["inputs"]["bybit_1m_completed_bars"],
            "bybit_5m_trade_built": base["inputs"]["bybit_5m_completed_bars"],
            "binance_perp_1m_klines": {
                "status": "MISSING",
                "note": "only 5m / 1h / 4h / 1d perp klines are on disk; not needed by N1/N2/N4",
            },
            "binance_perp_5m_klines": _kline_checks(
                perp, "perp_klines (Binance USDT-perp, data.binance.vision)"
            ),
            "binance_spot_5m_klines": _kline_checks(
                spot,
                f"{SPOT_DATASET} (Binance SPOT, data.binance.vision, sha256 manifest {spot_manifest.name})",
            ),
        },
        "taker_buy_fields_in_binance_minute_archives": {
            "perp": "AVAILABLE (total quote/base volume + taker-buy quote/base volume retained)"
            if perp is not None
            else "MISSING",
            "spot": "AVAILABLE (total quote/base volume + taker-buy quote/base volume retained)"
            if spot is not None
            else "MISSING",
            "note": "verified on the parsed monthly archives; the spot archive switched to microsecond timestamps on 2025-01-01 and is normalised to ms",
        },
        "signed_perp_flow_inputs": {
            "binance_perp_taker_buy_quote": "AVAILABLE" if perp is not None else "MISSING",
            "bybit_perp_aggressor_qty_per_bar": "AVAILABLE (buy_qty / sell_qty / buy_notional / sell_notional in bybit_trades_5m)",
            "formula": "signed_quote_volume = 2 * taker_buy_quote_volume - total_quote_volume",
        },
        "synchronized_spot_data": sync,
        "spot_venue_identifiers": {
            "historical": ["BINANCE_SPOT"],
            "replication_second_venue": "MISSING (no second spot venue archive on disk) -> N2 is an exploratory single-venue screen",
        },
        "timestamp_units": {
            "bybit_bars": "ms",
            "binance_perp_klines": "ms",
            "binance_spot_klines": "ms (us archive rows normalised)",
            "metrics_oi": "ms",
            "funding": "ms",
        },
        "utc_normalization": {
            "all_datasets_keyed_by_utc_epoch_ms": True,
            "session_anchors": "local wall clock via zoneinfo (Asia/Tokyo, Europe/London, America/New_York) -> UTC ms",
        },
        "funding_oi_fields": {
            "binance_funding_8h": base["inputs"]["funding"],
            "binance_open_interest_5m": {
                "status": "AVAILABLE" if oi_n else "MISSING",
                "rows": oi_n,
                "role": "recorded only (N1 diagnostic), never gated",
            },
            "bybit_funding_oi_history": "MISSING (not on disk for the development span; Binance used as a labelled proxy)",
        },
        "receipt_timed_raw_collector_fields": {k: v["status"] for k, v in audit["fields"].items()},
        "e4_coverage": base["raw_store_coverage"],
        "e4_consequence": base["consequences"]["E4"],
        "phase0_base_inventory": base,
    }
    return v2


# ----------------------------------------------------------------------------- preregistration
def preregistration(
    run_id: str, first_day: str | None, last_day: str | None, hashes: dict[str, str]
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "PREREGISTERED_BEFORE_ANY_OUTCOME_WAS_COMPUTED",
        "package_sha256": hashes,
        "evaluation_window": {
            "first_day": first_day,
            "last_day": last_day,
            "holdout_never_read_from": HOLDOUT_START.isoformat(),
            "chronological_blocks": mx.BLOCKS,
        },
        "embargo_min": ne.EMBARGO_MIN,
        "entry_convention": "OPEN of the first Bybit 1m bar starting at decision + embargo; never the signal bar's own close",
        "outcome_convention": "side-adjusted gross return to the CLOSE of the 1m bar ending `horizon` minutes after entry; full 1m path required",
        "lagged_threshold_convention": f"quantile over the {ne.HISTORY_DAYS} most recent COMPLETE UTC days strictly before the signal day (signal day and bar excluded)",
        "episode_rule": f"same-side flag within {ne.COOLDOWN_MIN} min of the parent's first setup = repeated trigger (never a new opportunity); opposite side independent; N4 one parent per anchor per local weekday",
        "risk_unit": "ATR20 (completed 5m bars) in bps at setup = 1R",
        "N1": {
            "rule": "completed 5m bar: |ret5| >= lagged 95th percentile of |ret5|; LONG if ret5 > 0 and close > max high of the preceding 6 completed 5m bars (signal bar excluded); SHORT mirror",
            "pct": ne.N1_PCT,
            "prior_range_bars": ne.N1_RANGE_BARS,
            "primary_horizon_min": ne.N1_HORIZON_MIN,
            "diagnostic_horizon_min": ne.N1_DIAG_HORIZON_MIN,
            "recorded_not_gated": [
                "impulse_bps",
                "volume_ratio_vs_30d_median",
                "vwap30_displacement_bps",
                "oi_change_pct_proxy",
                "atr_bps",
            ],
            "kill": [
                "forward gross economically insufficient (< 16 bps base cost or gates fail)",
                "forward 30m gross < 25% of the completed impulse (movement mostly already done)",
                "positive blocks < 3 of 4",
                "core eligible cadence < 0.75/day",
            ],
        },
        "N2": {
            "rule": "completed 15m boundary: 15m signed quote flow = sum over the three constituent completed 5m bars of (2*taker_buy_quote - total_quote); |flow| >= lagged top decile (90th pct) of |15m flow|; all three 5m signs agree; no missing field; LONG if positive",
            "pct": ne.N2_PCT,
            "primary_horizon_min": ne.N2_HORIZON_MIN,
            "flow_sources": {
                "primary": "Binance SPOT 5m klines (N2S)",
                "comparison": "Binance USDT-perp 5m klines (N2P)",
            },
            "control": f"up to {nc.N2_CONTROLS_PER_PARENT} matched 15m boundaries (same UTC hour, preceding 15m Bybit return quintile, ATR quintile) outside +-{nc.N2_CONTROL_EXCLUSION_MIN} min of any flow flag; control side = sign of the preceding 15m return",
            "incremental_question": "spot-only cohort (spot in top decile, perp not) and parent-minus-matched-control",
            "replication": "MISSING second spot venue -> exploratory screen; no persistent spot-leadership claim",
            "kill": [
                "incremental over matched price/volume controls absent",
                "remaining effect ~1-5 bps",
                "effect disappears after the 1-minute embargo (no-embargo vs post-embargo)",
                "cadence collapses after dedup",
            ],
        },
        "N4": {
            "sessions": [
                {"name": n, "tz": tz, "local_anchor": f"{h:02d}:{m:02d}"}
                for n, tz, h, m in ne.N4_SESSIONS
            ],
            "rule": "first completed 15m after the anchor (three 5m bars): return = close/open - 1, volume = sum; require volume > median of the previous 30 comparable weekday sessions of the same anchor; LONG if return > 0, SHORT if < 0",
            "primary_hold_min": ne.N4_HOLD_MIN,
            "weekday_filter": "local weekday Mon-Fri; exchange holidays NOT excluded (no calendar library on this host) -> recorded limitation",
            "dst": "zoneinfo local wall clock; non-existent local times (spring-forward) skipped",
            "primary_analysis": "POOLED over the three sessions; per-session rows are secondary reporting only (no best-session selection)",
            "kill": [
                "pooled gross mean below the 25 bps hurdle",
                "one session or block > 50% of positive PnL",
                "incremental cadence after overlap with N1/N2 negligible",
            ],
        },
        "costs_bps": {
            "scenarios": list(COST_SCENARIOS_BPS),
            "base": BASE_COST_BPS,
            "stress": STRESS_COST_BPS,
            "model": "taker/taker round trip subtracted once from the benchmark gross",
        },
        "gates": mx.GATES,
        "sample_requirement": {
            "min_parents": mx.SAMPLE_MIN_PARENTS,
            "min_months": mx.SAMPLE_MIN_MONTHS,
        },
        "cadence_cuts_per_day": {
            "core": nc.CADENCE_CORE_MIN_PER_DAY,
            "satellite": nc.CADENCE_SATELLITE_MIN_PER_DAY,
            "min_months": nc.MIN_MONTHS_FOR_CADENCE,
        },
        "overlap_replay": {
            "engines": list(nc.ENGINE_ORDER),
            "rule": "one open signal at a time, fixed tie order N1 > N2S > N4, horizons as above; N2P excluded from the union (comparison only)",
        },
        "verdict_sets": {"edge": list(EDGE_VERDICTS), "cadence": list(CADENCE_VERDICTS)},
    }


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# ----------------------------------------------------------------------------- run
def run_n(
    data_dir: Path,
    out_root: Path,
    start: date | None = None,
    end: date | None = None,
    run_id: str | None = None,
) -> NRunResult:
    assert RESEARCH_ONLY and not ORDER_CAPABLE
    run_id = run_id or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    # ---- data + inventory v2 + E4 audit (no outcome computed yet)
    b5, _p5, days5 = load_bybit_bars(data_dir, "5m", start, end)
    b1, _p1, _d1 = load_bybit_bars(data_dir, "1m", start, end)
    spot, perp, oi = load_spot_5m(data_dir), load_perp_5m(data_dir), load_oi(data_dir)
    audit = e4_collection_audit(data_dir)
    (out / "e4_collection_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True, default=str) + "\n"
    )
    inv = inventory_v2(data_dir, spot, perp, b5.open_ms, len(oi), audit)
    (out / "data_inventory_v2.json").write_text(
        json.dumps(inv, indent=2, sort_keys=True, default=str) + "\n"
    )
    files += ["e4_collection_audit.json", "data_inventory_v2.json"]
    first_day, last_day = (days5[0], days5[-1]) if days5 else (None, None)
    hashes = _package_hashes()
    prereg = preregistration(run_id, first_day, last_day, hashes)
    prereg_sha = _sha(prereg)
    man_path = out / "experiment_manifest_n1_n2_n4.json"
    man_path.write_text(
        json.dumps(
            {"preregistration": prereg, "preregistration_sha256": prereg_sha},
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    files.append("experiment_manifest_n1_n2_n4.json")

    # ---- screens
    atr, er = wilder_atr(b5, ATR_PERIOD), efficiency_ratio(b5, ER_BARS)
    ctx = ne.NContext(
        b5,
        b1,
        atr,
        er,
        flow_spot=ne.flow_dict(
            spot["open_time_ms"].to_numpy(),
            spot["quote_volume"].to_numpy(),
            spot["taker_buy_quote_volume"].to_numpy(),
        )
        if spot is not None
        else {},
        flow_perp=ne.flow_dict(
            perp["open_time_ms"].to_numpy(),
            perp["quote_volume"].to_numpy(),
            perp["taker_buy_quote_volume"].to_numpy(),
        )
        if perp is not None
        else {},
        oi=oi,
    )
    screens = {
        "N1": ne.run_n1(ctx),
        "N2S": ne.run_n2(ctx, "SPOT"),
        "N2P": ne.run_n2(ctx, "PERP"),
        "N4": ne.run_n4(ctx),
    }
    for name, fn in (
        ("N1", "n1_parents"),
        ("N2S", "n2_parents"),
        ("N2P", "n2p_parents"),
        ("N4", "n4_parents"),
    ):
        files += _write_table([p.as_row() for p in screens[name].parents], out / fn)
    trades = {k: [p for p in v.parents if p.traded] for k, v in screens.items()}

    # ---- overlap replay (union of N1, N2S, N4) and census
    union = {k: trades[k] for k in nc.ENGINE_ORDER}
    overlap_rows = nc.overlap_replay(union)
    available = {r.parent_id for r in overlap_rows if r.available}
    fd, ld = first_day or "1970-01-01", last_day or "1970-01-01"
    cens = {k: nc.census(v, available, fd, ld) for k, v in screens.items()}
    files += _write_table(
        [r for k in screens for r in nc.census_rows(k, cens[k])],
        out / "frequency_census",
        parquet=False,
    )
    ov_summary = nc.overlap_summary(union, fd, ld)
    files += _write_table(ov_summary, out / "overlap_analysis", parquet=False)
    files += _write_table([r.__dict__ for r in overlap_rows], out / "overlap_replay", parquet=False)

    # ---- gross markouts, controls, spot vs perp, costs, blocks, gates
    mk_rows = [r for k in screens for r in nc.markout_rows(k, trades[k])]
    ctrl = (
        nc.matched_controls(ctx, screens["N2S"], screens["N2P"], ne.N2_HORIZON_MIN)
        if spot is not None
        else None
    )
    svp = nc.spot_vs_perp(screens["N2S"], screens["N2P"])
    mk_rows += [svp["spot_only"], svp["spot_and_perp"]]
    if ctrl is not None:
        mk_rows.append(
            {
                "engine": "N2S",
                "subset": "MATCHED_CONTROLS",
                "n": ctrl.summary["parents_with_controls"],
                "gross_bps_mean": ctrl.summary["control_gross_bps_mean"],
            }
        )
        mk_rows.append(
            {
                "engine": "N2S",
                "subset": "INCREMENTAL_VS_CONTROLS",
                "n": ctrl.summary["parents_with_controls"],
                "gross_bps_mean": ctrl.summary["incremental_bps_mean"],
                "gross_bps_lb95_day_cluster": ctrl.summary["incremental_bps_lb95_day_cluster"],
            }
        )
        files += _write_table(ctrl.rows, out / "n2_matched_controls", parquet=False)
    files += _write_table(mk_rows, out / "gross_markouts", parquet=False)
    files += _write_table(
        [r for k in screens for r in nc.cost_rows(k, trades[k])], out / "cost_screen", parquet=False
    )
    files += _write_table(
        [r for k in screens for r in nc.block_rows(k, trades[k])],
        out / "chronological_blocks",
        parquet=False,
    )
    gates = {
        k: mx.gates(trades[k], first_day, last_day, n_parents=len(screens[k].parents))
        for k in screens
    }

    # ---- verdicts (EDGE from the predeclared gates; CADENCE from the census)
    ov_by = {r["engine"]: r for r in ov_summary}
    verdicts: dict[str, dict[str, Any]] = {}
    for k in ("N1", "N2S", "N4"):
        c = cens[k]
        epd = c["eligible_entries"]["per_calendar_day"]
        inc = ov_by.get(k, {}).get("incremental_available_per_day")
        edge = nc.edge_verdict(gates[k])
        cad = nc.cadence_verdict(epd, inc, gates[k]["months"])
        kill = _kill_flags(k, gates[k], mk_rows, ctrl.summary if ctrl else None, svp, c, inc)
        if any(kill.values()) and edge["verdict"] not in ("INCONCLUSIVE",):
            edge = {
                "verdict": "REJECT",
                "reason": edge["reason"]
                + "; kill criteria: "
                + ", ".join(n for n, v in kill.items() if v),
            }
        assert edge["verdict"] in EDGE_VERDICTS and cad["verdict"] in CADENCE_VERDICTS
        verdicts[k] = {"edge": edge, "cadence": cad, "kill_flags": kill}

    results = {
        "census": {
            k: {s: {kk: vv for kk, vv in v[s].items() if kk != "per_month"} for s in v}
            for k, v in cens.items()
        },
        "gates": gates,
        "overlap": ov_summary,
        "n2_controls": ctrl.summary if ctrl else {"status": "SPOT_DATA_MISSING"},
        "n2_spot_vs_perp": svp,
        "n4_sessions": screens["N4"].extra.get("sessions"),
        "verdicts": verdicts,
    }
    manifest = {
        "schema": "intraday_portfolio_experiment_manifest_n-1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "research_only": RESEARCH_ONLY,
        "order_capable": ORDER_CAPABLE,
        "python": platform.python_version(),
        "preregistration": prereg,
        "preregistration_sha256": prereg_sha,
        "data_versions": {
            "bybit_5m_days": len(days5),
            "bybit_5m_first": first_day,
            "bybit_5m_last": last_day,
            "binance_spot_5m_rows": int(spot.height) if spot is not None else 0,
            "binance_perp_5m_rows": int(perp.height) if perp is not None else 0,
            "binance_oi_rows": len(oi),
            "spot_manifest_sha256": _sha_file(
                Path(__file__).resolve().parents[3]
                / "manifests"
                / "binance_spot_klines_5m_manifest.jsonl"
            ),
        },
        "results": results,
        "files": [*sorted(set(files)), "SUMMARY.md"],
    }
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    (out / "SUMMARY.md").write_text(_summary(run_id, manifest, inv, audit, mk_rows))
    files.append("SUMMARY.md")
    return NRunResult(run_id, out, verdicts, sorted(set(files)), results)


def _sha_file(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _kill_flags(
    engine: str,
    gate: dict[str, Any],
    mk: list[dict[str, Any]],
    ctrl: dict[str, Any] | None,
    svp: dict[str, Any],
    c: dict[str, Any],
    inc: float | None,
) -> dict[str, bool]:
    s16 = gate["summary_16"]
    all_row = next((r for r in mk if r["engine"] == engine and r["subset"] == "ALL"), {})
    g = all_row.get("gross_bps_mean")
    n = s16.get("n", 0)
    flags: dict[str, bool] = {}
    if n == 0:
        return flags
    flags["gross_below_base_cost"] = (g or 0) < BASE_COST_BPS
    flags["chronological_unstable"] = gate["positive_blocks"] < mx.BLOCKS_POSITIVE_MIN
    if engine == "N1":
        imp = all_row.get("abs_impulse_bps_mean") or 0
        flags["movement_mostly_completed_impulse"] = bool(imp > 0 and (g or 0) < 0.25 * imp)
        flags["core_cadence_lt_0_75"] = (
            c["eligible_entries"]["per_calendar_day"] or 0
        ) < nc.CADENCE_CORE_MIN_PER_DAY
    if engine == "N2S":
        incr = (ctrl or {}).get("incremental_bps_mean")
        flags["incremental_over_controls_absent"] = incr is None or incr <= 0
        flags["remaining_effect_1_to_5_bps"] = (g is not None) and 0 < g <= 5
        ne_ = all_row.get("gross_no_embargo_bps_mean")
        flags["effect_disappears_after_embargo"] = bool(
            ne_ is not None and ne_ > 5 and (g or 0) <= 5
        )
        flags["cadence_collapses_after_dedup"] = (
            c["independent_parents"]["total"] or 0
        ) < 0.25 * max(1, c["raw_flags"]["total"]) and (
            c["eligible_entries"]["per_calendar_day"] or 0
        ) < nc.CADENCE_SATELLITE_MIN_PER_DAY
    if engine == "N4":
        flags["pooled_gross_below_hurdle"] = (g or 0) < mx.GATES["mean_gross_bps_min"]
        sess = [
            r for r in mk if r["engine"] == "N4" and r["subset"].startswith("session_") and r["n"]
        ]
        pos = {r["subset"]: max(0.0, (r["gross_bps_mean"] or 0) * r["n"]) for r in sess}
        tot = sum(pos.values())
        flags["gains_concentrated_one_session"] = bool(tot > 0 and max(pos.values()) / tot > 0.5)
        flags["gains_concentrated_one_block"] = bool(
            (gate.get("max_block_share_of_positive_pnl") or 0) > 0.5
        )
        flags["incremental_cadence_negligible"] = (inc or 0) < 0.1
    return flags


# ----------------------------------------------------------------------------- summary
def _summary(
    run_id: str,
    man: dict[str, Any],
    inv: dict[str, Any],
    audit: dict[str, Any],
    mk: list[dict[str, Any]],
) -> str:
    r = man["results"]
    o = [f"# SUMMARY — N1 / N2 / N4 preregistered screens `{run_id}`", ""]
    o.append(
        f"Preregistration sha256 `{man['preregistration_sha256'][:16]}…` written before any outcome; frozen rules in `experiment_manifest_n1_n2_n4.json`."
    )
    o.append("")
    o.append("| engine | EDGE | CADENCE | edge reason | cadence reason |")
    o.append("|---|---|---|---|---|")
    for k, v in r["verdicts"].items():
        o.append(
            f"| {k} | **{v['edge']['verdict']}** | **{v['cadence']['verdict']}** | {v['edge']['reason']} | {v['cadence']['reason']} |"
        )
    o.append("")
    o.append("## Event census (per calendar day, zero-event days included)")
    o.append("")
    o.append(
        "| engine | raw flags | parents | eligible | portfolio-available | parents/day | eligible/day | available/day | zero-eligible days | p10/p50/p90 | long/short (eligible) |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, c in r["census"].items():
        rf, pa, el, av = (
            c["raw_flags"],
            c["independent_parents"],
            c["eligible_entries"],
            c["portfolio_available"],
        )
        o.append(
            f"| {k} | {rf['total']} | {pa['total']} | {el['total']} | {av['total']} | {_f(pa['per_calendar_day'], 3)} | {_f(el['per_calendar_day'], 3)} | "
            f"{_f(av['per_calendar_day'], 3)} | {el['zero_event_days']}/{el['calendar_days']} | {el['daily_p10']}/{el['daily_p50']}/{el['daily_p90']} | {el['long']}/{el['short']} |"
        )
    o.append("")
    o.append("## Gross markouts (benchmark entry after the 1-minute embargo; side-adjusted bps)")
    o.append("")
    o.append(
        "| engine | subset | n | gross mean | median | LB95 day-cluster | win | no-embargo mean | 60m diag | |impulse| | MFE | MAE |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in mk:
        o.append(
            f"| {row['engine']} | {row['subset']} | {row.get('n')} | {_f(row.get('gross_bps_mean'))} | {_f(row.get('gross_bps_median'))} | "
            f"{_f(row.get('gross_bps_lb95_day_cluster'))} | {_f(row.get('win_rate_gross'), 3)} | {_f(row.get('gross_no_embargo_bps_mean'))} | "
            f"{_f(row.get('gross_60m_diag_bps_mean'))} | {_f(row.get('abs_impulse_bps_mean'))} | {_f(row.get('mfe_bps_mean'))} | {_f(row.get('mae_bps_mean'))} |"
        )
    o.append("")
    o.append("## Cost screen (taker/taker; 14 sensitivity / 16 base / 24 stress)")
    o.append("")
    o.append(
        "| engine | n | gross | net@14 | net@16 | LB95@16 | PF@16 | net@24 | PF@24 | net R@16 | net USDT@16 | blocks+ | max block share | w/o top-5 |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, g in r["gates"].items():
        s14, s16, s24 = g["scenarios"]["14.0"], g["summary_16"], g["summary_24"]
        o.append(
            f"| {k} | {s16.get('n', 0)} | {_f(s16.get('gross_bps_mean'))} | {_f(s14.get('net_bps_mean'))} | {_f(s16.get('net_bps_mean'))} | "
            f"{_f(s16.get('net_bps_lower_bound_95_day_cluster'))} | {_f(s16.get('profit_factor'))} | {_f(s24.get('net_bps_mean'))} | {_f(s24.get('profit_factor'))} | "
            f"{_f(s16.get('net_r_sum'))} | {_f(s16.get('net_usdt'))} | {g['positive_blocks']}/4 | {_f(g['max_block_share_of_positive_pnl'], 3)} | {_f(g['fragility_mean_net_without_top5_bps'])} |"
        )
    o.append("")
    o.append("## Overlap / incremental cadence (chronological one-signal replay of N1 + N2S + N4)")
    o.append("")
    o.append(
        "| engine | eligible | standalone/day | available in union | available/day | overlap rate | same-dir blocked | opposite blocked | incremental vs others | incremental/day | profitable available @16 | profitable/day |"
    )
    o.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in r["overlap"]:
        o.append(
            f"| {row['engine']} | {row.get('eligible')} | {_f(row.get('standalone_eligible_per_day'), 3)} | {row.get('available_in_union')} | {_f(row.get('available_per_day'), 3)} | "
            f"{_f(row.get('overlap_rate'), 3)} | {row.get('same_direction_overlap', '')} | {row.get('opposite_direction_conflict', '')} | {row.get('incremental_available_vs_others', '')} | "
            f"{_f(row.get('incremental_available_per_day'), 3)} | {row.get('profitable_available_at_16bps')} | {_f(row.get('profitable_available_per_day'), 3)} |"
        )
    o.append("")
    c2 = r["n2_controls"]
    o.append("## N2: spot flow vs matched price/volume controls and vs perp flow")
    o.append("")
    o.append(
        f"- Matched controls: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in c2.items()}, sort_keys=True)}"
    )
    s = r["n2_spot_vs_perp"]
    o.append(
        f"- Spot flags {s['spot_flags']}, perp flags {s['perp_flags']}, in both {s['flags_in_both']}, spot-only share {_f(s['spot_only_share_of_spot_flags'], 3)}"
    )
    o.append(
        f"- SPOT_ONLY cohort: n {s['spot_only']['n']}, gross {_f(s['spot_only']['gross_bps_mean'])} bps (LB95 {_f(s['spot_only']['gross_bps_lb95_day_cluster'])}); SPOT_AND_PERP: n {s['spot_and_perp']['n']}, gross {_f(s['spot_and_perp']['gross_bps_mean'])}; PERP all: n {s['perp_all']['n']}, gross {_f(s['perp_all']['gross_bps_mean'])}"
    )
    o.append(
        "- Replication: single spot venue (Binance) -> exploratory screen; persistent spot leadership is NOT claimed."
    )
    o.append("")
    o.append(f"## N4 sessions (secondary): {json.dumps(r['n4_sessions'], sort_keys=True)}")
    o.append("")
    o.append("## Kill flags")
    o.append("")
    for k, v in r["verdicts"].items():
        o.append(f"- {k}: {json.dumps(v['kill_flags'], sort_keys=True)}")
    o.append("")
    o.append("## Data coverage")
    o.append("")
    mo = inv["minute_ohlcv"]
    o.append(
        f"- Bybit 1m/5m trade-built bars {man['data_versions']['bybit_5m_first']}..{man['data_versions']['bybit_5m_last']} ({man['data_versions']['bybit_5m_days']} days); holdout from {inv['holdout_locked_never_read_from']} never read."
    )
    o.append(
        f"- Binance perp 5m klines: {mo['binance_perp_5m_klines'].get('status')} ({mo['binance_perp_5m_klines'].get('rows')} rows, taker-buy quote {mo['binance_perp_5m_klines'].get('fields', {}).get('taker_buy_quote_volume')}); Binance spot 5m klines: {mo['binance_spot_5m_klines'].get('status')} ({mo['binance_spot_5m_klines'].get('rows')} rows, gaps > 5m {mo['binance_spot_5m_klines'].get('gaps_gt_5m')}); sync share spot {_f(inv['synchronized_spot_data']['share_with_binance_spot_row'], 4)}, perp {_f(inv['synchronized_spot_data']['share_with_binance_perp_row'], 4)}."
    )
    o.append(
        f"- Spot venues: {inv['spot_venue_identifiers']['historical']}; second venue {inv['spot_venue_identifiers']['replication_second_venue']}"
    )
    o.append(
        f"- OI (Binance, recorded only): {inv['funding_oi_fields']['binance_open_interest_5m']['status']}; E4 raw coverage: {inv['e4_coverage'].get('n_complete_days')} complete days of {inv['e4_coverage'].get('n_days_touched')} touched."
    )
    o.append("")
    o.append("## E4 collection audit (read-only)")
    o.append("")
    for k, v in audit["fields"].items():
        o.append(f"- {k}: **{v['status']}** — {v['evidence']}")
    o.append("")
    o.append(
        "Proposed collector-only change (NOT implemented): "
        + json.dumps(
            audit["proposed_minimal_collector_only_change_NOT_IMPLEMENTED"], sort_keys=True
        )
    )
    o.append("")
    o.append(
        "Nothing here is runner-ready; a verdict of ELIGIBLE FOR FULL POLICY REPLAY would only start the next research phase."
    )
    o.append("")
    return "\n".join(o)


_ = math
