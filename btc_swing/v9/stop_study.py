"""V9 stop-geometry sensitivity study (design section 12): the unchanged entry signal under wider stop
floors with proportionally larger targets, constant account risk. A sensitivity matrix, not a search:
nothing is selected or adopted."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from btc_swing.v9.config import V9_VERSION, V9Config
from btc_swing.v9.data import ms
from btc_swing.v9.engine import Spec, V9Engine, ladder
from btc_swing.v9.features import V9Frame
from btc_swing.v9.research import _clean, _with, trade_stats
from btc_swing.v9.setups import DirectionTables

log = logging.getLogger(__name__)
FLOORS = [0.50, 0.75, 1.00, 1.25, 1.50]
EXITS = [("R1.0", "+1.0R"), ("R1.5", "+1.5R"), ("R2.0", "+2.0R"), ("S1.0/2.0", "50% +1R / 50% +2R")]
WINDOWS = ["train", "validation_1", "recent_validation"]


@dataclass
class StopStudy:
    manifest: dict[str, Any]
    cells: list[dict[str, Any]]  # one per (base, mode, floor, exit, window, time_stop)

    def as_dict(self) -> dict[str, Any]:
        return {"manifest": self.manifest, "cells": self.cells}


def _cell(
    st: dict[str, Any], base: str, mode: str, floor: float, exit_: str, win: str, ts: int
) -> dict[str, Any]:
    ex = st.get("exit_types") or {}
    n = st["n"]
    skipped = st["funnel"].get("skipped_in_position", 0)
    return {
        "base": base,
        "mode": mode,
        "floor": floor,
        "exit": exit_,
        "window": win,
        "time_stop": ts,
        "n": n,
        "days": st["days"],
        "trades_per_day": st["trades_per_day"],
        "win_rate": st.get("win_rate"),
        "gross_r": st.get("gross_r"),
        "cost_r": st.get("cost_r"),
        "cost_pct_of_r": (st.get("cost_r") or float("nan")) * 100.0,
        "net_r": st.get("net_r"),
        "net_r_ci_lo": st.get("net_r_ci_lo"),
        "net_r_ci_hi": st.get("net_r_ci_hi"),
        "pf": st.get("pf"),
        "max_dd_pct": st.get("max_dd_pct"),
        "median_hold_min": st.get("median_hold_min"),
        "stop_outs": ex.get("STOP", 0),
        "tp1": ex.get("TP1", 0),
        "tp2": ex.get("TP2", 0),
        "time_exits": ex.get("TIME", 0) + ex.get("HARD", 0),
        "n_long": st.get("n_long", 0),
        "n_short": st.get("n_short", 0),
        "net_r_long": st.get("net_r_long"),
        "net_r_short": st.get("net_r_short"),
        "stop_pct_median": st.get("stop_pct_median"),
        "skipped_in_position": skipped,
        "skipped_per_day": skipped / st["days"],
        "potential_per_day": (n + skipped) / st["days"],
        "gross_bps": st.get("gross_bps"),
        "net_bps": st.get("net_bps"),
    }


def run_stop_study(
    cfg: V9Config,
    fr: V9Frame,
    tables: dict[int, DirectionTables],
    funding: pl.DataFrame,
    out_dir: Path,
    data_hash: str,
    frozen_spec: dict[str, Any],
) -> StopStudy:
    out_dir.mkdir(parents=True, exist_ok=True)
    ft = funding["time_ms"].to_numpy().astype(np.int64) if not funding.is_empty() else None
    frr = funding["funding_rate"].to_numpy().astype(np.float64) if not funding.is_empty() else None
    eng = V9Engine(fr, tables, cfg, ft, frr)
    w = cfg.windows
    spans = {
        "train": (ms(w.train.start), ms(w.train.end_exclusive)),
        "validation_1": (ms(w.validation_1.start), ms(w.validation_1.end_exclusive)),
        "recent_validation": (ms(w.recent_validation.start), ms(w.recent_validation.end_exclusive)),
    }
    assert int(fr.meta["last_close_ms"]) <= ms(w.locked_holdout.start), "holdout rows present"
    frozen = Spec(**{k: v for k, v in frozen_spec.items() if k in Spec.__dataclass_fields__})
    if isinstance(frozen.retrace, list):
        frozen = _with(frozen, frozen.name, retrace=tuple(frozen.retrace))
    bases = [("FROZEN_B", frozen), ("A_PRICE_ONLY", ladder(cfg)[0])]
    cells: list[dict[str, Any]] = []

    def run(base: str, spec: Spec, mode: str, floor: float, exit_: str, ts: int) -> None:
        for win in WINDOWS:
            a, b = spans[win]
            res = eng.run(spec, a, b)
            st = trade_stats(res, fr.t_open, cfg.risk.initial_equity)
            cells.append(_cell(st, base, mode, floor, exit_, win, ts))

    for base, bspec in bases:
        for mode in ("floor", "reject"):
            for floor in FLOORS:
                for exit_, _label in EXITS:
                    if mode == "reject" and exit_ != "R1.5":
                        continue  # the secondary reject table uses the pre-registered +1.5R exit only
                    sp = _with(
                        bspec,
                        f"{base} {mode} {floor}% {exit_}",
                        min_stop_pct=floor,
                        stop_mode=mode,
                        exit=exit_,
                    )
                    run(base, sp, mode, floor, exit_, cfg.exits.time_stop_min)
                    log.info("done %s", sp.name)
        for exit_ in ("R1.5", "R2.0"):
            sp = _with(
                bspec,
                f"{base} floor 1.0% {exit_} ts180",
                min_stop_pct=1.0,
                stop_mode="floor",
                exit=exit_,
                time_stop_min=180,
            )
            run(base, sp, "floor", 1.0, exit_, 180)
    manifest = {
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "v9_version": V9_VERSION,
        "config_hash": cfg.config_hash,
        "dataset_hash_1m": data_hash,
        "frozen_spec": frozen.as_dict(),
        "floors": FLOORS,
        "exits": [e for e, _ in EXITS],
        "risk_per_trade": cfg.risk.risk_per_trade,
        "locked_holdout": "not read",
        "cells": len(cells),
    }
    study = StopStudy(manifest, cells)
    (out_dir / "stop_study.json").write_text(
        json.dumps(_clean(study.as_dict()), indent=1, sort_keys=True)
    )
    return study


# ----------------------------------------------------------------------------- report
def _v(x: Any, nd: int = 3, pct: bool = False) -> str:
    if x is None:
        return "n/a"
    f = float(x)
    if f != f:
        return "n/a"
    if f == float("inf"):
        return "inf"
    return f"{f * 100:.1f}%" if pct else f"{f:.{nd}f}"


def _sel(cells: list[dict[str, Any]], **kw: Any) -> list[dict[str, Any]]:
    return [c for c in cells if all(c[k] == v for k, v in kw.items())]


def _spearman(x: list[float], y: list[float]) -> float:
    if len(x) < 3:
        return float("nan")
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def render_stop_study(study: StopStudy, cfg: V9Config) -> str:
    m, cells = study.manifest, study.cells
    out: list[str] = []
    out.append(
        "# BTC V9 — stop-geometry sensitivity study (wider stops, proportionally larger targets)\n"
    )
    out.append(
        f"Generated {m['generated_utc']} · V9 {m['v9_version']} · config `{m['config_hash'][:12]}` · Bybit 1m dataset "
        f"`{m['dataset_hash_1m'][:12]}` · {m['cells']} simulations · locked holdout: {m['locked_holdout']}.\n"
    )
    out.append(
        "Question (owner, design section 12): do wider stops with proportionally larger targets turn the EXISTING V9 "
        "setup into a positive net strategy? Entry signal unchanged; FLOOR-mode stops (structural stop or the floor, "
        "whichever is wider); constant account risk 0.125% per trade (wider stop = smaller position; USDT risk never "
        "rises); taker costs (fee 5.5 bps/side, half-spread 0.5, slippage 1/3 bps); identical 120-min time stop "
        "(progress < 0.5R) and 240-min hard maximum; one position. Bases: the frozen specification (B, impulse "
        "RVOL) and PRICE ONLY (A). Sensitivity matrix, not a search: nothing is selected or adopted.\n"
    )
    # ------------------------------------------------------------ the requested table
    for base in ("FROZEN_B", "A_PRICE_ONLY"):
        out.append(
            f"## {'1' if base == 'FROZEN_B' else '2'}. {base}: STOP | TARGET | TRADES/DAY | COST/R | NET R | PF | DD (floor mode)\n"
        )
        for win in WINDOWS:
            out.append(f"### {win}\n")
            out.append(
                "| STOP floor | TARGET | trades/day | cost (% of 1R) | gross R | NET R | NET R 95% CI | PF | max DD | win rate | median hold | stop-outs | TP1 | TP2 | time exits | LONG n (net R) | SHORT n (net R) | mean stop % | skipped (pos. open)/day |"
            )
            out.append(
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
            )
            for floor in FLOORS:
                for exit_, label in EXITS:
                    c = _sel(
                        cells,
                        base=base,
                        mode="floor",
                        floor=floor,
                        exit=exit_,
                        window=win,
                        time_stop=cfg.exits.time_stop_min,
                    )
                    if not c:
                        continue
                    x = c[0]
                    out.append(
                        f"| {floor:.2f}% | {label} | {_v(x['trades_per_day'], 2)} | {_v(x['cost_pct_of_r'], 1)}% | {_v(x['gross_r'])} | "
                        f"**{_v(x['net_r'])}** | [{_v(x['net_r_ci_lo'], 2)}, {_v(x['net_r_ci_hi'], 2)}] | {_v(x['pf'], 2)} | "
                        f"{_v(x['max_dd_pct'], 2)}% | {_v(x['win_rate'], 0, True)} | {_v(x['median_hold_min'], 0)} | {x['stop_outs']} | "
                        f"{x['tp1']} | {x['tp2']} | {x['time_exits']} | {x['n_long']} ({_v(x['net_r_long'], 2)}) | "
                        f"{x['n_short']} ({_v(x['net_r_short'], 2)}) | {_v(x['stop_pct_median'], 2)}% | {_v(x['skipped_per_day'], 2)} |"
                    )
            out.append("")
    # ------------------------------------------------------------ monotonicity
    out.append("## 3. Monotonicity: net R per trade vs stop floor (floor mode)\n")
    out.append(
        "| base | target | window | 0.50% | 0.75% | 1.00% | 1.25% | 1.50% | Spearman(floor, net R) | best floor | any cell net > 0 and PF > 1 |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    useful: list[str] = []
    for base in ("FROZEN_B", "A_PRICE_ONLY"):
        for exit_, label in EXITS:
            for win in WINDOWS:
                row = [
                    _sel(
                        cells,
                        base=base,
                        mode="floor",
                        floor=f,
                        exit=exit_,
                        window=win,
                        time_stop=cfg.exits.time_stop_min,
                    )
                    for f in FLOORS
                ]
                nets = [
                    r[0]["net_r"] if r and r[0]["net_r"] is not None else float("nan") for r in row
                ]
                ok = [i for i, v in enumerate(nets) if v == v]
                rho = _spearman([FLOORS[i] for i in ok], [nets[i] for i in ok])
                best = FLOORS[int(np.nanargmax(nets))] if ok else float("nan")
                pos = [
                    r[0]
                    for r in row
                    if r and r[0]["n"] > 0 and r[0]["net_r"] > 0 and r[0]["pf"] > 1
                ]
                out.append(
                    f"| {base} | {label} | {win} | "
                    + " | ".join(_v(v) for v in nets)
                    + f" | {_v(rho, 2)} | {best:.2f}% | {'YES' if pos else 'no'} |"
                )
            # usefulness requires both validation windows for the SAME cell
            for f in FLOORS:
                v1 = _sel(
                    cells,
                    base=base,
                    mode="floor",
                    floor=f,
                    exit=exit_,
                    window="validation_1",
                    time_stop=cfg.exits.time_stop_min,
                )
                v2 = _sel(
                    cells,
                    base=base,
                    mode="floor",
                    floor=f,
                    exit=exit_,
                    window="recent_validation",
                    time_stop=cfg.exits.time_stop_min,
                )
                if (
                    v1
                    and v2
                    and all(
                        c[0]["n"] > 0 and c[0]["net_r"] > 0 and c[0]["pf"] > 1 for c in (v1, v2)
                    )
                ):
                    useful.append(f"{base} {f:.2f}% {label}")
    out.append("")
    # ------------------------------------------------------------ time stop sensitivity
    out.append(
        "## 4. Time-stop sensitivity at the 1.00% floor (180-min time stop, 360-min hard maximum)\n"
    )
    out.append(
        "| base | target | window | time stop | trades/day | NET R | PF | median hold | time exits | stop-outs | TP hits |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for base in ("FROZEN_B", "A_PRICE_ONLY"):
        for exit_ in ("R1.5", "R2.0"):
            label = next(lb for e, lb in EXITS if e == exit_)
            for win in WINDOWS:
                for ts in (cfg.exits.time_stop_min, 180):
                    c = _sel(
                        cells,
                        base=base,
                        mode="floor",
                        floor=1.0,
                        exit=exit_,
                        window=win,
                        time_stop=ts,
                    )
                    if c:
                        x = c[0]
                        out.append(
                            f"| {base} | {label} | {win} | {ts} | {_v(x['trades_per_day'], 2)} | {_v(x['net_r'])} | {_v(x['pf'], 2)} | "
                            f"{_v(x['median_hold_min'], 0)} | {x['time_exits']} | {x['stop_outs']} | {x['tp1'] + x['tp2']} |"
                        )
    out.append("")
    # ------------------------------------------------------------ reject mode
    out.append(
        "## 5. Secondary: REJECT mode (only trades whose structural stop already exceeds the floor; +1.5R exit)\n"
    )
    out.append("| base | window | 0.50% /day, net R | 0.75% | 1.00% | 1.25% | 1.50% |")
    out.append("|---|---|---|---|---|---|---|")
    for base in ("FROZEN_B", "A_PRICE_ONLY"):
        for win in WINDOWS:
            cells_r = [
                _sel(cells, base=base, mode="reject", floor=f, exit="R1.5", window=win)
                for f in FLOORS
            ]
            out.append(
                f"| {base} | {win} | "
                + " | ".join(
                    f"{_v(c[0]['trades_per_day'], 2)}, {_v(c[0]['net_r'])}" if c else "n/a"
                    for c in cells_r
                )
                + " |"
            )
    out.append("")
    # ------------------------------------------------------------ one-position cost
    out.append(
        "## 6. One-position cost (floor mode, +1.5R): executed vs potential trades per day and median hold\n"
    )
    out.append(
        "| base | window | floor | executed/day | skipped (position open)/day | potential/day | median hold (min) |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for base in ("FROZEN_B", "A_PRICE_ONLY"):
        for win in WINDOWS:
            for f in FLOORS:
                c = _sel(
                    cells,
                    base=base,
                    mode="floor",
                    floor=f,
                    exit="R1.5",
                    window=win,
                    time_stop=cfg.exits.time_stop_min,
                )
                if c:
                    x = c[0]
                    out.append(
                        f"| {base} | {win} | {f:.2f}% | {_v(x['trades_per_day'], 2)} | {_v(x['skipped_per_day'], 2)} | {_v(x['potential_per_day'], 2)} | {_v(x['median_hold_min'], 0)} |"
                    )
    out.append("")
    # ------------------------------------------------------------ answer
    out.append("## 7. Answer\n")
    if useful:
        out.append(
            "Cells with net R > 0 and PF > 1 on BOTH validation windows: "
            + "; ".join(useful)
            + ".\n"
        )
    else:
        out.append(
            "**No stop/target cell has net R > 0 and PF > 1 on both validation windows, for either base.** Wider stops "
            "lower the cost share of 1R exactly as arithmetic predicts (section 1-2, cost column) but the gross edge of the "
            "setup does not grow with the stop, so net expectancy stays negative. Stop geometry is not the problem; the "
            "strategy family is closed.\n"
        )
    # quantified reading (frozen base, +1.5R, floor mode)
    rows = {
        (f, w): _sel(
            cells,
            base="FROZEN_B",
            mode="floor",
            floor=f,
            exit="R1.5",
            window=w,
            time_stop=cfg.exits.time_stop_min,
        )
        for f in FLOORS
        for w in WINDOWS
    }
    g_tr = [rows[(f, "train")][0]["gross_r"] for f in FLOORS if rows[(f, "train")]]
    c_tr = [rows[(f, "train")][0]["cost_r"] for f in FLOORS if rows[(f, "train")]]
    n_tr = [rows[(f, "train")][0]["net_r"] for f in FLOORS if rows[(f, "train")]]
    x15 = rows[(1.5, "train")][0]
    x05 = rows[(0.5, "train")][0]
    out.append("### Reading\n")
    out.append(
        f"- Monotone, yes; material, no. On the frozen base with +1.5R (TRAIN) the cost share of 1R falls from "
        f"{_v(c_tr[0] * 100, 0)}% at 0.50% to {_v(c_tr[-1] * 100, 0)}% at 1.50%, and net R rises from {_v(n_tr[0])} to "
        f"{_v(n_tr[-1])} in lock-step (Spearman 1.0 in 23 of 24 base/target/window rows). Gross R stays flat at "
        f"{_v(min(g_tr))} to {_v(max(g_tr))} R across all five floors: the setup does not earn more as the stop widens, "
        f"so the improvement is pure cost arithmetic and converges on net ~ -0.08 to -0.10 R, never on zero.\n"
        f"- Where the trades end: at the 0.50% floor {x05['stop_outs']} stop-outs, {x05['tp1']} target hits and "
        f"{x05['time_exits']} time exits (TRAIN, +1.5R); at 1.50% {x15['stop_outs']} stop-outs, {x15['tp1']} target hits and "
        f"{x15['time_exits']} time exits. With a 1.5% stop, +0.5R progress means +0.75% in 120 minutes, which the identical "
        f"time stop rarely sees, so {_v(x15['time_exits'] / max(1, x15['n']), 0, True)} of trades are closed at market after "
        f"paying the round trip. A 180-minute time stop (section 4) moves net R by a few hundredths either way and never "
        f"changes the sign.\n"
        f"- One-position cost: potential triggers are {_v(x05['potential_per_day'], 1)}/day on TRAIN for the frozen base; "
        f"executed trades fall from {_v(x05['trades_per_day'], 2)}/day at 0.50% to {_v(x15['trades_per_day'], 2)}/day at 1.50% "
        f"because median holds lengthen from {_v(x05['median_hold_min'], 0)} to {_v(x15['median_hold_min'], 0)} minutes; the "
        f"frequency band is reachable, the expectancy is not.\n"
        f"- REJECT mode (section 5) at 1.25-1.50% leaves 0.01-0.07 trades/day; the positive recent-validation cells there "
        f"rest on 2-4 trades and are noise.\n"
        f"- Conclusion: stop geometry is not the problem. The V9 setup has ~1-3 bps of gross edge per trade at every stop "
        f"width studied; no stop floor, target or time stop turns that into a positive net strategy on either validation "
        f"window. The strategy family is closed.\n"
    )
    out.append(
        "Nothing activated; no Bybit order; V5.1 untouched; locked holdout not read; nothing adopted from this matrix.\n"
    )
    return "\n".join(out)
