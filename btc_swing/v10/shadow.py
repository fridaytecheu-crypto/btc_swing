"""Shadow execution (no order): the hypothetical fill of a decision with the conservative taker model
(last + half-spread + slippage, taker fee per side) and exits resolved on the live trade stream
(stop at stop - 3 bps, targets at target - 1 bps, time stop at last - 1 bps). Same exit rules as the
Demo executor: TP1 +1R 50%, TP2 +2R 50%, time stop, hard maximum."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from btc_swing.v10.config import V10Config
from btc_swing.v10.features import Trade


@dataclass
class ShadowPosition:
    signal_id: str
    episode_id: str
    family: str
    side: int
    entry_ms: int
    entry_ref: float
    entry_fill: float
    stop: float
    tp1: float
    tp2: float
    qty: float
    leverage: float
    risk_usdt: float
    frac_open: float = 1.0
    legs: list[dict[str, Any]] = field(default_factory=list)
    tp1_done: bool = False
    time_checked: bool = False
    mfe: float = 0.0
    mae: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShadowBook:
    def __init__(self, cfg: V10Config) -> None:
        self.cfg = cfg
        self.pos: ShadowPosition | None = None

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any] | None:
        return self.pos.as_dict() if self.pos else None

    def restore(self, d: dict[str, Any] | None) -> None:
        self.pos = ShadowPosition(**d) if d else None

    # ------------------------------------------------------------------ open
    def open(self, sig: dict[str, Any], f: dict[str, float]) -> ShadowPosition:
        if self.pos is not None:
            raise RuntimeError("shadow position already open")
        c = self.cfg.costs
        s = float(sig["side_sign"])
        last = float(sig["entry_ref"])
        half = (
            0.5 * f["spread_bps"]
            if math.isfinite(f.get("spread_bps", math.nan))
            else c.half_spread_fallback_bps
        )
        fill = last * (1 + s * (half + c.entry_slippage_bps) / 1e4)
        self.pos = ShadowPosition(
            signal_id=str(sig["signal_id"]),
            episode_id=str(sig["episode_id"]),
            family=str(sig["family"]),
            side=int(sig["side_sign"]),
            entry_ms=int(sig["decision_ms"]),
            entry_ref=last,
            entry_fill=fill,
            stop=float(sig["stop"]),
            tp1=float(sig["tp1"]),
            tp2=float(sig["tp2"]),
            qty=float(sig["qty"]),
            leverage=float(sig["leverage"]),
            risk_usdt=float(sig["risk_usdt"]),
        )
        return self.pos

    # ------------------------------------------------------------------ resolution
    def _leg(self, frac: float, ref: float, kind: str, t_ms: int) -> None:
        p = self.pos
        assert p is not None
        c = self.cfg.costs
        s = float(p.side)
        slip = c.stop_slippage_bps if kind == "STOP" else c.exit_slippage_bps
        fill = ref * (1 - s * slip / 1e4)
        p.legs.append({"frac": frac, "ref": ref, "fill": fill, "kind": kind, "t_ms": t_ms})
        p.frac_open = max(0.0, p.frac_open - frac)

    def on_trade(self, tr: Trade) -> dict[str, Any] | None:
        p = self.pos
        if p is None or tr.t < p.entry_ms:
            return None
        s = float(p.side)
        r = s * (tr.p - p.entry_fill) / abs(p.entry_fill - p.stop)
        p.mfe, p.mae = max(p.mfe, r), min(p.mae, r)
        if s * (tr.p - p.stop) <= 0:  # stop first (conservative)
            self._leg(p.frac_open, p.stop, "STOP", tr.t)
            return self._close("STOP", tr.t)
        if not p.tp1_done and s * (tr.p - p.tp1) >= 0:
            p.tp1_done = True
            self._leg(self.cfg.exits.tp1_frac, p.tp1, "TP1", tr.t)
        if p.tp1_done and p.frac_open > 1e-9 and s * (tr.p - p.tp2) >= 0:
            self._leg(p.frac_open, p.tp2, "TP2", tr.t)
            return self._close("TP2", tr.t)
        return None

    def on_tick(self, now_ms: int, last: float) -> dict[str, Any] | None:
        p = self.pos
        if p is None or not math.isfinite(last):
            return None
        ex = self.cfg.exits
        s = float(p.side)
        held = now_ms - p.entry_ms
        if not p.time_checked and held >= ex.time_stop_min * 60_000:
            p.time_checked = True
            r = s * (last - p.entry_fill) / abs(p.entry_fill - p.stop)
            if r < ex.progress_min_r:
                self._leg(p.frac_open, last, "TIME", now_ms)
                return self._close("TIME", now_ms)
        if held >= ex.hard_max_min * 60_000 and p.frac_open > 1e-9:
            self._leg(p.frac_open, last, "HARD", now_ms)
            return self._close("HARD", now_ms)
        return None

    def _close(self, reason: str, t_ms: int) -> dict[str, Any]:
        p = self.pos
        assert p is not None
        c = self.cfg.costs
        s = float(p.side)
        fee_rate = c.taker_fee_bps / 1e4
        notional_in = p.qty * p.entry_fill
        gross = slip = 0.0
        fees = fee_rate * notional_in
        slip += p.qty * s * (p.entry_fill - p.entry_ref)
        for lg in p.legs:
            q = p.qty * float(lg["frac"])
            gross += q * s * (float(lg["ref"]) - p.entry_ref)
            slip += q * s * (float(lg["ref"]) - float(lg["fill"]))
            fees += fee_rate * q * float(lg["fill"])
        net = gross - fees - slip
        risk = p.qty * abs(p.entry_ref - p.stop)
        trade = {
            **p.as_dict(),
            "exit_reason": reason,
            "exit_ms": t_ms,
            "hold_min": (t_ms - p.entry_ms) / 60_000,
            "gross_usd": gross,
            "fee_usd": fees,
            "slip_usd": slip,
            "net_usd": net,
            "risk_usd": risk,
            "gross_r": gross / risk if risk > 0 else math.nan,
            "net_r": net / risk if risk > 0 else math.nan,
            "mfe_r": p.mfe,
            "mae_r": p.mae,
            "execution_mode": "SHADOW",
        }
        self.pos = None
        return trade
