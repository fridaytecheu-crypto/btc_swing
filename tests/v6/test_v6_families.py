from __future__ import annotations

import numpy as np

from btc_swing.core.enums import Side
from btc_swing.v6.config import V6Family
from btc_swing.v6.engine import V6Engine
from btc_swing.v6.families import (
    EpState,
    FailedBreakoutReversal,
    MomentumContinuation,
    first_in_cluster,
)
from btc_swing.v6.features import build_frame
from tests.v6.helpers import cfg, synthetic_inputs


def test_first_in_cluster() -> None:
    ev = np.zeros(100, dtype=bool)
    ev[[10, 15, 20, 40, 41, 60]] = True
    fic = first_in_cluster(ev, 12)
    assert np.flatnonzero(fic).tolist() == [10, 40, 60]


def _breakout_inputs(k0: int = 30 * 288 + 62):
    """Flat market, then a strong 15m up-break on volume with buying and rising OI, then a
    pullback close into the zone."""
    n = 36 * 288
    rng = np.random.RandomState(5)
    price = 60_000 + np.cumsum(rng.normal(0, 8, n))
    vol = rng.uniform(100, 120, n)
    bs = rng.uniform(0.48, 0.52, n)
    oi = np.full(n, 50_000.0) + rng.normal(0, 1, n)
    # 15m bar rows k0-2..k0 (k0 is a 15m completion: (k0+1) % 3 == 0)
    assert (k0 + 1) % 3 == 0
    # a mild uptrend before the break so the 1h trend is not opposed (family A's regime filter)
    price[k0 - 1500 : k0 - 2] += np.linspace(0.0, 900.0, 1498)
    price[k0 - 2 :] += 900.0
    level = price[k0 - 50 : k0 - 2].max()
    price[k0 - 2 : k0 + 1] = level + np.array([60.0, 120.0, 180.0])
    vol[k0 - 2 : k0 + 1] = 600.0
    bs[k0 - 2 : k0 + 1] = 0.75
    oi[k0 - 12 : k0 + 1] += np.linspace(0, 400, 13)
    # pullback: closes drift back into [level, level + 1 ATR5]
    price[k0 + 1] = level + 130.0
    price[k0 + 2] = level + 60.0  # back inside [level, level + 1 ATR5] (the level is a HIGH)
    price[k0 + 3 :] = level + 60.0 + np.cumsum(rng.normal(0, 8, n - k0 - 3))
    oi[k0 + 1 :] = oi[k0]
    return synthetic_inputs(n_days=36, price=price, volume=vol, buy_share=bs, oi=oi), level, k0


def test_momentum_event_and_pullback_trigger() -> None:
    c = cfg()
    inp, level, k0 = _breakout_inputs()
    fr = build_frame(inp, c)
    det = MomentumContinuation(c, fr, Side.LONG)
    assert det.events[k0], "the 15m break with volume/flow/OI confirmation must be a Stage A event"
    level_high = float(np.max(fr.cols["high"][k0 - 50 : k0 - 2]))  # 48 highs before the 15m bar
    assert abs(det.refs["level"][k0] - level_high) < 1e-6 and level_high > level
    ep = det.open_episode(k0, 1)
    trg = None
    for k in range(k0 + 1, k0 + 6):
        trg = det.step(ep, k)
        if trg is not None:
            break
    assert trg is not None and ep.state is EpState.ACTIONABLE
    assert trg.reason in ("PULLBACK_TO_LEVEL", "HIGHER_LOW")
    assert trg.structural_stop < level_high  # below the 15m structure since the event
    short = MomentumContinuation(c, fr, Side.SHORT)
    assert not short.events[k0]


def test_momentum_failed_break_invalidates() -> None:
    c = cfg()
    inp, level, k0 = _breakout_inputs()
    fr = build_frame(inp, c)
    det = MomentumContinuation(c, fr, Side.LONG)
    ep = det.open_episode(k0, 1)
    fr.cols["close"][k0 + 1] = level - 5 * fr.cols["atr5"][k0 + 1]
    det.step(ep, k0 + 1)
    assert ep.state is EpState.INVALIDATED and ep.end_reason == "FAILED_BREAK"


def test_episode_expires_at_max_age() -> None:
    c = cfg()
    inp, level, k0 = _breakout_inputs()
    fr = build_frame(inp, c)
    det = MomentumContinuation(c, fr, Side.LONG)
    ep = det.open_episode(k0, 1)
    # park the price far above the zone: EXTENDED, never actionable, expires
    fr.cols["close"][k0 + 1 : k0 + 40] = level + 10 * fr.cols["atr5"][k0 + 1]
    fr.cols["low"][k0 + 1 : k0 + 40] = level + 9 * fr.cols["atr5"][k0 + 1]
    for k in range(k0 + 1, k0 + 40):
        det.step(ep, k)
        if ep.state in (EpState.EXPIRED, EpState.INVALIDATED):
            break
    assert ep.state is EpState.EXPIRED and ep.end_reason == "MAX_AGE"
    assert ep.extended_bars > 0


def test_failed_breakout_short_event() -> None:
    c = cfg()
    n = 36 * 288
    rng = np.random.RandomState(9)
    price = 60_000 + np.cumsum(rng.normal(0, 8, n))
    vol = rng.uniform(100, 120, n)
    bs = rng.uniform(0.48, 0.52, n)
    oi = np.full(n, 50_000.0)
    k0 = 31 * 288 + 7
    hi24 = price[k0 - 288 : k0].max()
    price[k0] = hi24 + 150.0  # new 24h high ...
    vol[k0] = 900.0
    bs[k0] = 0.85  # ... on heavy buying
    price[k0 + 1] = hi24 - 30.0  # back inside next bar
    bs[k0 + 1] = 0.2  # CVD slope turns down -> not confirming
    price[k0 + 2 :] = hi24 - 30.0 + np.cumsum(rng.normal(0, 8, n - k0 - 2))
    inp = synthetic_inputs(n_days=36, price=price, volume=vol, buy_share=bs, oi=oi)
    fr = build_frame(inp, c)
    det = FailedBreakoutReversal(c, fr, Side.SHORT)
    assert det.events[k0 + 1]
    assert det.refs["sweep_extreme"][k0 + 1] >= fr.cols["high"][k0]
    assert det.refs["break_bar_extreme"][k0 + 1] == fr.cols["low"][k0]


def test_engine_takes_the_momentum_trade() -> None:
    c = cfg()
    inp, _level, k0 = _breakout_inputs()
    fr = build_frame(inp, c)
    eng = V6Engine(c, fr, inp.funding, families=[V6Family.MOMENTUM_CONTINUATION])
    res = eng.run(int(fr.close_ms[25 * 288]), int(fr.close_ms[-1]) + 1)
    t = res.trades
    assert t.height >= 1
    row = t.filter(t["event_ms"] == int(fr.close_ms[k0])).to_dicts()
    assert row, "the injected breakout must become a trade"
    r = row[0]
    assert r["side"] == "LONG" and r["family"] == "MOMENTUM_CONTINUATION"
    assert r["entry_ms"] == int(fr.open_ms[fr.idx_at(r["trigger_ms"]) + 1])  # next 5m open
    assert r["initial_stop"] < r["entry_fill"]
    assert r["leverage"] in (1, 2, 3, 5, 10)
