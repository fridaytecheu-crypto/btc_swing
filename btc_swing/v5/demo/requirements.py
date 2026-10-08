"""Can the frozen V5 position structure be represented on Bybit at a given reference equity?

Frozen structure: total quantity = reference_equity x 0.25% / stop distance; TP1 = 40%, TP2 = 30%,
remaining 30% trails. Each leg is a separate order and must satisfy the exchange minimum quantity
and minimum notional; quantities are floored to the step (never rounded up). The frozen fractions
are never changed to fit the exchange."""

from __future__ import annotations

import math
from typing import Any

V5_STOP_PCTS = {
    "p10": 0.746571169279275,
    "median": 1.400724926475881,
    "p90": 2.5321479864752168,
}  # V5 research run, % of price


def _legs(q_steps: int, step: float, fr1: float, fr2: float) -> tuple[float, float, float]:
    t1 = math.floor(q_steps * fr1 + 1e-9) * step
    t2 = math.floor(q_steps * fr2 + 1e-9) * step
    return t1, t2, q_steps * step - t1 - t2


def leg_check(
    qty: float,
    step: float,
    min_qty: float,
    min_notional: float,
    price: float,
    fr1: float,
    fr2: float,
) -> dict[str, Any]:
    k = math.floor(qty / step + 1e-9)
    t1, t2, rem = _legs(k, step, fr1, fr2)
    ok = {
        name: (v >= min_qty - 1e-12 and v * price >= min_notional - 1e-9)
        for name, v in (("total", k * step), ("tp1", t1), ("tp2", t2), ("remainder", rem))
    }
    exact = abs(t1 - fr1 * k * step) < 1e-12 and abs(t2 - fr2 * k * step) < 1e-12
    return {
        "qty": k * step,
        "tp1_qty": t1,
        "tp2_qty": t2,
        "remainder_qty": rem,
        "ok": ok,
        "all_legs_ok": all(ok.values()),
        "fractions_exact": exact,
    }


def min_equity(
    step: float,
    min_qty: float,
    min_notional: float,
    price: float,
    stop_pct: float,
    risk: float,
    fr1: float,
    fr2: float,
    exact: bool,
) -> dict[str, Any]:
    """Smallest reference equity whose frozen quantity represents all three legs (and, if `exact`,
    the frozen 40/30/30 fractions exactly at the exchange step)."""
    for k in range(1, 100_000):
        c = leg_check(k * step, step, min_qty, min_notional, price, fr1, fr2)
        if c["all_legs_ok"] and (c["fractions_exact"] or not exact):
            q = k * step
            return {
                "qty": q,
                "equity": q * price * stop_pct / 100.0 / risk,
                "legs": (c["tp1_qty"], c["tp2_qty"], c["remainder_qty"]),
            }
    return {"qty": math.nan, "equity": math.nan, "legs": None}


def reference_equity_report(
    ref_equity: float,
    risk: float,
    price: float,
    inst: dict[str, Any],
    fr1: float = 0.4,
    fr2: float = 0.3,
    stops: dict[str, float] | None = None,
) -> dict[str, Any]:
    step, mq, mn = (
        float(inst["qty_step"]),
        float(inst["min_qty"]),
        float(inst.get("min_notional") or 0.0),
    )
    mn = 0.0 if math.isnan(mn) else mn
    out: dict[str, Any] = {
        "reference_equity": ref_equity,
        "risk_per_trade": risk,
        "risk_usdt": ref_equity * risk,
        "price": price,
        "min_qty": mq,
        "qty_step": step,
        "min_notional": mn,
        "by_stop": {},
    }
    for name, sp in (stops or V5_STOP_PCTS).items():
        qty_raw = ref_equity * risk / (price * sp / 100.0)
        c = leg_check(qty_raw, step, mq, mn, price, fr1, fr2)
        out["by_stop"][name] = {
            "stop_pct": sp,
            "qty_raw": qty_raw,
            **c,
            "min_equity_one_lot": max(mq, mn / price) * price * sp / 100.0 / risk,
            "min_equity_all_legs": min_equity(step, mq, mn, price, sp, risk, fr1, fr2, exact=False),
            "min_equity_exact_fractions": min_equity(
                step, mq, mn, price, sp, risk, fr1, fr2, exact=True
            ),
        }
    return out
