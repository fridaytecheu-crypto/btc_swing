"""V9 research runner (design sections 5-8): ladder A/B/C/D, funnel, half-year folds, pre-declared
one-at-a-time neighbourhoods on TRAIN, accel diagnostic, selection + freeze on TRAIN only, then one
pass per validation window. The locked holdout is never read."""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from btc_swing.core.hashing import sha256_text
from btc_swing.v9.config import V9_VERSION, V9Config
from btc_swing.v9.data import DAY_MS, ms
from btc_swing.v9.engine import RunResult, Spec, Trade, V9Engine, ladder
from btc_swing.v9.features import V9Frame
from btc_swing.v9.setups import DirectionTables

log = logging.getLogger(__name__)
F = NDArray[np.float64]
WINDOWS = ("train", "validation_1", "recent_validation")


def _f(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def _nanmean(x: list[float]) -> float:
    return float(np.mean(x)) if x else float("nan")


def _folds(cfg: V9Config) -> list[tuple[str, int, int]]:
    """TRAIN half-years."""
    out: list[tuple[str, int, int]] = []
    s = ms(cfg.windows.train.start)
    e = ms(cfg.windows.train.end_exclusive)
    y = datetime.fromtimestamp(s / 1000, tz=UTC).year
    while True:
        for h, (m0, m1) in enumerate(((1, 7), (7, 13))):
            a = ms(f"{y}-{m0:02d}-01")
            b = ms(f"{y + 1}-01-01") if m1 == 13 else ms(f"{y}-{m1:02d}-01")
            if a >= e:
                return out
            if b > s:
                out.append((f"{y}H{h + 1}", max(a, s), min(b, e)))
        y += 1


def _boot_mean_ci(values: F, days: NDArray[np.int64], n: int, seed: int) -> tuple[float, float]:
    if len(values) < 5:
        return float("nan"), float("nan")
    rng = np.random.RandomState(seed)
    ud = np.unique(days)
    groups = [values[days == d] for d in ud]
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    out = np.empty(n)
    for k in range(n):
        idx = rng.randint(0, len(ud), len(ud))
        c = cnts[idx].sum()
        out[k] = sums[idx].sum() / c if c > 0 else np.nan
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def trade_stats(res: RunResult, t_open: NDArray[np.int64], initial_equity: float) -> dict[str, Any]:
    tr = res.trades
    n = len(tr)
    days = res.days
    out: dict[str, Any] = {
        "spec": res.spec.name,
        "n": n,
        "days": days,
        "trades_per_day": n / days,
        "funnel": res.funnel.as_dict(),
    }
    if n == 0:
        out.update(
            {
                k: float("nan")
                for k in (
                    "win_rate",
                    "gross_r",
                    "fee_r",
                    "slip_r",
                    "funding_r",
                    "cost_r",
                    "net_r",
                    "net_r_ci_lo",
                    "net_r_ci_hi",
                    "pf",
                    "max_dd_pct",
                    "max_dd_r",
                    "median_hold_min",
                    "gross_bps",
                    "cost_bps",
                    "net_bps",
                    "stop_pct_median",
                    "net_r_long",
                    "net_r_short",
                    "median_trades_per_day",
                )
            }
        )
        out.update({"n_long": 0, "n_short": 0, "exit_types": {}, "day_dist": {}})
        return out
    net_r = np.array([t.r(t.net_usd) for t in tr])
    gross_r = np.array([t.r(t.gross_usd) for t in tr])
    fee_r = np.array([t.r(t.fee_usd) for t in tr])
    slip_r = np.array([t.r(t.slip_usd) for t in tr])
    fund_r = np.array([t.r(t.funding_usd) for t in tr])
    net_usd = np.array([t.net_usd for t in tr])
    notional = np.array([t.notional_usd for t in tr])
    entry_day = np.array([int(t_open[t.entry_minute] // DAY_MS) for t in tr], dtype=np.int64)
    curve = np.concatenate([[0.0], np.cumsum(net_usd)])
    dd = np.maximum.accumulate(curve) - curve
    risk_usd = np.array([t.risk_usd for t in tr])
    wins = net_usd[net_usd > 0].sum()
    losses = -net_usd[net_usd < 0].sum()
    dirs = np.array([t.direction for t in tr])
    # per-calendar-day distribution over all days in the window
    counts = np.bincount(entry_day - entry_day.min(), minlength=1)
    per_day = np.zeros(days, dtype=np.int64)
    k = min(days, len(counts))
    per_day[:k] = counts[:k]
    zero = float(np.mean(per_day == 0))
    one_two = float(np.mean((per_day >= 1) & (per_day <= 2)))
    three_six = float(np.mean((per_day >= 3) & (per_day <= 6)))
    more = float(np.mean(per_day > 6))
    lo, hi = _boot_mean_ci(net_r, entry_day, 1000, 17)
    ex: dict[str, int] = {}
    for t in tr:
        for lg in t.legs:
            ex[lg[2]] = ex.get(lg[2], 0) + 1
    out.update(
        {
            "win_rate": float(np.mean(net_r > 0)),
            "gross_r": float(gross_r.mean()),
            "fee_r": float(fee_r.mean()),
            "slip_r": float(slip_r.mean()),
            "funding_r": float(fund_r.mean()),
            "cost_r": float((fee_r + slip_r - fund_r).mean()),
            "net_r": float(net_r.mean()),
            "net_r_median": float(np.median(net_r)),
            "net_r_ci_lo": lo,
            "net_r_ci_hi": hi,
            "net_r_sum": float(net_r.sum()),
            "pf": float(wins / losses) if losses > 0 else float("inf"),
            "max_dd_pct": float(dd.max() / initial_equity * 100.0),
            "max_dd_r": float(dd.max() / np.median(risk_usd)) if np.median(risk_usd) > 0 else 0.0,
            "median_hold_min": float(np.median([t.hold_min for t in tr])),
            "gross_bps": float(
                np.mean([t.gross_usd / nt * 1e4 for t, nt in zip(tr, notional, strict=True)])
            ),
            "fee_bps": float(
                np.mean([t.fee_usd / nt * 1e4 for t, nt in zip(tr, notional, strict=True)])
            ),
            "slip_bps": float(
                np.mean([t.slip_usd / nt * 1e4 for t, nt in zip(tr, notional, strict=True)])
            ),
            "cost_bps": float(
                np.mean(
                    [
                        (t.fee_usd + t.slip_usd - t.funding_usd) / nt * 1e4
                        for t, nt in zip(tr, notional, strict=True)
                    ]
                )
            ),
            "net_bps": float(
                np.mean([t.net_usd / nt * 1e4 for t, nt in zip(tr, notional, strict=True)])
            ),
            "stop_pct_median": float(np.median([t.stop_pct for t in tr])),
            "n_long": int((dirs > 0).sum()),
            "n_short": int((dirs < 0).sum()),
            "net_r_long": float(net_r[dirs > 0].mean()) if (dirs > 0).any() else float("nan"),
            "net_r_short": float(net_r[dirs < 0].mean()) if (dirs < 0).any() else float("nan"),
            "median_trades_per_day": float(np.median(per_day)),
            "day_dist": {"zero": zero, "one_two": one_two, "three_six": three_six, "more": more},
            "exit_types": ex,
            "risk_capped": sum(t.risk_capped for t in tr),
            "mean_leverage": float(np.mean([t.leverage for t in tr])),
            "funding_events": sum(t.funding_events for t in tr),
        }
    )
    return out


def fold_stats(
    res: RunResult, folds: list[tuple[str, int, int]], t_open: NDArray[np.int64]
) -> list[dict[str, Any]]:
    rows = []
    for name, a, b in folds:
        sel = [t for t in res.trades if a <= int(t_open[t.entry_minute]) < b]
        days = max(1, round((b - a) / DAY_MS))
        if sel:
            net = np.array([t.r(t.net_usd) for t in sel])
            usd = np.array([t.net_usd for t in sel])
            w, l_ = usd[usd > 0].sum(), -usd[usd < 0].sum()
            rows.append(
                {
                    "fold": name,
                    "n": len(sel),
                    "trades_per_day": len(sel) / days,
                    "net_r": float(net.mean()),
                    "win_rate": float(np.mean(net > 0)),
                    "pf": float(w / l_) if l_ > 0 else float("inf"),
                }
            )
        else:
            rows.append(
                {
                    "fold": name,
                    "n": 0,
                    "trades_per_day": 0.0,
                    "net_r": float("nan"),
                    "win_rate": float("nan"),
                    "pf": float("nan"),
                }
            )
    return rows


def _with(spec: Spec, name: str, **kw: Any) -> Spec:
    return Spec(**{**spec.as_dict(), "name": name, **kw})


def sensitivity_specs(cfg: V9Config) -> list[tuple[str, Spec]]:
    """Design section 6: one change at a time from the anchors; nothing else."""
    a, b, c, d = ladder(cfg)
    out: list[tuple[str, Spec]] = []
    for v in cfg.impulse.size_atr_grid:
        out.append(("impulse_atr", _with(a, f"A impulse_atr={v}", impulse_atr=v)))
        out.append(("impulse_atr", _with(d, f"D impulse_atr={v}", impulse_atr=v)))
    for v in cfg.impulse.rvol_grid:
        out.append(("impulse_rvol", _with(b, f"B impulse_rvol={v}", impulse_rvol=v)))
    for g in cfg.pullback.retrace_grid:
        out.append(("retrace", _with(a, f"A retrace={g[0]}-{g[1]}", retrace=(g[0], g[1]))))
    for v in cfg.pullback.volume_ratio_grid:
        out.append(("pb_vol_ratio", _with(c, f"C pb_vol_ratio={v}", pb_vol_ratio=v)))
    for v in cfg.trigger.rvol_grid:
        out.append(("trig_rvol", _with(d, f"D trig_rvol={v}", trig_rvol=v)))
    for v in cfg.trigger.imbalance_grid:
        out.append(("imbalance", _with(d, f"D imbalance={v}", imbalance=v)))
    out.append(("delta_mode", _with(d, "D delta=sign", delta_mode="sign")))
    out.append(("delta_mode", _with(d, "D delta=median", delta_mode="median")))
    for v in cfg.stop.min_distance_grid:
        out.append(("min_stop", _with(a, f"A min_stop={v}%", min_stop_pct=v)))
        out.append(("min_stop", _with(d, f"D min_stop={v}%", min_stop_pct=v)))
    for e in ("A", "B", "C"):
        out.append(("exit", _with(a, f"A exit={e}", exit=e)))
        out.append(("exit", _with(d, f"D exit={e}", exit=e)))
    for v in cfg.exits.time_stop_grid:
        out.append(("time_stop", _with(a, f"A time_stop={v}", time_stop_min=v)))
        out.append(("time_stop", _with(d, f"D time_stop={v}", time_stop_min=v)))
    for v in cfg.stop.min_distance_grid:
        out.append(
            ("stop_floor", _with(a, f"A floor min_stop={v}%", min_stop_pct=v, stop_mode="floor"))
        )
        out.append(
            ("stop_floor", _with(d, f"D floor min_stop={v}%", min_stop_pct=v, stop_mode="floor"))
        )
    out.append(
        (
            "accel_gate",
            _with(
                d,
                f"D accel>={cfg.trigger.accel_gate_candidate}",
                accel_min=cfg.trigger.accel_gate_candidate,
            ),
        )
    )
    return out


def accel_quartiles(
    trades: list[Trade], folds: list[tuple[str, int, int]], t_open: NDArray[np.int64]
) -> dict[str, Any]:
    if len(trades) < 20:
        return {"quartiles": [], "top_vs_rest_by_fold": []}
    acc = np.array([t.accel1 for t in trades])
    net = np.array([t.r(t.net_usd) for t in trades])
    ok = ~np.isnan(acc)
    acc, net = acc[ok], net[ok]
    tsel = [t for t, o in zip(trades, ok, strict=True) if o]
    qs = np.percentile(acc, [25, 50, 75])
    bucket = np.digitize(acc, qs)
    rows = []
    for q in range(4):
        m = bucket == q
        rows.append(
            {
                "quartile": q + 1,
                "accel_min": float(acc[m].min()) if m.any() else float("nan"),
                "accel_max": float(acc[m].max()) if m.any() else float("nan"),
                "n": int(m.sum()),
                "net_r": float(net[m].mean()) if m.any() else float("nan"),
            }
        )
    by_fold = []
    for name, a, b in folds:
        sel = np.array([a <= int(t_open[t.entry_minute]) < b for t in tsel], dtype=bool)
        top = sel & (bucket == 3)
        rest = sel & (bucket < 3)
        by_fold.append(
            {
                "fold": name,
                "n_top": int(top.sum()),
                "net_r_top": float(net[top].mean()) if top.any() else float("nan"),
                "net_r_rest": float(net[rest].mean()) if rest.any() else float("nan"),
            }
        )
    return {"quartiles": rows, "top_vs_rest_by_fold": by_fold, "cut_points": [float(q) for q in qs]}


@dataclass
class V9Research:
    manifest: dict[str, Any]
    train: dict[str, Any]
    validation: dict[str, dict[str, Any]]
    frozen: dict[str, Any]
    verdict: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "train": self.train,
            "validation": self.validation,
            "frozen": self.frozen,
            "verdict": self.verdict,
            "extra": self.extra,
        }


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return None if (np.isnan(o) or np.isinf(o)) else o
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return _clean(float(o))
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _qualifies(st: dict[str, Any], folds: list[dict[str, Any]], cfg: V9Config) -> bool:
    pos = sum(1 for f in folds if f["n"] > 0 and f["net_r"] > 0)
    return (
        st["n"] > 0
        and st["net_r"] > 0
        and pos >= cfg.selection.min_folds_positive
        and st["trades_per_day"] >= cfg.selection.min_trades_per_day
    )


def select_primary(
    cfg: V9Config, ladder_stats: list[dict[str, Any]], ladder_folds: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Design section 7.2."""
    qual = [s for s in ladder_stats if _qualifies(s, ladder_folds[s["spec"]], cfg)]
    if qual:
        best = max(qual, key=lambda s: s["net_r"])
        return {"step": best["spec"], "qualified": True, "net_r": best["net_r"]}
    best = max(ladder_stats, key=lambda s: s["net_r"] if s["n"] > 0 else -1e9)
    return {"step": best["spec"], "qualified": False, "net_r": best["net_r"]}


def run_research(
    cfg: V9Config,
    fr: V9Frame,
    tables: dict[int, DirectionTables],
    funding: pl.DataFrame,
    out_dir: Path,
    data_hash: str,
) -> V9Research:
    out_dir.mkdir(parents=True, exist_ok=True)
    ft = funding["time_ms"].to_numpy().astype(np.int64) if not funding.is_empty() else None
    fr_ = funding["funding_rate"].to_numpy().astype(np.float64) if not funding.is_empty() else None
    eng = V9Engine(fr, tables, cfg, ft, fr_)
    t_open = fr.t_open
    eq = cfg.risk.initial_equity
    w = cfg.windows
    spans = {
        "train": (ms(w.train.start), ms(w.train.end_exclusive)),
        "validation_1": (ms(w.validation_1.start), ms(w.validation_1.end_exclusive)),
        "recent_validation": (ms(w.recent_validation.start), ms(w.recent_validation.end_exclusive)),
    }
    assert int(fr.meta["last_close_ms"]) <= ms(w.locked_holdout.start), "holdout rows present"
    folds = _folds(cfg)
    manifest = {
        "generated_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "v9_version": V9_VERSION,
        "config_hash": cfg.config_hash,
        "dataset_hash_1m": data_hash,
        "frame": fr.meta,
        "windows": {k: {"start_ms": a, "end_ms": b} for k, (a, b) in spans.items()},
        "locked_holdout": "not read",
        "folds": [f[0] for f in folds],
    }

    def run_and_stat(spec: Spec, win: str, save: bool = False) -> tuple[dict[str, Any], RunResult]:
        a, b = spans[win]
        res = eng.run(spec, a, b)
        st = trade_stats(res, t_open, eq)
        if save:
            (out_dir / f"trades_{win}_{spec.name}.json").write_text(
                json.dumps(_clean([t.as_dict() for t in res.trades]))
            )
        return st, res

    # ---------------------------------------------------------------- TRAIN
    log.info("TRAIN ladder")
    ladder_specs = ladder(cfg)
    ladder_stats: list[dict[str, Any]] = []
    ladder_folds: dict[str, list[dict[str, Any]]] = {}
    ladder_res: dict[str, RunResult] = {}
    for sp in ladder_specs:
        st, res = run_and_stat(sp, "train", save=True)
        ladder_stats.append(st)
        ladder_folds[sp.name] = fold_stats(res, folds, t_open)
        ladder_res[sp.name] = res
        log.info(
            "  %s n=%d /day=%.2f net=%.3fR pf=%.2f",
            sp.name,
            st["n"],
            st["trades_per_day"],
            st["net_r"],
            st["pf"],
        )
    log.info("TRAIN cost stress")
    stress = []
    for sp in ladder_specs:
        st, _ = run_and_stat(
            _with(sp, sp.name + " x1.5 costs", cost_mult=cfg.costs.stress_multiplier), "train"
        )
        stress.append(st)
    log.info("TRAIN sensitivities")
    sens: list[dict[str, Any]] = []
    sens_folds: dict[str, list[dict[str, Any]]] = {}
    for group, sp in sensitivity_specs(cfg):
        st, res = run_and_stat(sp, "train")
        st["group"] = group
        sens.append(st)
        sens_folds[sp.name] = fold_stats(res, folds, t_open)
        log.info(
            "  [%s] %s n=%d /day=%.2f net=%.3fR",
            group,
            sp.name,
            st["n"],
            st["trades_per_day"],
            st["net_r"],
        )
    accel = accel_quartiles(ladder_res["D_TRIGGER_FLOW"].trades, folds, t_open)
    # ---------------------------------------------------------------- selection + freeze
    sel = select_primary(cfg, ladder_stats, ladder_folds)
    base_spec = next(s for s in ladder_specs if s.name == sel["step"])
    d_stat = next(s for s in ladder_stats if s["spec"] == "D_TRIGGER_FLOW")
    gate_name = f"D accel>={cfg.trigger.accel_gate_candidate}"
    gate_stat = next(s for s in sens if s["spec"] == gate_name)
    gate_pos = sum(1 for f in sens_folds[gate_name] if f["n"] > 0 and f["net_r"] > 0)
    accel_promoted = (
        sel["step"] == "D_TRIGGER_FLOW"
        and gate_stat["n"] > 0
        and gate_stat["net_r"] - d_stat["net_r"] >= cfg.selection.accel_min_gain_r
        and gate_pos >= cfg.selection.min_folds_positive
        and gate_stat["trades_per_day"] >= cfg.selection.min_trades_per_day
    )
    frozen_spec = (
        _with(
            base_spec,
            "FROZEN_" + base_spec.name + "_ACCEL",
            accel_min=cfg.trigger.accel_gate_candidate,
        )
        if accel_promoted
        else _with(base_spec, "FROZEN_" + base_spec.name)
    )
    frozen = {
        "selection": sel,
        "accel_promoted": accel_promoted,
        "accel_gate": {
            "net_r": gate_stat["net_r"],
            "folds_positive": gate_pos,
            "trades_per_day": gate_stat["trades_per_day"],
        },
        "spec": frozen_spec.as_dict(),
        "config_hash": cfg.config_hash,
    }
    frozen["spec_hash"] = sha256_text(json.dumps(_clean(frozen["spec"]), sort_keys=True))
    (out_dir / "frozen_spec.json").write_text(json.dumps(_clean(frozen), indent=1, sort_keys=True))
    log.info(
        "FROZEN %s (qualified=%s, accel_promoted=%s)",
        frozen_spec.name,
        sel["qualified"],
        accel_promoted,
    )
    risk_rows = []
    for rv in cfg.risk.risk_grid:
        st, _ = run_and_stat(
            _with(frozen_spec, f"{frozen_spec.name} risk={rv * 100:.3f}%", risk_per_trade=rv),
            "train",
        )
        risk_rows.append(st)
    train = {
        "ladder": ladder_stats,
        "ladder_folds": ladder_folds,
        "stress": stress,
        "sensitivities": sens,
        "sensitivity_folds": sens_folds,
        "accel": accel,
        "risk": risk_rows,
    }
    # ---------------------------------------------------------------- validation (once each)
    validation: dict[str, dict[str, Any]] = {}
    for win in ("validation_1", "recent_validation"):
        log.info("%s (frozen specification, once)", win)
        rows = []
        for sp in ladder_specs:
            st, _ = run_and_stat(sp, win, save=True)
            rows.append(st)
        fst, fres = run_and_stat(frozen_spec, win, save=True)
        sst, _ = run_and_stat(
            _with(
                frozen_spec, frozen_spec.name + " x1.5 costs", cost_mult=cfg.costs.stress_multiplier
            ),
            win,
        )
        validation[win] = {
            "ladder": rows,
            "frozen": fst,
            "frozen_stress": sst,
            "frozen_funnel": fres.funnel.as_dict(),
        }
        log.info(
            "  frozen n=%d /day=%.2f net=%.3fR pf=%.2f",
            fst["n"],
            fst["trades_per_day"],
            fst["net_r"],
            fst["pf"],
        )
    verdict = _verdict(cfg, train, validation, frozen)
    res_obj = V9Research(manifest, train, validation, frozen, verdict)
    (out_dir / "research.json").write_text(
        json.dumps(_clean(res_obj.as_dict()), indent=1, sort_keys=True)
    )
    (out_dir / "manifest.json").write_text(json.dumps(_clean(manifest), indent=1, sort_keys=True))
    return res_obj


def _verdict(
    cfg: V9Config,
    train: dict[str, Any],
    validation: dict[str, dict[str, Any]],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    lo, hi = cfg.selection.frequency_target
    per_win = {}
    for win, v in validation.items():
        st = v["frozen"]
        per_win[win] = {
            "n": st["n"],
            "trades_per_day": st["trades_per_day"],
            "net_r": st["net_r"],
            "pf": st["pf"],
            "max_dd_pct": st["max_dd_pct"],
            "profit_pass": bool(
                st["n"] > 0
                and st["net_r"] > 0
                and st["pf"] > 1.0
                and st["max_dd_pct"] < cfg.selection.max_drawdown_pct
            ),
            "pf_110": bool(st["n"] > 0 and st["pf"] >= 1.10),
            "frequency_pass": bool(lo <= st["trades_per_day"] <= hi),
        }
    profit = all(x["profit_pass"] for x in per_win.values())
    freq = all(x["frequency_pass"] for x in per_win.values())
    # which conditions add edge (TRAIN ladder deltas, confirmed on both validation windows)
    steps = ["A_PRICE_ONLY", "B_IMPULSE_RVOL", "C_PULLBACK_VOLUME", "D_TRIGGER_FLOW"]
    tr = {s["spec"]: s for s in train["ladder"]}
    conds = []
    for prev, cur in itertools.pairwise(steps):
        d_train = (
            tr[cur]["net_r"] - tr[prev]["net_r"] if tr[cur]["n"] and tr[prev]["n"] else float("nan")
        )
        both = []
        for v in validation.values():
            vw = {s["spec"]: s for s in v["ladder"]}
            both.append(
                vw[cur]["net_r"] - vw[prev]["net_r"]
                if vw[cur]["n"] and vw[prev]["n"]
                else float("nan")
            )
        freq_cut = (
            1 - tr[cur]["trades_per_day"] / tr[prev]["trades_per_day"]
            if tr[prev]["trades_per_day"] > 0
            else float("nan")
        )
        adds = bool(d_train > 0 and all((not np.isnan(x)) and x > 0 for x in both))
        conds.append(
            {
                "rule": cur,
                "delta_net_r_train": d_train,
                "delta_net_r_validation": both,
                "frequency_cut_train": freq_cut,
                "adds_edge": adds,
            }
        )
    return {
        "frozen_spec": frozen["spec"]["name"],
        "per_window": per_win,
        "profitability_pass": profit,
        "frequency_pass": freq,
        "conditions": conds,
        "expected_trades_per_day_validation": float(
            np.mean([x["trades_per_day"] for x in per_win.values()])
        ),
    }
