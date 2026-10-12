"""V9 deterministic engine: variant specification -> armed 5m bars -> 1m trigger -> taker entry ->
1m-resolved stop / target / time exits -> conservative Bybit cost accounting (design section 4-5).
One position at a time; no averaging, pyramiding or trailing. The frozen `size_position` ladder sets
the leverage; it never sets the risk."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from btc_swing.core.config import RiskCfg
from btc_swing.core.enums import Side
from btc_swing.risk.sizing import size_position
from btc_swing.v9.config import V9Config
from btc_swing.v9.features import V9Frame
from btc_swing.v9.setups import DirectionTables

F = NDArray[np.float64]
MS_1M = 60_000
FUNDING_PERIOD_MS = 8 * 3_600_000


@dataclass(frozen=True)
class Spec:
    """One fully specified variant. `None` disables a rule."""

    name: str
    impulse_atr: float
    impulse_rvol: float | None
    retrace: tuple[float, float]
    pb_vol_ratio: float | None
    trig_rvol: float | None
    imbalance: float | None
    delta_mode: str | None  # None | "sign" | "median"
    accel_min: float | None
    min_stop_pct: float
    exit: str  # A | B | C
    time_stop_min: int
    risk_per_trade: float
    cost_mult: float = 1.0
    stop_mode: str = "reject"  # reject | floor (sensitivity only)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ladder(cfg: V9Config, exit_mode: str | None = None, time_stop: int | None = None) -> list[Spec]:
    """The A/B/C/D steps at the pre-registered anchors."""
    base = dict(
        impulse_atr=cfg.impulse.size_atr,
        retrace=(cfg.pullback.retrace_min, cfg.pullback.retrace_max),
        min_stop_pct=cfg.stop.min_distance_pct,
        exit=exit_mode or cfg.exits.primary,
        time_stop_min=time_stop or cfg.exits.time_stop_min,
        risk_per_trade=cfg.risk.risk_per_trade,
        accel_min=None,
    )
    a = Spec(
        name="A_PRICE_ONLY",
        impulse_rvol=None,
        pb_vol_ratio=None,
        trig_rvol=None,
        imbalance=None,
        delta_mode=None,
        **base,  # type: ignore[arg-type]
    )
    b = Spec(
        name="B_IMPULSE_RVOL",
        impulse_rvol=cfg.impulse.rvol_min,
        pb_vol_ratio=None,
        trig_rvol=None,
        imbalance=None,
        delta_mode=None,
        **base,  # type: ignore[arg-type]
    )
    c = Spec(
        name="C_PULLBACK_VOLUME",
        impulse_rvol=cfg.impulse.rvol_min,
        pb_vol_ratio=cfg.pullback.volume_ratio_max,
        trig_rvol=None,
        imbalance=None,
        delta_mode=None,
        **base,  # type: ignore[arg-type]
    )
    d = Spec(
        name="D_TRIGGER_FLOW",
        impulse_rvol=cfg.impulse.rvol_min,
        pb_vol_ratio=cfg.pullback.volume_ratio_max,
        trig_rvol=cfg.trigger.rvol_min,
        imbalance=cfg.trigger.imbalance_min,
        delta_mode=cfg.trigger.delta_mode,
        **base,  # type: ignore[arg-type]
    )
    return [a, b, c, d]


@dataclass
class Trade:
    direction: int
    leg_p: int
    arm_bar: int
    trigger_minute: int
    entry_minute: int
    exit_minute: int
    entry_ref: float
    stop_ref: float
    stop_pct: float
    qty: float
    leverage: float
    risk_capped: bool
    legs: list[tuple[float, float, str, int]]  # (fraction, exit_ref, type, minute)
    gross_usd: float
    fee_usd: float
    slip_usd: float
    funding_usd: float
    funding_events: int
    accel1: float
    rvol1: float
    imb1: float
    hold_min: int

    @property
    def net_usd(self) -> float:
        return self.gross_usd - self.fee_usd - self.slip_usd + self.funding_usd

    @property
    def risk_usd(self) -> float:
        return self.qty * abs(self.entry_ref - self.stop_ref)

    @property
    def notional_usd(self) -> float:
        return self.qty * self.entry_ref

    def r(self, usd: float) -> float:
        return usd / self.risk_usd if self.risk_usd > 0 else math.nan

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "leg_p": self.leg_p,
            "arm_bar": self.arm_bar,
            "trigger_minute": self.trigger_minute,
            "entry_minute": self.entry_minute,
            "exit_minute": self.exit_minute,
            "entry_ref": self.entry_ref,
            "stop_ref": self.stop_ref,
            "stop_pct": self.stop_pct,
            "qty": self.qty,
            "leverage": self.leverage,
            "risk_capped": self.risk_capped,
            "exit_types": [lg[2] for lg in self.legs],
            "gross_r": self.r(self.gross_usd),
            "fee_r": self.r(self.fee_usd),
            "slip_r": self.r(self.slip_usd),
            "funding_r": self.r(self.funding_usd),
            "net_r": self.r(self.net_usd),
            "net_usd": self.net_usd,
            "hold_min": self.hold_min,
            "accel1": self.accel1,
            "rvol1": self.rvol1,
            "imb1": self.imb1,
        }


@dataclass
class Funnel:
    """Distinct impulse legs (p, direction) reaching each stage; see design section 5."""

    raw: int = 0
    after_trend: int = 0
    after_impulse: int = 0
    after_rvol: int = 0
    after_pullback: int = 0
    after_pb_volume: int = 0
    after_trigger: int = 0
    after_flow: int = 0
    skipped_in_position: int = 0
    rejected_micro_stop: int = 0
    floored_micro_stop: int = 0
    rejected_sizing: int = 0
    executed: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class RunResult:
    spec: Spec
    trades: list[Trade]
    funnel: Funnel
    days: int
    meta: dict[str, Any] = field(default_factory=dict)


class V9Engine:
    def __init__(
        self,
        fr: V9Frame,
        tables: dict[int, DirectionTables],
        cfg: V9Config,
        funding_t: NDArray[np.int64] | None = None,
        funding_r: F | None = None,
    ) -> None:
        self.fr = fr
        self.tables = tables
        self.cfg = cfg
        self.fund_t = funding_t if funding_t is not None else np.zeros(0, dtype=np.int64)
        self.fund_r = funding_r if funding_r is not None else np.zeros(0)
        m = fr.m
        self.o, self.h, self.l, self.c = m["open"], m["high"], m["low"], m["close"]
        self.valid = fr.valid

    # ------------------------------------------------------------------ arming
    def armed_mask(self, spec: Spec, d: int) -> NDArray[np.bool_]:
        t = self.tables[d]
        p = t.arms.p
        okp = p >= 0
        pc = np.clip(p, 0, len(p) - 1)
        ok = okp & t.legs.ok[pc] & t.legs.trend_ok[pc] & (t.legs.size_atr[pc] >= spec.impulse_atr)
        if spec.impulse_rvol is not None:
            ok &= t.legs.imp_rvol[pc] >= spec.impulse_rvol
        ok &= (t.arms.retrace >= spec.retrace[0]) & (t.arms.retrace <= spec.retrace[1])
        ok &= t.arms.origin_intact
        if spec.pb_vol_ratio is not None:
            ok &= t.arms.pb_ratio <= spec.pb_vol_ratio
        return ok

    def flow_ok(self, spec: Spec, d: int, m: int) -> bool:
        f = self.fr.m
        if spec.trig_rvol is not None and not (f["rvol1"][m] >= spec.trig_rvol):
            return False
        if spec.imbalance is not None and not (d * f["imb1"][m] >= spec.imbalance):
            return False
        if spec.delta_mode == "sign" and not (d * f["delta1"][m] > 0):
            return False
        if spec.delta_mode == "median" and not (d * f["delta1"][m] > f["delta_med60"][m]):
            return False
        return not (spec.accel_min is not None and not (f["accel1"][m] >= spec.accel_min))

    def trigger_in_bar(self, spec: Spec, d: int, bar: int) -> tuple[int, bool]:
        """First breakout minute inside 5m bar `bar` and whether any breakout passed the flow block.
        Returns (minute of the first minute passing everything, or -1; breakout_seen)."""
        brk = self.fr.m["brk_up"] if d > 0 else self.fr.m["brk_dn"]
        m0 = bar * 5
        seen = False
        for m in range(m0, min(m0 + 5, len(self.valid))):
            if not self.valid[m] or brk[m] != 1.0:
                continue
            seen = True
            if self.flow_ok(spec, d, m):
                return m, True
        return -1, seen

    # ------------------------------------------------------------------ simulation
    def run(self, spec: Spec, start_ms: int, end_ms: int) -> RunResult:
        fr, cfg = self.fr, self.cfg
        t_open = fr.t_open
        n5 = fr.n5
        bar_start = int(np.searchsorted(t_open, start_ms, side="left")) // 5
        bar_end = int(np.searchsorted(t_open, end_ms, side="left")) // 5
        days = max(1, round((end_ms - start_ms) / 86_400_000))
        funnel = self._leg_funnel(spec, bar_start, bar_end)
        armed = {d: self.armed_mask(spec, d) for d in (1, -1)}
        events: list[tuple[int, int]] = []  # (bar, direction) armed bars in window
        for d in (1, -1):
            idx = np.nonzero(armed[d][bar_start:bar_end])[0] + bar_start
            events.extend((int(b), d) for b in idx)
        events.sort()
        trades: list[Trade] = []
        busy_until = -1  # minute index until which a position is open
        seen_trig: set[tuple[int, int]] = set()
        seen_flow: set[tuple[int, int]] = set()
        risk_cfg = self._risk_cfg(spec)
        equity = cfg.risk.initial_equity
        for bar, d in events:
            if bar + 1 >= n5:
                break
            leg_id = (int(self.tables[d].arms.p[bar]), d)
            m_trig, brk_seen = self.trigger_in_bar(spec, d, bar + 1)
            if brk_seen:
                seen_trig.add(leg_id)
            if m_trig < 0:
                continue
            seen_flow.add(leg_id)
            m_entry = m_trig + 1
            if m_entry >= len(t_open) or not self.valid[m_entry]:
                continue
            if m_trig < busy_until:
                funnel.skipped_in_position += 1
                continue
            tr = self._open_trade(spec, d, bar, m_trig, m_entry, risk_cfg, equity, funnel)
            if tr is None:
                continue
            trades.append(tr)
            busy_until = tr.exit_minute + 1
            if cfg.risk.compounding:
                equity += tr.net_usd
        funnel.after_trigger = len(seen_trig)
        funnel.after_flow = len(seen_flow)
        funnel.executed = len(trades)
        return RunResult(spec, trades, funnel, days, {"bar_start": bar_start, "bar_end": bar_end})

    def _risk_cfg(self, spec: Spec) -> RiskCfg:
        r = self.cfg.risk
        return RiskCfg(
            initial_equity=r.initial_equity,
            risk_per_trade=spec.risk_per_trade,
            allowed_leverage=r.allowed_leverage,
            max_leverage=r.max_leverage,
            margin_cap_frac=r.margin_cap_frac,
            maintenance_margin_rate=r.maintenance_margin_rate,
            min_stop_to_liquidation_ratio=r.min_stop_to_liquidation_ratio,
            min_liquidation_distance_atr=r.min_liquidation_distance_atr,
            liquidation_atr_tf=r.liquidation_atr_tf,
            compounding=r.compounding,
        )

    def _leg_funnel(self, spec: Spec, bar_start: int, bar_end: int) -> Funnel:
        """Leg-level stages (counted on the peak bar p inside the window)."""
        fn = Funnel()
        sl = slice(bar_start, bar_end)
        for d in (1, -1):
            lg = self.tables[d].legs
            raw = lg.ok[sl] & (lg.size_atr[sl] >= self.cfg.impulse.raw_size_atr)
            fn.raw += int(raw.sum())
            tr = raw & lg.trend_ok[sl]
            fn.after_trend += int(tr.sum())
            im = tr & (lg.size_atr[sl] >= spec.impulse_atr)
            fn.after_impulse += int(im.sum())
            rv = im & (lg.imp_rvol[sl] >= (spec.impulse_rvol or 0.0))
            fn.after_rvol += int(rv.sum())
            # pullback stage without the volume rule (pb volume stage = armed under the spec)
            spec_no_pbv = Spec(**{**spec.as_dict(), "pb_vol_ratio": None})
            am = self.armed_mask(spec_no_pbv, d)[sl]
            p = self.tables[d].arms.p[sl]
            legs_pb = set(p[am].tolist())
            am2 = self.armed_mask(spec, d)[sl]
            legs_pbv = set(p[am2].tolist())
            fn.after_pullback += len(legs_pb)
            fn.after_pb_volume += len(legs_pbv)
        return fn

    def _open_trade(
        self,
        spec: Spec,
        d: int,
        bar: int,
        m_trig: int,
        m_entry: int,
        risk_cfg: RiskCfg,
        equity: float,
        funnel: Funnel,
    ) -> Trade | None:
        fr, cfg = self.fr, self.cfg
        t = self.tables[d]
        p = int(t.arms.p[bar])
        entry_ref = float(self.o[m_entry])
        atr5 = float(fr.b5["atr5"][bar])
        pb_low_m = float(t.arms.pb_low[bar])  # mirrored space
        stop_m = pb_low_m - cfg.stop.buffer_atr * atr5
        stop_ref = stop_m if d > 0 else -stop_m
        stop_dist = d * (entry_ref - stop_ref)
        if not (stop_dist > 0):
            funnel.rejected_micro_stop += 1
            return None
        stop_pct = 100.0 * stop_dist / entry_ref
        if stop_pct < spec.min_stop_pct:
            if spec.stop_mode != "floor":
                funnel.rejected_micro_stop += 1
                return None
            funnel.floored_micro_stop += 1
            stop_pct = spec.min_stop_pct
            stop_dist = entry_ref * stop_pct / 100.0
            stop_ref = entry_ref - d * stop_dist
        side = Side.LONG if d > 0 else Side.SHORT
        sz = size_position(equity, entry_ref, stop_ref, side, float(fr.b5["atr1h"][bar]), risk_cfg)
        if not sz.accepted or sz.qty <= 0:
            funnel.rejected_sizing += 1
            return None
        qty = float(sz.qty)
        legs, exit_minute = self._simulate(spec, d, m_entry, entry_ref, stop_ref, stop_dist)
        cm = spec.cost_mult
        co = cfg.costs
        fee = co.taker_fee_bps * cm / 1e4
        entry_fill = entry_ref * (1 + d * (co.entry_slippage_bps + co.half_spread_bps) * cm / 1e4)
        gross = fee_usd = slip_usd = 0.0
        fee_usd += fee * qty * entry_fill
        slip_usd += qty * d * (entry_fill - entry_ref)
        for frac, px, kind, _m in legs:
            s_bps = co.stop_slippage_bps if kind == "STOP" else co.exit_slippage_bps
            fill = px * (1 - d * (s_bps + co.half_spread_bps) * cm / 1e4)
            gross += frac * qty * d * (px - entry_ref)
            slip_usd += frac * qty * d * (px - fill)
            fee_usd += fee * frac * qty * fill
        funding_usd, n_fund = self._funding(d, qty, m_entry, legs)
        return Trade(
            direction=d,
            leg_p=p,
            arm_bar=bar,
            trigger_minute=m_trig,
            entry_minute=m_entry,
            exit_minute=exit_minute,
            entry_ref=entry_ref,
            stop_ref=stop_ref,
            stop_pct=stop_pct,
            qty=qty,
            leverage=float(sz.leverage),
            risk_capped=bool(sz.risk_capped),
            legs=legs,
            gross_usd=gross,
            fee_usd=fee_usd,
            slip_usd=slip_usd,
            funding_usd=funding_usd,
            funding_events=n_fund,
            accel1=float(fr.m["accel1"][m_trig]),
            rvol1=float(fr.m["rvol1"][m_trig]),
            imb1=float(fr.m["imb1"][m_trig]),
            hold_min=exit_minute - m_entry + 1,
        )

    def _simulate(
        self, spec: Spec, d: int, m0: int, entry: float, stop: float, dist: float
    ) -> tuple[list[tuple[float, float, str, int]], int]:
        ex = self.cfg.exits
        if spec.exit == "A":
            targets = [(1.0, ex.exit_a_r)]
        elif spec.exit == "B":
            targets = [(1.0, ex.exit_b_r)]
        else:
            targets = [(0.5, ex.split_tp1_r), (0.5, ex.split_tp2_r)]
        time_stop = spec.time_stop_min
        hard_max = time_stop * ex.hard_max_multiple
        legs: list[tuple[float, float, str, int]] = []
        open_frac = 1.0
        ti = 0
        n = len(self.valid)
        last_valid = m0
        time_checked = False
        for m in range(m0, min(m0 + hard_max, n)):
            if not self.valid[m]:
                continue
            last_valid = m
            fav = self.h[m] if d > 0 else self.l[m]
            adv = self.l[m] if d > 0 else self.h[m]
            r_lo = d * (adv - entry) / dist
            r_hi = d * (fav - entry) / dist
            if r_lo <= -1.0:
                legs.append((open_frac, stop, "STOP", m))
                return legs, m
            while ti < len(targets) and r_hi >= targets[ti][1]:
                frac, tr = targets[ti]
                legs.append((frac, entry + d * tr * dist, f"TP{ti + 1}", m))
                open_frac -= frac
                ti += 1
            if open_frac <= 1e-12:
                return legs, m
            if not time_checked and m - m0 + 1 >= time_stop:
                time_checked = True
                r_close = d * (self.c[m] - entry) / dist
                if r_close < ex.progress_min_r:
                    legs.append((open_frac, float(self.c[m]), "TIME", m))
                    return legs, m
        m_last = last_valid
        legs.append((open_frac, float(self.c[m_last]), "HARD", m_last))
        return legs, m_last

    def _funding(
        self, d: int, qty: float, m_entry: int, legs: list[tuple[float, float, str, int]]
    ) -> tuple[float, int]:
        if not self.cfg.costs.apply_funding or len(self.fund_t) == 0:
            return 0.0, 0
        t_in = int(self.fr.t_open[m_entry])
        m_exit = max(lg[3] for lg in legs)
        t_out = int(self.fr.t_open[m_exit]) + MS_1M
        lo = int(np.searchsorted(self.fund_t, t_in, side="right"))
        hi = int(np.searchsorted(self.fund_t, t_out, side="right"))
        total = 0.0
        n_ev = 0
        for j in range(lo, hi):
            tm = int(self.fund_t[j])
            mi = int(np.searchsorted(self.fr.t_open, tm, side="right")) - 1
            open_frac = 1.0 - sum(lg[0] for lg in legs if int(self.fr.t_open[lg[3]]) + MS_1M < tm)
            if open_frac <= 0:
                continue
            px = float(self.c[mi]) if self.valid[mi] else math.nan
            if math.isnan(px):
                continue
            total += -d * float(self.fund_r[j]) * qty * open_frac * px
            n_ev += 1
        return total, n_ev
