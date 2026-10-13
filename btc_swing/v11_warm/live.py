"""V11-WARM live runtime: the V11 live scorer (subclassed, same engine, same features, same scores)
with (a) a 31-day rolling frame so the bootstrap bars feed every rolling baseline immediately and
(b) a pre-declared live warm-up before the engine is in-window. Nothing here changes a strategy rule."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import numpy as np
import polars as pl

from btc_swing.v10.features import Bar
from btc_swing.v11.config import V11Config
from btc_swing.v11.engine import MS_1M, V11Engine
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.live import MIN_BARS, V11Live
from btc_swing.v11.scoring import score_frame
from btc_swing.v11_warm.config import STRATEGY, WarmCfg
from btc_swing.v11_warm.paths import V11WarmPaths


class V11WarmLive(V11Live):
    def __init__(
        self, cfg: V11Config, paths: V11WarmPaths, observation_start_ms: int, warm: WarmCfg
    ) -> None:
        self.warm = warm
        self.window_bars = warm.window_days * 1440
        super().__init__(cfg, paths, observation_start_ms)
        self.state["strategy"] = STRATEGY
        self.state["warm_start"] = {
            "window_days": warm.window_days,
            "live_warmup_min": warm.live_warmup_min,
        }
        self._last_tick_open_ms = (
            int(self.micro.bars.done[-1].open_ms) if self.micro.bars.done else -1
        )

    # ------------------------------------------------------------------ persistence (warm window)
    def _load_state(self) -> None:
        """V11 state restore, with the bar window = window_days and gap-fill bars (unknown taker
        split -> NaN) accepted. Runs inside the V11 constructor."""
        if not hasattr(self, "window_bars"):  # parent constructor ordering guard
            self.window_bars = 31 * 1440
        self.n_bootstrap = 0
        p = self.paths.state
        if p.exists():
            st = json.loads(p.read_text())
            if st.get("observation_start_ms") == self.observation_start_ms:
                self.state = {**self.state, **st}
                self.oi = [(int(a), float(b)) for a, b in self.state.get("oi") or []]
                self.funding = [(int(a), float(b)) for a, b in self.state.get("funding") or []]
                for k, vals in (self.state.get("micro_history") or {}).items():
                    if k in self.micro.hist:
                        for v in vals:
                            self.micro.hist[k].add(float(v))
        self.micro.bars.done = deque(maxlen=self.window_bars)
        if self.paths.bars_1m.exists():
            with self.paths.bars_1m.open() as fh:
                rows = [json.loads(line) for line in fh if line.strip()][-self.window_bars :]
            for r in rows:
                if r.get("src") in ("bootstrap", "kline_gapfill"):
                    self.n_bootstrap += 1
                self.micro.bars.done.append(
                    Bar(
                        int(r["open_ms"]),
                        float(r["o"]),
                        float(r["h"]),
                        float(r["l"]),
                        float(r["c"]),
                        float(r["vol"]),
                        float(r["buy"]) if r.get("buy") is not None else float("nan"),
                        float(r["sell"]) if r.get("sell") is not None else float("nan"),
                        int(r["n"]),
                    )
                )
            self._n_bars_seen = len(self.micro.bars.done)

    # ------------------------------------------------------------------ tick (V11 tick on the warm window)
    def tick(self, now_ms: int) -> dict[str, Any] | None:
        self.micro.compute(now_ms)
        n = len(self.micro.bars.done)
        if n < MIN_BARS:
            return None
        # a new completed bar is detected by its open time, never by the deque length: a full
        # deque keeps a constant length while it rolls (the V11 live loop compares lengths)
        last_open = int(self.micro.bars.done[-1].open_ms)
        if last_open == self._last_tick_open_ms:
            return None
        self._last_tick_open_ms = last_open
        self._n_bars_seen = n
        bars = list(self.micro.bars.done)[-self.window_bars :]
        rows = pl.DataFrame(
            {
                "open_time_ms": [b.open_ms for b in bars],
                "open": [b.o for b in bars],
                "high": [b.h for b in bars],
                "low": [b.lo for b in bars],
                "close": [b.c for b in bars],
                "volume": [b.vol for b in bars],
                "n_trades": [float(b.n) for b in bars],
                "buy_qty": [b.buy for b in bars],
                "sell_qty": [b.sell for b in bars],
            }
        )
        met = (
            pl.DataFrame(
                {"time_ms": [t for t, _ in self.oi], "open_interest": [v for _, v in self.oi]}
            )
            if self.oi
            else None
        )
        fnd = (
            pl.DataFrame(
                {
                    "time_ms": [t for t, _ in self.funding],
                    "funding_rate": [v for _, v in self.funding],
                }
            )
            if self.funding
            else None
        )
        cfg_live = self.cfg.model_copy(
            update={"features": self.cfg.features.model_copy(update={"binance_latency_s": 0})}
        )
        fr = build_frame_v11(rows, cfg_live, met, fnd)
        i = int(np.searchsorted(fr.t_open, bars[-1].open_ms))
        if i >= len(fr) or fr.t_open[i] != bars[-1].open_ms:
            return None
        micro_arrays = self._micro_arrays(len(fr), i, bars[-1].open_ms + MS_1M)
        sc = score_frame(fr, self.cfg, micro_arrays)
        if self.engine is None:
            self.engine = V11Engine(self.cfg, fr, sc)
            self._restore_engine()
        else:
            self.engine.fr, self.engine.X, self.engine.sc, self.engine.t_open = (
                fr,
                fr.c,
                sc,
                fr.t_open,
            )
        eng = self.engine
        t_close = int(fr.t_open[i]) + MS_1M
        self.state["last_bar_ms"] = t_close
        self.last_features = eng.snapshot(i)
        a1h = float(fr.c["atr1h"][i])  # sizing input; not part of the V11 snapshot keys
        self.last_features["atr1h"] = None if np.isnan(a1h) else a1h
        self.last_scores = {
            "long": eng._score(1, i),
            "short": eng._score(-1, i),
            "contrib_long": eng._contrib(1, i),
            "contrib_short": eng._contrib(-1, i),
        }
        pos_before = eng.pos
        n_tr = len(eng.trades)
        rec = eng.process_row(i, in_window=t_close > self.observation_start_ms)
        if eng.pending_open is not None:
            fill_ref = float(fr.c["close"][i])
            pos = eng.open_from_pending(i, fill_ref=fill_ref)
            if pos is not None:
                pos.entry_ms = t_close
                self.j_sh.append("SHADOW_OPEN", pos.as_dict())
        if len(eng.trades) > n_tr:
            for t in eng.trades[n_tr:]:
                d = t.as_dict()
                self.j_sh.append("SHADOW_CLOSED", d)
                self.state["trades"] += 1
                self.state["cum_net_usd"] = float(self.state["cum_net_usd"]) + float(t.net_usd)
                self.state["wins" if t.net_usd > 0 else "losses"] += 1
            eng.trades = eng.trades[-50:]
        self._journal_row(eng, i, t_close, rec, pos_before)
        self._outcomes(t_close)
        self.save()
        return rec

    # ------------------------------------------------------------------ diagnostics
    def warm_check(self) -> dict[str, Any]:
        """Which pre-declared fields / groups are populated on the latest completed bar."""
        feats = self.last_features or {}
        contrib = (self.last_scores or {}).get("contrib_long") or {}
        fields = {k: feats.get(k) is not None for k in self.warm.required_warm_fields}
        groups = {g: contrib.get(g) is not None for g in self.warm.required_warm_groups}
        return {
            "fields": fields,
            "groups": groups,
            "all_fields": all(fields.values()) if fields else False,
            "all_groups": all(groups.values()) if groups else False,
            "microstructure_available": contrib.get("microstructure") is not None,
            "derivatives_available": contrib.get("derivatives") is not None,
            "bootstrap_bars_in_window": self.n_bootstrap,
            "bars_in_window": len(self.micro.bars.done),
        }

    def diagnostic(self) -> dict[str, Any]:
        d = super().diagnostic()
        d["strategy"] = STRATEGY
        d["mode"] = "SHADOW_ONLY (warm-started V11)"
        d["warm"] = self.warm_check()
        d["in_window"] = bool(
            self.state.get("last_bar_ms") and self.state["last_bar_ms"] > self.observation_start_ms
        )
        return d
