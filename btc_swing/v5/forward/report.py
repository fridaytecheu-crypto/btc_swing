"""Immutable daily forward snapshots (reports/forward/<YYYY-MM-DD>.md + .json) and the live status.

A day's report is written once and never overwritten. A report generated before the UTC day has
ended is labelled PARTIAL in its title and file name (<date>_partial_<HHMM>.md) so the final one
can still be written at 00:05 UTC the next day."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.research.phase21_report import _n, _p, _table
from btc_swing.v5.config import V5Family
from btc_swing.v5.forward.config import ForwardContextLike, ForwardPaths
from btc_swing.v5.forward.pipeline import DAY_MS, _f, _iso, _read_jsonl
from btc_swing.v5.forward.raw import load_forward_bars

HZ = [0.25, 0.5, 1, 2, 4, 8, 12]


def _day_bounds(day: str) -> tuple[int, int]:
    d0 = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    return d0, d0 + DAY_MS


def _mean(xs: list[float]) -> float:
    return float(np.mean(xs)) if xs else math.nan


def collector_health(paths: ForwardPaths, day: str) -> dict[str, Any]:
    st: dict[str, Any] = {}
    if (paths.raw / "state.json").exists():
        st = json.loads((paths.raw / "state.json").read_text())
    stats = st.get("stats", {})
    files = (
        sorted(p for p in (paths.raw / day).glob("*.jsonl*")) if (paths.raw / day).exists() else []
    )
    size = sum(p.stat().st_size for p in files)
    newest = max((p.stat().st_mtime for p in files), default=0.0)
    pid_alive = False
    if paths.run_pid.exists():
        try:
            import os

            os.kill(int(paths.run_pid.read_text().strip()), 0)
            pid_alive = True
        except (OSError, ValueError):
            pid_alive = False
    return {
        "process_alive": pid_alive,
        "state_saved_at": st.get("saved_at"),
        "stats_current_process": {
            k: stats.get(k)
            for k in (
                "uptime_seconds",
                "messages",
                "messages_per_second",
                "duplicates",
                "sequence_gaps",
                "sequence_gap_rate",
                "reconnects",
                "stale_timeouts",
                "errors",
                "bytes_written",
            )
            if k in stats
        },
        "by_topic_current_process": stats.get("by_topic", {}),
        "latency_ms_current_process": stats.get("latency_ms", {}),
        "raw_files_today": len(files),
        "raw_bytes_today": size,
        "newest_raw_file_mtime": datetime.fromtimestamp(newest, tz=UTC).isoformat()
        if newest
        else None,
        "last_orderbook_u": st.get("last_orderbook_u"),
    }


def day_snapshot(ctx: ForwardContextLike, day: str, now_ms: int | None = None) -> dict[str, Any]:
    paths = ctx.paths
    now = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    d0, d1 = _day_bounds(day)
    partial = now < d1
    bars = load_forward_bars(paths.bars_dir)
    db = (
        bars.filter((pl.col("open_time_ms") >= d0) & (pl.col("open_time_ms") < d1))
        if bars.height
        else bars
    )
    sigs = _read_jsonl(paths.signals_file)
    outs = _read_jsonl(paths.outcomes_file)
    trades = _read_jsonl(paths.paper_trades)
    state = json.loads(paths.paper_state.read_text()) if paths.paper_state.exists() else {}
    cycles = [c for c in _read_jsonl(paths.cycle_log) if day in str(c.get("cycle_at", ""))]
    sig_day = [s for s in sigs if d0 <= int(s["t_ms"]) < d1]
    tr_day = [t for t in trades if d0 <= int(t["entry_ms"]) < d1]
    closed_day = [t for t in trades if d0 <= int(t["exit_ms"]) < d1]
    out_day = [o for o in outs if d0 <= int(o["t_ms"]) < d1]

    def fam_rows(items: list[dict[str, Any]], key: str = "family") -> list[dict[str, Any]]:
        rows = []
        for fam in [f.value for f in V5Family]:
            for side in ("LONG", "SHORT"):
                sub = [x for x in items if x[key] == fam and x["side"] == side]
                rows.append({"family": fam, "side": side, "n": len(sub)})
        return rows

    liq: dict[str, Any] = {}
    if db.height and "liq_long_qty" in db.columns:
        vol = float(db["volume"].sum())
        lq, sq = float(db["liq_long_qty"].sum()), float(db["liq_short_qty"].sum())
        per_bar = (db["liq_long_qty"] + db["liq_short_qty"]).to_numpy().astype(float)
        liq = {
            "n_long": int(db["liq_n_long"].sum()),
            "n_short": int(db["liq_n_short"].sum()),
            "long_qty_btc": lq,
            "short_qty_btc": sq,
            "long_notional": float(db["liq_long_notional"].sum()),
            "short_notional": float(db["liq_short_notional"].sum()),
            "max_single_qty_btc": _f(db["liq_max_single_qty"].max()) if db.height else math.nan,
            "max_5m_burst_qty_btc": float(per_bar.max()) if len(per_bar) else math.nan,
            "intensity_btc_per_hour": (lq + sq) / (db.height / 12.0) if db.height else math.nan,
            "liq_to_volume_ratio": (lq + sq) / vol if vol > 0 else math.nan,
            "bars_with_liquidations": int((per_bar > 0).sum()),
        }
        if db.height > 3:
            oi_chg = db["oi_last"].to_numpy().astype(float)
            oi_chg = np.diff(oi_chg, prepend=np.nan) / np.where(oi_chg > 0, oi_chg, np.nan)
            delta = (db["buy_qty"] - db["sell_qty"]).to_numpy().astype(float)
            ok = ~np.isnan(oi_chg) & ~np.isnan(per_bar)
            liq["corr_liq_vs_oi_chg_5m"] = (
                float(np.corrcoef(per_bar[ok], oi_chg[ok])[0, 1])
                if ok.sum() > 3 and per_bar[ok].std() > 0
                else math.nan
            )
            liq["corr_liq_vs_abs_delta_5m"] = (
                float(np.corrcoef(per_bar, np.abs(delta))[0, 1])
                if per_bar.std() > 0 and np.abs(delta).std() > 0
                else math.nan
            )
            liq["corr_long_liq_vs_delta_5m"] = (
                float(np.corrcoef(db["liq_long_qty"].to_numpy().astype(float), delta)[0, 1])
                if db["liq_long_qty"].std() and delta.std() > 0
                else math.nan
            )
    pnl_day = sum(float(t["POSITION_PNL"]) for t in closed_day)
    snap: dict[str, Any] = {
        "day": day,
        "partial": partial,
        "generated_at": _iso(now),
        "observation_start": _iso(ctx.start_ms),
        "frozen_v5_config_hash": ctx.cfg.config_hash,
        "collector": collector_health(paths, day),
        "bars": {
            "forward_bars_today": db.height,
            "expected_if_full_day": 288,
            "gap_filled_today": int((db["gap_filled"] == 1.0).sum())
            if db.height and "gap_filled" in db.columns
            else 0,
            "bars_without_trades": int((db["trades"] == 0).sum()) if db.height else 0,
            "messages_today": int(db["n_msgs"].sum())
            if db.height and "n_msgs" in db.columns
            else 0,
            "latency_p50_ms_median": _f(db["latency_p50_ms"].median())
            if db.height and "latency_p50_ms" in db.columns
            else math.nan,
            "book_valid_share": _f(db["book_valid"].mean())
            if db.height and "book_valid" in db.columns
            else math.nan,
            "kline_confirmed_share": _f(db["kline_confirmed"].mean())
            if db.height and "kline_confirmed" in db.columns
            else math.nan,
            "first_bar": _iso(int(db["open_time_ms"].min())) if db.height else None,  # type: ignore[arg-type]
            "last_bar_close": _iso(int(db["close_time_ms"].max())) if db.height else None,  # type: ignore[arg-type]
        },
        "warmup": cycles[-1].get("warmup") if cycles else None,
        "cycles_today": len(cycles),
        "signals_today": len(sig_day),
        "signals_today_by_family_side": fam_rows(sig_day),
        "signals_today_list": [
            {
                "t": s["t"],
                "family": s["family"],
                "side": s["side"],
                "strength": s["strength"],
                "first_in_cluster": s["first_in_cluster"],
                "stop_pct": s["plan"]["stop_distance_pct"],
                "entry": s["plan"]["hypothetical_entry"],
                "stop": s["plan"]["stop_price_at_event_close"],
                "tp1": s["plan"]["tp1"],
                "tp2": s["plan"]["tp2"],
            }
            for s in sig_day
        ],
        "signals_cumulative": len(sigs),
        "paper_trades_opened_today": len(tr_day),
        "paper_trades_closed_today": len(closed_day),
        "paper_closed_today_list": [
            {
                "family": t["family"],
                "side": t["side"],
                "entry": t["entry_ms"],
                "exit": t["exit_ms"],
                "exit_reason": t["exit_reason"],
                "net_R": t["R_MULTIPLE"],
                "gross_R": t["R_MULTIPLE_GROSS"],
                "pnl": t["POSITION_PNL"],
            }
            for t in closed_day
        ],
        "open_paper_position": state.get("open_position"),
        "daily_pnl_usdt": pnl_day,
        "cumulative": {
            "closed_trades": state.get("closed_trades"),
            "cumulative_net_pnl": state.get("cumulative_net_pnl"),
            "equity_realised": state.get("equity_realised"),
            "equity_mtm": state.get("equity_mtm"),
            "mean_gross_R": state.get("mean_gross_R"),
            "mean_net_R": state.get("mean_net_R"),
            "win_rate": state.get("win_rate"),
            "max_drawdown_frac": state.get("max_drawdown_frac"),
            "integrity_errors": state.get("integrity_errors"),
        },
        "family_breakdown_cumulative": [
            {
                "family": fam,
                "side": side,
                "n": len(sub),
                "mean_gross_R": _mean([float(t["R_MULTIPLE_GROSS"]) for t in sub]),
                "mean_net_R": _mean([float(t["R_MULTIPLE"]) for t in sub]),
                "sum_pnl": sum(float(t["POSITION_PNL"]) for t in sub),
            }
            for fam in [f.value for f in V5Family]
            for side in ("ALL", "LONG", "SHORT")
            for sub in [
                [t for t in trades if t["family"] == fam and (side == "ALL" or t["side"] == side)]
            ]
        ],
        "matured_outcomes_today": len(out_day),
        "outcomes_cumulative": {
            "n": len(outs),
            **{
                f"mean_fwd_{h:g}h": _mean(
                    [float(o[f"fwd_{h:g}h"]) for o in outs if o.get(f"fwd_{h:g}h") is not None]
                )
                for h in HZ
            },
            "mean_hyp_gross_R": _mean(
                [float(o["hyp_gross_R"]) for o in outs if o.get("hyp_gross_R") is not None]
            ),
            "mean_hyp_net_R": _mean(
                [float(o["hyp_net_R"]) for o in outs if o.get("hyp_net_R") is not None]
            ),
        },
        "liquidations_today": liq,
    }
    return snap


def render_day(snap: dict[str, Any]) -> str:
    c, b, cu, lq = snap["collector"], snap["bars"], snap["cumulative"], snap["liquidations_today"]
    title = f"# BTC V5 forward observation — daily snapshot {snap['day']}" + (
        " (PARTIAL)" if snap["partial"] else ""
    )
    st = c.get("stats_current_process", {})
    lat = c.get("latency_ms_current_process", {})
    lines = [
        title,
        "",
        f"Generated {snap['generated_at'][:16]} UTC · observation start {snap['observation_start'][:16]} UTC · frozen V5 config `{snap['frozen_v5_config_hash'][:12]}` · observational only (no orders, no credentials, no rule changes).",
        "",
        "## Collector health",
        "",
        *_table(
            ["metric", "value"],
            [
                ["forward process alive", _n(c.get("process_alive"))],
                ["collector state saved at", str(c.get("state_saved_at"))[:19]],
                ["messages (current process)", str(st.get("messages"))],
                ["messages / s", _n(st.get("messages_per_second"), 1)],
                [
                    "order-book sequence gaps / gap rate",
                    f"{st.get('sequence_gaps')} / {_p(st.get('sequence_gap_rate'), 3)}",
                ],
                ["duplicates suppressed", str(st.get("duplicates"))],
                [
                    "reconnects / stale timeouts / errors",
                    f"{st.get('reconnects')} / {st.get('stale_timeouts')} / {st.get('errors')}",
                ],
                [
                    "latency ms p50 / p90 / p99 / max",
                    f"{_n(lat.get('p50'), 0)} / {_n(lat.get('p90'), 0)} / {_n(lat.get('p99'), 0)} / {_n(lat.get('max'), 0)}",
                ],
                [
                    "by topic",
                    ", ".join(f"{k} {v}" for k, v in c.get("by_topic_current_process", {}).items()),
                ],
                [
                    "raw files / bytes today",
                    f"{c.get('raw_files_today')} / {_n((c.get('raw_bytes_today') or 0) / 1e6, 1)} MB",
                ],
                ["newest raw file", str(c.get("newest_raw_file_mtime"))[:19]],
                [
                    "5m bars today (of 288) / gap-filled / without trades",
                    f"{b['forward_bars_today']} / {b['gap_filled_today']} / {b['bars_without_trades']}",
                ],
                ["messages inside today's bars", str(b["messages_today"])],
                ["median in-bar latency p50 (ms)", _n(b.get("latency_p50_ms_median"), 0)],
                [
                    "order-book valid share / kline confirmed share",
                    f"{_p(b.get('book_valid_share'), 0)} / {_p(b.get('kline_confirmed_share'), 0)}",
                ],
                [
                    "first bar / last bar close",
                    f"{str(b.get('first_bar'))[:16]} / {str(b.get('last_bar_close'))[:16]}",
                ],
                ["cycles today", str(snap["cycles_today"])],
            ],
        ),
        "Feature warm-up at the last cycle (True = the frozen z-score is available): "
        + (", ".join(f"{k} {v}" for k, v in (snap.get("warmup") or {}).items()) or "n/a")
        + ".",
        "",
        "## Signals (prospective snapshots, written before any outcome is known)",
        "",
        *_table(
            ["family", "side", "today"],
            [[r["family"], r["side"], str(r["n"])] for r in snap["signals_today_by_family_side"]],
        ),
        f"- Signals today: {snap['signals_today']}; cumulative since start: {snap['signals_cumulative']}.",
        "",
    ]
    if snap["signals_today_list"]:
        lines += [
            *_table(
                [
                    "time",
                    "family",
                    "side",
                    "strength",
                    "first-in-cluster",
                    "hyp. entry",
                    "stop",
                    "stop %",
                    "TP1",
                    "TP2",
                ],
                [
                    [
                        s["t"][11:16],
                        s["family"],
                        s["side"],
                        _n(s["strength"], 2),
                        _n(s["first_in_cluster"]),
                        _n(s["entry"], 1),
                        _n(s["stop"], 1),
                        _n(s["stop_pct"], 2),
                        _n(s["tp1"], 1),
                        _n(s["tp2"], 1),
                    ]
                    for s in snap["signals_today_list"]
                ],
            )
        ]
    op = snap.get("open_paper_position")
    lines += [
        "## Paper ledger (virtual; frozen V5 execution, 0.25% risk, no orders)",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "hypothetical trades opened today / closed today",
                    f"{snap['paper_trades_opened_today']} / {snap['paper_trades_closed_today']}",
                ],
                [
                    "open paper position",
                    f"{op['family']} {op['side']} entry {_n(op['entry_price'], 1)} stop {_n(op['final_stop'], 1)} unrealised {_n(op['POSITION_PNL'], 2)} USDT"
                    if op
                    else "none",
                ],
                ["daily P&L (closed today, USDT)", _n(snap["daily_pnl_usdt"], 2)],
                ["cumulative closed trades", str(cu.get("closed_trades"))],
                ["cumulative net P&L (USDT)", _n(cu.get("cumulative_net_pnl"), 2)],
                [
                    "equity realised / mark-to-market",
                    f"{_n(cu.get('equity_realised'), 2)} / {_n(cu.get('equity_mtm'), 2)}",
                ],
                [
                    "gross expectancy (R) / net expectancy (R)",
                    f"{_n(cu.get('mean_gross_R'))} / {_n(cu.get('mean_net_R'))}",
                ],
                [
                    "win rate / max drawdown",
                    f"{_p(cu.get('win_rate'), 0)} / {_p(cu.get('max_drawdown_frac'))}",
                ],
                ["ledger integrity errors", str(cu.get("integrity_errors") or [])],
            ],
        ),
    ]
    if snap["paper_closed_today_list"]:
        lines += [
            *_table(
                ["family", "side", "exit reason", "gross R", "net R", "P&L"],
                [
                    [
                        t["family"],
                        t["side"],
                        t["exit_reason"],
                        _n(t["gross_R"]),
                        _n(t["net_R"]),
                        _n(t["pnl"], 2),
                    ]
                    for t in snap["paper_closed_today_list"]
                ],
            )
        ]
    lines += [
        "Family breakdown (cumulative closed paper trades):",
        "",
        *_table(
            ["family", "side", "n", "gross R", "net R", "sum P&L"],
            [
                [
                    r["family"],
                    r["side"],
                    str(r["n"]),
                    _n(r["mean_gross_R"]),
                    _n(r["mean_net_R"]),
                    _n(r["sum_pnl"], 2),
                ]
                for r in snap["family_breakdown_cumulative"]
            ],
        ),
        "## Matured forward outcomes (12 h after the signal; written once)",
        "",
        *_table(
            ["metric", "value"],
            [
                [
                    "matured today / cumulative",
                    f"{snap['matured_outcomes_today']} / {snap['outcomes_cumulative']['n']}",
                ],
                *[
                    [
                        f"mean signed forward return {h:g} h",
                        _p(snap["outcomes_cumulative"].get(f"mean_fwd_{h:g}h"), 3),
                    ]
                    for h in HZ
                ],
                [
                    "mean hypothetical gross R / net R",
                    f"{_n(snap['outcomes_cumulative'].get('mean_hyp_gross_R'))} / {_n(snap['outcomes_cumulative'].get('mean_hyp_net_R'))}",
                ],
            ],
        ),
        "## Liquidation diagnostics (real Bybit allLiquidation stream; forward only, not a signal input)",
        "",
        *(
            _table(
                ["metric", "value"],
                [[k, _n(v, 4) if isinstance(v, float) else str(v)] for k, v in lq.items()],
            )
            if lq
            else ["- no liquidation data yet.", ""]
        ),
        "- Reading rule: nothing in this snapshot changes a V5 rule. Thresholds, families, stops and exits stay frozen for the whole observation window.",
        "",
    ]
    return "\n".join(lines)


def write_day_report(
    ctx: ForwardContextLike, day: str, out_dir: Path, now_ms: int | None = None
) -> Path | None:
    snap = day_snapshot(ctx, day, now_ms)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = snap["generated_at"][11:16].replace(":", "")
    base = f"{day}_partial_{stamp}" if snap["partial"] else day
    md, js = out_dir / f"{base}.md", out_dir / f"{base}.json"
    if md.exists():
        return None  # immutable: never overwrite
    js.write_text(json.dumps(snap, indent=1, sort_keys=True, default=str))
    md.write_text(render_day(snap))
    return md


def status(ctx: ForwardContextLike) -> dict[str, Any]:
    paths = ctx.paths
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    d0, d1 = _day_bounds(today)
    sigs = _read_jsonl(paths.signals_file)
    trades = _read_jsonl(paths.paper_trades)
    state = json.loads(paths.paper_state.read_text()) if paths.paper_state.exists() else {}
    cycles = _read_jsonl(paths.cycle_log)
    c = collector_health(paths, today)
    newest_raw = None
    raw_days = sorted(p for p in paths.raw.glob("*") if p.is_dir())
    if raw_days:
        files = sorted(raw_days[-1].glob("*.jsonl*"))
        if files:
            newest_raw = datetime.fromtimestamp(files[-1].stat().st_mtime, tz=UTC).isoformat()
    return {
        "now": datetime.now(UTC).isoformat(),
        "observation_start": _iso(ctx.start_ms),
        "frozen_v5_config_hash": ctx.cfg.config_hash,
        "collector": {
            "process_alive": c["process_alive"],
            "state_saved_at": c["state_saved_at"],
            "last_raw_write": newest_raw,
            "stats": c["stats_current_process"],
            "latency_ms": c["latency_ms_current_process"],
        },
        "last_cycle": {
            k: v
            for k, v in cycles[-1].items()
            if k
            in (
                "cycle_at",
                "last_bar_close",
                "new_bars",
                "bars_forward",
                "events_since_start",
                "warmup",
                "elapsed_s",
            )
        }
        if cycles
        else None,
        "open_paper_position": state.get("open_position"),
        "signals_today": sum(1 for s in sigs if d0 <= int(s["t_ms"]) < d1),
        "signals_total": len(sigs),
        "trades_today": sum(1 for t in trades if d0 <= int(t["entry_ms"]) < d1),
        "trades_total": len(trades),
        "cumulative_paper": {
            k: state.get(k)
            for k in (
                "equity_realised",
                "equity_mtm",
                "cumulative_net_pnl",
                "mean_gross_R",
                "mean_net_R",
                "win_rate",
                "max_drawdown_frac",
                "closed_trades",
            )
        },
    }
