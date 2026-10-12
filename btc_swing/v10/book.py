"""Local order book from orderbook.50 snapshots/deltas with validity gating: valid only after a
snapshot, with unbroken `u` continuity since, and updated within `max_age_s`. Depth metrics within a
band of basis points around the mid, plus a ring of depth samples for replenishment / depletion."""

from __future__ import annotations

import math
from collections import deque
from typing import Any


class OrderBook:
    def __init__(self, depth_bps: float, replenish_lookback_s: int, max_age_s: float) -> None:
        self.depth_bps = depth_bps
        self.lookback_ms = replenish_lookback_s * 1000
        self.max_age_ms = int(max_age_s * 1000)
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.valid = False
        self.last_u: int | None = None
        self.last_update_ms: int | None = None
        self.invalidations = 0
        self.snapshots = 0
        self._samples: deque[tuple[int, float, float]] = deque(maxlen=600)

    def apply(self, ev: dict[str, Any]) -> None:
        data = ev.get("data") or {}
        typ = ev.get("type")
        if typ == "snapshot":
            self.bids = {float(p): float(q) for p, q in data.get("b", [])}
            self.asks = {float(p): float(q) for p, q in data.get("a", [])}
            self.valid = True
            self.snapshots += 1
        else:
            if ev.get("seq_gap") and self.valid:
                self.valid = False
                self.invalidations += 1
            if self.valid:
                for p, q in data.get("b", []):
                    fp, fq = float(p), float(q)
                    if fq == 0:
                        self.bids.pop(fp, None)
                    else:
                        self.bids[fp] = fq
                for p, q in data.get("a", []):
                    fp, fq = float(p), float(q)
                    if fq == 0:
                        self.asks.pop(fp, None)
                    else:
                        self.asks[fp] = fq
        u = data.get("u")
        self.last_u = int(u) if u is not None else self.last_u
        ts = ev.get("ts")
        self.last_update_ms = int(ts) if isinstance(ts, int | float) else ev.get("rx_ms")
        if self.valid and self.bids and self.asks and max(self.bids) >= min(self.asks):
            self.valid = False  # crossed book: never trust it
            self.invalidations += 1

    def is_fresh(self, now_ms: int) -> bool:
        return (
            self.valid
            and bool(self.bids)
            and bool(self.asks)
            and self.last_update_ms is not None
            and now_ms - self.last_update_ms <= self.max_age_ms
        )

    def sample(self, now_ms: int) -> None:
        m = self.metrics(now_ms)
        if m["book_valid"] == 1.0:
            self._samples.append((now_ms, m["bid_depth"], m["ask_depth"]))

    def metrics(self, now_ms: int) -> dict[str, float]:
        out = {
            "book_valid": 0.0,
            "bid1": math.nan,
            "ask1": math.nan,
            "spread_bps": math.nan,
            "bid_depth": math.nan,
            "ask_depth": math.nan,
            "depth_imb": math.nan,
            "bid_repl": math.nan,
            "ask_repl": math.nan,
            "book_age_ms": math.nan
            if self.last_update_ms is None
            else float(now_ms - self.last_update_ms),
        }
        if not self.is_fresh(now_ms):
            return out
        bb, ba = max(self.bids), min(self.asks)
        mid = 0.5 * (bb + ba)
        lo, hi = mid * (1 - self.depth_bps / 1e4), mid * (1 + self.depth_bps / 1e4)
        bd = sum(q for p, q in self.bids.items() if p >= lo)
        ad = sum(q for p, q in self.asks.items() if p <= hi)
        out.update(
            {
                "book_valid": 1.0,
                "bid1": bb,
                "ask1": ba,
                "spread_bps": (ba - bb) / mid * 1e4,
                "bid_depth": bd,
                "ask_depth": ad,
                "depth_imb": (bd - ad) / (bd + ad) if bd + ad > 0 else math.nan,
            }
        )
        # replenishment: depth now / depth about lookback ago (the oldest sample inside the lookback)
        ref = None
        for ts, b, a in self._samples:
            if now_ms - ts <= self.lookback_ms:
                ref = (b, a)
                break
        if ref is not None:
            out["bid_repl"] = bd / ref[0] if ref[0] > 0 else math.nan
            out["ask_repl"] = ad / ref[1] if ref[1] > 0 else math.nan
        return out
