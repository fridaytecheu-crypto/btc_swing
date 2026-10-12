"""V10 live features (`v10-feat-1`, design section 2): rolling flow / liquidation windows over
in-memory event buffers, price response, order-book metrics (gated), simple structure from 1m / 5m
bars built from trades, and percentile normalisation against the previous 4 hours of 10-second
samples. Every value at tick `now_ms` uses only events with exchange timestamp <= now_ms."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from btc_swing.v10.book import OrderBook
from btc_swing.v10.config import V10Config

MS_1M = 60_000
Feat = dict[str, float]


@dataclass
class Trade:
    t: int
    p: float
    q: float
    buy: bool


@dataclass
class Liq:
    t: int
    long_liq: bool  # S == "Buy": a LONG position was liquidated (audit + Bybit doc)
    notional: float
    qty: float


@dataclass
class Bar:
    open_ms: int
    o: float
    h: float
    lo: float
    c: float
    vol: float
    buy: float
    sell: float
    n: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "open_ms": self.open_ms,
            "o": self.o,
            "h": self.h,
            "l": self.lo,
            "c": self.c,
            "vol": self.vol,
            "buy": self.buy,
            "sell": self.sell,
            "n": self.n,
        }


class History:
    """Percentile rank against PREVIOUS samples only (NaN until `min_samples`)."""

    def __init__(self, max_samples: int, min_samples: int) -> None:
        self.vals: deque[float] = deque(maxlen=max_samples)
        self.min_samples = min_samples
        self._sorted: np.ndarray | None = None

    def add(self, v: float) -> None:
        if math.isfinite(v):
            self.vals.append(v)
            self._sorted = None

    def _arr(self) -> np.ndarray:
        if self._sorted is None:
            self._sorted = np.sort(np.array(self.vals, dtype=np.float64))
        return self._sorted

    def ready(self) -> bool:
        return len(self.vals) >= self.min_samples

    def rank(self, v: float) -> float:
        if not self.ready() or not math.isfinite(v):
            return math.nan
        a = (
            self._arr()
        )  # mid-rank: ties (e.g. exact zeros) count half, as in a standard percentile rank
        lo = float(np.searchsorted(a, v, side="left"))
        hi = float(np.searchsorted(a, v, side="right"))
        return 0.5 * (lo + hi) / len(a)

    def median(self) -> float:
        if not self.ready():
            return math.nan
        a = self._arr()
        return float(a[len(a) // 2])


def wilder_atr(bars: list[Bar], period: int) -> float:
    if len(bars) < period + 1:
        return math.nan
    trs = []
    for prev, b in zip(bars[-period - 1 : -1], bars[-period:], strict=True):
        trs.append(max(b.h - b.lo, abs(b.h - prev.c), abs(b.lo - prev.c)))
    return float(np.mean(trs))  # seeded Wilder ATR over the last `period` true ranges


def last_swings(bars: list[Bar], k: int) -> tuple[float, float]:
    """Last confirmed swing high / low (pivot confirmed k bars later)."""
    sh = sl = math.nan
    n = len(bars)
    for p in range(k, n - k):
        hp, lp = bars[p].h, bars[p].lo
        if all(bars[j].h < hp for j in range(p - k, p + k + 1) if j != p):
            sh = hp
        if all(bars[j].lo > lp for j in range(p - k, p + k + 1) if j != p):
            sl = lp
    return sh, sl


class BarBuilder:
    """Completed 1-minute bars from trades (never fabricated: minutes without trades are absent)."""

    def __init__(self, on_bar: Callable[[Bar], None] | None = None, keep: int = 1500) -> None:
        self.cur: Bar | None = None
        self.done: deque[Bar] = deque(maxlen=keep)
        self.on_bar = on_bar

    def add(self, tr: Trade) -> None:
        m = (tr.t // MS_1M) * MS_1M
        if self.cur is not None and m > self.cur.open_ms:
            self._close()
        if self.cur is None:
            self.cur = Bar(m, tr.p, tr.p, tr.p, tr.p, 0.0, 0.0, 0.0, 0)
        elif m < self.cur.open_ms:
            return  # late trade from an earlier minute after the bar closed: ignored
        b = self.cur
        b.h, b.lo, b.c, b.n = max(b.h, tr.p), min(b.lo, tr.p), tr.p, b.n + 1
        b.vol += tr.q
        if tr.buy:
            b.buy += tr.q
        else:
            b.sell += tr.q

    def finalize_until(self, now_ms: int, grace_ms: int = 2000) -> None:
        if self.cur is not None and now_ms >= self.cur.open_ms + MS_1M + grace_ms:
            self._close()

    def _close(self) -> None:
        if self.cur is None:
            return
        b, self.cur = self.cur, None
        self.done.append(b)
        if self.on_bar is not None:
            self.on_bar(b)

    def bars5(self, n: int) -> list[Bar]:
        """Completed 5-minute bars (aligned) from the completed 1m bars, last n."""
        out: list[Bar] = []
        groups: dict[int, list[Bar]] = {}
        for b in self.done:
            groups.setdefault((b.open_ms // (5 * MS_1M)) * 5 * MS_1M, []).append(b)
        keys = sorted(groups)
        last_min_open = self.done[-1].open_ms if self.done else 0
        for k in keys:
            g = groups[k]
            if k + 5 * MS_1M - MS_1M > last_min_open:
                break  # the 5m bar is not complete yet
            out.append(
                Bar(
                    k,
                    g[0].o,
                    max(x.h for x in g),
                    min(x.lo for x in g),
                    g[-1].c,
                    sum(x.vol for x in g),
                    sum(x.buy for x in g),
                    sum(x.sell for x in g),
                    sum(x.n for x in g),
                )
            )
        return out[-n:]


class FeatureEngine:
    def __init__(self, cfg: V10Config, on_bar: Callable[[Bar], None] | None = None) -> None:
        self.cfg = cfg
        fc = cfg.features
        self.trades: deque[Trade] = deque()
        self.liqs: deque[Liq] = deque()
        self.prices: deque[tuple[int, float]] = deque(maxlen=4 * 3600 + 600)  # 1-s samples
        self.bars = BarBuilder(on_bar)
        self.book = OrderBook(fc.book_depth_bps, fc.book_replenish_lookback_s, fc.book_max_age_s)
        self.last_trade: Trade | None = None
        self.last_liq_ms: int | None = None
        self.last_ticker: dict[str, float] = {}
        self.n_trades = 0
        self.n_liqs = 0
        self.n_book = 0
        hist_n = int(fc.history_hours * 3600 / fc.history_sample_s)
        self.hist: dict[str, History] = {}
        for w in fc.flow_windows_s:
            self.hist[f"abs_delta_{w}"] = History(hist_n, fc.history_min_samples)
            self.hist[f"intensity_{w}"] = History(hist_n, fc.history_min_samples)
        for w in fc.liq_windows_s:
            self.hist[f"liq_total_{w}"] = History(hist_n, fc.history_min_samples)
        self.hist["abs_impact_30"] = History(hist_n, fc.history_min_samples // 2)
        self._last_sample_ms = 0
        self._atr5 = math.nan
        self._atr1 = math.nan
        self._atr1h = math.nan
        self._swings = (math.nan, math.nan)
        self._bars_at_struct = -1

    # ------------------------------------------------------------------ ingestion
    def on_event(self, ev: dict[str, Any]) -> None:
        topic = ev["topic"]
        data = ev.get("data")
        if topic.startswith("publicTrade"):
            for t in data or []:
                tr = Trade(int(t["T"]), float(t["p"]), float(t["v"]), t.get("S") == "Buy")
                self.trades.append(tr)
                self.bars.add(tr)
                self.last_trade = tr
                self.n_trades += 1
        elif topic.startswith("allLiquidation"):
            for liq in data or []:
                q, p = float(liq.get("v", 0)), float(liq.get("p", 0))
                self.liqs.append(
                    Liq(int(liq.get("T", ev.get("ts") or 0)), liq.get("S") == "Buy", q * p, q)
                )
                self.last_liq_ms = int(liq.get("T", ev.get("ts") or 0))
                self.n_liqs += 1
        elif topic.startswith("orderbook.50"):
            self.book.apply(ev)
            self.n_book += 1
        elif topic.startswith("tickers") and isinstance(data, dict):
            for k in ("markPrice", "indexPrice", "bid1Price", "ask1Price", "lastPrice"):
                if data.get(k) not in (None, ""):
                    self.last_ticker[k] = float(data[k])

    def _trim(self, now_ms: int) -> None:
        keep = max(self.cfg.features.flow_windows_s) * 1000 + 5000
        while self.trades and self.trades[0].t < now_ms - keep:
            self.trades.popleft()
        keep_l = max(self.cfg.features.liq_windows_s) * 1000 + 5000
        while self.liqs and self.liqs[0].t < now_ms - keep_l:
            self.liqs.popleft()

    def _price_at(self, t_ms: int) -> float:
        """Last 1-second price sample at or before t_ms."""
        best = math.nan
        for ts, p in reversed(self.prices):
            if ts <= t_ms:
                best = p
                break
        return best

    def _structure(self) -> None:
        n = len(self.bars.done)
        if n == self._bars_at_struct:
            return
        self._bars_at_struct = n
        fc = self.cfg.features
        b1 = list(self.bars.done)
        b5 = self.bars.bars5(fc.atr_period + 60)
        self._atr5 = wilder_atr(b5, fc.atr_period)
        self._atr1 = wilder_atr(b1[-fc.atr_period - 1 :], fc.atr_period)
        groups: dict[int, list[Bar]] = {}
        for b in b1:
            groups.setdefault((b.open_ms // (60 * MS_1M)) * 60 * MS_1M, []).append(b)
        hb = [
            Bar(k, g[0].o, max(x.h for x in g), min(x.lo for x in g), g[-1].c, 0, 0, 0, 0)
            for k, g in sorted(groups.items())
        ]
        self._atr1h = wilder_atr(hb[:-1], fc.atr_period)  # completed hours only
        self._swings = last_swings(b5, fc.swing_k)

    # ------------------------------------------------------------------ compute
    def compute(self, now_ms: int) -> Feat:
        fc = self.cfg.features
        self.bars.finalize_until(now_ms)
        self._trim(now_ms)
        f: Feat = {"now_ms": float(now_ms)}
        lt = None
        for t in reversed(self.trades):  # latest trade at or before the tick (PIT)
            if t.t <= now_ms:
                lt = t
                break
        last = lt.p if lt is not None else math.nan
        f["last"] = last
        f["trade_age_s"] = (
            max(0.0, (now_ms - self.last_trade.t) / 1000) if self.last_trade else math.nan
        )
        f["liq_age_s"] = (now_ms - self.last_liq_ms) / 1000 if self.last_liq_ms else math.nan
        if math.isfinite(last) and (not self.prices or self.prices[-1][0] < now_ms):
            self.prices.append((now_ms, last))
        # flow windows
        trs = [t for t in self.trades if t.t <= now_ms]
        for w in fc.flow_windows_s:
            lo = now_ms - w * 1000
            buy = sell = 0.0
            n = 0
            for t in trs:
                if t.t > lo:
                    n += 1
                    if t.buy:
                        buy += t.q
                    else:
                        sell += t.q
            tot = buy + sell
            f[f"buy_{w}"], f[f"sell_{w}"], f[f"n_{w}"] = buy, sell, float(n)
            f[f"delta_{w}"] = buy - sell
            f[f"imb_{w}"] = (buy - sell) / tot if tot > 0 else math.nan
            f[f"intensity_{w}"] = n / w
            f[f"abs_delta_pct_{w}"] = self.hist[f"abs_delta_{w}"].rank(abs(buy - sell))
            f[f"intensity_pct_{w}"] = self.hist[f"intensity_{w}"].rank(n / w)
            p0 = self._price_at(lo)
            f[f"ret_{w}"] = (last / p0 - 1.0) * 1e4 if math.isfinite(p0) and p0 > 0 else math.nan
        # liquidation windows
        lqs = [q for q in self.liqs if q.t <= now_ms]
        for w in fc.liq_windows_s:
            lo = now_ms - w * 1000
            lng = sht = 0.0
            n = 0
            for q in lqs:
                if q.t > lo:
                    n += 1
                    if q.long_liq:
                        lng += q.notional
                    else:
                        sht += q.notional
            tot = lng + sht
            f[f"liq_long_{w}"], f[f"liq_short_{w}"], f[f"liq_n_{w}"] = lng, sht, float(n)
            f[f"liq_total_{w}"] = tot
            f[f"liq_imb_{w}"] = (sht - lng) / tot if tot > 0 else math.nan
            f[f"liq_burst_pct_{w}"] = self.hist[f"liq_total_{w}"].rank(tot)
        # structure
        self._structure()
        f["atr5"], f["atr1"], f["atr1h"] = self._atr5, self._atr1, self._atr1h
        f["swing_hi_5m"], f["swing_lo_5m"] = self._swings
        b1 = list(self.bars.done)
        rng = b1[-fc.structure_range_min :]
        f["range_hi_15"] = max(b.h for b in rng) if len(rng) >= fc.structure_range_min else math.nan
        f["range_lo_15"] = (
            min(b.lo for b in rng) if len(rng) >= fc.structure_range_min else math.nan
        )
        ext = b1[-fc.local_extreme_min :]
        f["hi_60"] = max(b.h for b in ext) if len(ext) >= fc.local_extreme_min else math.nan
        f["lo_60"] = min(b.lo for b in ext) if len(ext) >= fc.local_extreme_min else math.nan
        a5 = self._atr5
        if math.isfinite(a5) and a5 > 0 and math.isfinite(last):
            lo_ref = np.nanmin([f["lo_60"], f["swing_lo_5m"]])
            hi_ref = np.nanmax([f["hi_60"], f["swing_hi_5m"]])
            f["dist_lo"] = (last - lo_ref) / a5 if math.isfinite(lo_ref) else math.nan
            f["dist_hi"] = (hi_ref - last) / a5 if math.isfinite(hi_ref) else math.nan
        else:
            f["dist_lo"] = f["dist_hi"] = math.nan
        f["bars_1m"] = float(len(b1))
        # price response (30 s)
        med = self.hist["abs_delta_30"].median()
        d30, r30 = f.get("delta_30", math.nan), f.get("ret_30", math.nan)
        a1 = self._atr1
        if (
            math.isfinite(med)
            and med > 0
            and math.isfinite(d30)
            and abs(d30) > 0
            and math.isfinite(r30)
            and math.isfinite(a1)
            and a1 > 0
        ):
            disp_atr = (r30 / 1e4 * last) / a1
            f["impact_30"] = disp_atr / (d30 / med)
        else:
            f["impact_30"] = math.nan
        f["abs_impact_pct_30"] = self.hist["abs_impact_30"].rank(abs(f["impact_30"]))
        f["flow_sign_30"] = float(np.sign(d30)) if math.isfinite(d30) else math.nan
        f["ret_sign_30"] = float(np.sign(r30)) if math.isfinite(r30) else math.nan
        # book
        self.book.sample(now_ms)
        f.update(self.book.metrics(now_ms))
        for k in ("markPrice", "indexPrice"):
            f[k] = self.last_ticker.get(k, math.nan)
        f["warm"] = float(
            all(h.ready() for k, h in self.hist.items() if not k.startswith("abs_impact"))
            and math.isfinite(a5)
            and math.isfinite(a1)
        )
        # history sampling AFTER the ranks were taken (previous samples only)
        if now_ms - self._last_sample_ms >= fc.history_sample_s * 1000:
            self._last_sample_ms = now_ms
            for w in fc.flow_windows_s:
                self.hist[f"abs_delta_{w}"].add(abs(f[f"delta_{w}"]))
                self.hist[f"intensity_{w}"].add(f[f"intensity_{w}"])
            for w in fc.liq_windows_s:
                self.hist[f"liq_total_{w}"].add(f[f"liq_total_{w}"])
            if math.isfinite(f["impact_30"]):
                self.hist["abs_impact_30"].add(abs(f["impact_30"]))
        return f

    def history_state(self) -> dict[str, int]:
        return {k: len(h.vals) for k, h in self.hist.items()}
