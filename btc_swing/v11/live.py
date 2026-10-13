"""V11 live shadow engine: own feed events -> own 1m bars (V10 bar builder as a library, separate
instance) -> the SAME frame / score / engine code as the historical run on a rolling window, with the
forward-only microstructure group from a V11-owned V10 feature-engine instance, and Bybit-native
ticker open interest / funding for the derivatives group. Hash-chained journals, state, outcomes.
No exchange client is imported anywhere in this module or package."""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v10.features import Bar, FeatureEngine
from btc_swing.v11.config import GROUPS, STRATEGY, V11Config
from btc_swing.v11.engine import MS_1M, Position, V11Engine
from btc_swing.v11.features import build_frame_v11
from btc_swing.v11.paths import V11Paths
from btc_swing.v11.research import _clean
from btc_swing.v11.scoring import score_frame

log = logging.getLogger(__name__)
WINDOW_BARS = (
    22 * 1440
)  # rolling 22-day window (RVOL baseline 20 d + ATR percentile 7 d fit inside)
MIN_BARS = 2 * 60  # frame needs at least two hours of completed bars


def _iso(ms_: int | float | None) -> str | None:
    return None if ms_ is None else datetime.fromtimestamp(ms_ / 1000, tz=UTC).isoformat()


class V11Live:
    def __init__(self, cfg: V11Config, paths: V11Paths, observation_start_ms: int) -> None:
        from btc_swing.v10.config import load_v10_config  # microstructure feature definitions only

        self.cfg, self.paths = cfg, paths
        paths.ensure()
        self.observation_start_ms = observation_start_ms
        self.micro = FeatureEngine(load_v10_config(), on_bar=self._on_bar)  # own instance, own feed
        self.micro.bars.done = deque(maxlen=WINDOW_BARS)  # V11-owned builder keeps the full window
        self.oi: list[tuple[int, float]] = []
        self.funding: list[tuple[int, float]] = []
        self.engine: V11Engine | None = None
        self.j_cand = HashChainJournal(paths.candidates, "v11_candidates")
        self.j_ep = HashChainJournal(paths.episodes, "v11_episodes")
        self.j_sig = HashChainJournal(paths.signals, "v11_signals")
        self.j_nm = HashChainJournal(paths.nearmiss, "v11_nearmiss")
        self.j_sh = HashChainJournal(paths.shadow_trades, "v11_shadow_trades")
        self.j_out = HashChainJournal(paths.outcomes, "v11_outcomes")
        self.state: dict[str, Any] = {
            "strategy": STRATEGY,
            "observation_start_ms": observation_start_ms,
            "position": None,
            "armed": {},
            "counters": {
                "candidates": 0,
                "armed": 0,
                "triggers": 0,
                "executed": 0,
                "blocked": 0,
                "skipped_while_open": 0,
                "nearmiss": 0,
            },
            "trades": 0,
            "cum_net_usd": 0.0,
            "wins": 0,
            "losses": 0,
            "last_bar_ms": None,
            "last_event_ms": None,
            "last_signal": None,
            "pending_outcomes": [],
            "micro_history": {},
            "oi": [],
            "funding": [],
        }
        self._n_bars_seen = 0
        self._cand_last: dict[str, int] = {}
        self.last_features: dict[str, Any] = {}
        self.last_scores: dict[str, Any] = {}
        self._load_state()

    # ------------------------------------------------------------------ persistence
    def _load_state(self) -> None:
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
        if self.paths.bars_1m.exists():
            with self.paths.bars_1m.open() as fh:
                rows = [json.loads(line) for line in fh if line.strip()][-WINDOW_BARS:]
            for r in rows:
                self.micro.bars.done.append(
                    Bar(
                        int(r["open_ms"]),
                        float(r["o"]),
                        float(r["h"]),
                        float(r["l"]),
                        float(r["c"]),
                        float(r["vol"]),
                        float(r["buy"]),
                        float(r["sell"]),
                        int(r["n"]),
                    )
                )
            self._n_bars_seen = len(self.micro.bars.done)

    def save(self) -> None:
        eng = self.engine
        self.state["position"] = eng.pos.as_dict() if eng and eng.pos else None
        self.state["armed"] = eng.armed if eng else self.state.get("armed", {})
        self.state["micro_history"] = {k: list(h.vals) for k, h in self.micro.hist.items()}
        self.state["oi"] = self.oi[-4320:]
        self.state["funding"] = self.funding[-200:]
        tmp = self.paths.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(_clean(self.state), sort_keys=True, default=str))
        tmp.replace(self.paths.state)

    def _on_bar(self, b: Bar) -> None:
        with self.paths.bars_1m.open("a") as fh:
            fh.write(json.dumps(b.as_dict(), sort_keys=True) + "\n")

    # ------------------------------------------------------------------ events
    def on_event(self, ev: dict[str, Any]) -> None:
        self.micro.on_event(ev)
        self.state["last_event_ms"] = ev.get("rx_ms")
        if ev["topic"].startswith("tickers") and isinstance(ev.get("data"), dict):
            d = ev["data"]
            ts = int(ev.get("ts") or ev.get("rx_ms") or 0)
            if d.get("openInterest") not in (None, ""):
                self.oi.append((ts, float(d["openInterest"])))
            if d.get("fundingRate") not in (None, ""):
                self.funding.append((ts, float(d["fundingRate"])))

    # ------------------------------------------------------------------ tick
    def tick(self, now_ms: int) -> dict[str, Any] | None:
        self.micro.compute(now_ms)  # keeps microstructure histories and completes bars (PIT)
        n = len(self.micro.bars.done)
        if n == self._n_bars_seen or n < MIN_BARS:
            return None
        self._n_bars_seen = n
        bars = list(self.micro.bars.done)[-WINDOW_BARS:]
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
                pos.entry_ms = t_close  # live: the position starts after the decision bar
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

    def _micro_arrays(self, n: int, i: int, t_close: int) -> dict[str, np.ndarray]:
        """Microstructure for the LAST row only (forward-only group; earlier rows NaN)."""
        f = self.micro.compute(t_close)
        out: dict[str, np.ndarray] = {}
        for k in (
            "imb_10",
            "imb_30",
            "imb_60",
            "delta_30",
            "intensity_pct_10",
            "depth_imb",
            "bid_repl",
            "ask_repl",
            "spread_bps",
            "liq_burst_pct_120",
            "liq_imb_120",
            "efficient_30",
            "absorption_30",
        ):
            arr = np.full(n, np.nan)
            arr[i] = (
                float(f.get(k, math.nan))
                if k not in ("efficient_30", "absorption_30")
                else float(f.get(k.replace("_30", ""), math.nan))
            )
            out[k] = arr
        self.last_micro = {k: float(out[k][i]) for k in out}
        return out

    def _restore_engine(self) -> None:
        eng = self.engine
        assert eng is not None
        st = self.state
        eng.counters.update(st.get("counters") or {})
        eng.armed = dict(st.get("armed") or {})
        p = st.get("position")
        if p:
            try:
                eng.pos = Position(
                    **{k: v for k, v in p.items() if k in Position.__dataclass_fields__}
                )
                eng._dist = abs(eng.pos.entry_ref - eng.pos.stop0)
                eng.pos_decision = {}
            except TypeError:
                eng.pos = None

    # ------------------------------------------------------------------ journals
    def _journal_row(
        self,
        eng: V11Engine,
        i: int,
        t_close: int,
        rec: dict[str, Any] | None,
        pos_before: Position | None,
    ) -> None:
        th = self.cfg.thresholds
        for s, tag in ((1, "long"), (-1, "short")):
            sc = eng._score(s, i)
            if math.isfinite(sc) and sc >= th.candidate and t_close > self.observation_start_ms:
                last = self._cand_last.get(tag, -(10**18))
                if t_close - last >= self.cfg.outcomes.candidate_dedupe_min * MS_1M:
                    self._cand_last[tag] = t_close
                    self.state["counters"]["candidates"] += 1
                    self.j_cand.append(
                        "CANDIDATE",
                        {
                            "ts": _iso(t_close),
                            "ts_ms": t_close,
                            "side": tag.upper(),
                            "score": sc,
                            "contrib": eng._contrib(s, i),
                            "micro": getattr(self, "last_micro", {}),
                            "indicators": eng.snapshot(i),
                        },
                    )
                    p0 = float(eng.X["close"][i])
                    if math.isfinite(p0):
                        self._queue_outcome(
                            f"V11C-{t_close}-{tag[0].upper()}", "CANDIDATE", t_close, p0, s
                        )
        for key, arm in eng.armed.items():
            if int(arm["armed_ms"]) == t_close:
                self.j_ep.append(
                    "ARMED",
                    {
                        "ts": _iso(t_close),
                        "ts_ms": t_close,
                        "family": key[0],
                        "side": "LONG" if key.endswith("+1") else "SHORT",
                        "score": arm["score"],
                        "valid_until": _iso(arm["until_ms"]),
                    },
                )
                self.state["counters"]["armed"] += 1
        # near-miss: a trigger that failed only the entry score (armed, score within 10 points below entry)
        for fam in ("A", "B", "C"):
            for s in (1, -1):
                key = f"{fam}{s:+d}"
                arm_nm = eng.armed.get(key)
                sc = eng._score(s, i)
                if (
                    arm_nm is not None
                    and eng.trigger(fam, s, i)
                    and math.isfinite(sc)
                    and th.entry - 10 <= sc < th.entry
                    and (rec is None or rec.get("signal_id") is None)
                ):
                    self.state["counters"]["nearmiss"] += 1
                    self.j_nm.append(
                        "NEAR_MISS",
                        {
                            "ts": _iso(t_close),
                            "ts_ms": t_close,
                            "family": fam,
                            "side": "LONG" if s > 0 else "SHORT",
                            "score": sc,
                            "entry_threshold": th.entry,
                            "contrib": eng._contrib(s, i),
                            "micro": getattr(self, "last_micro", {}),
                        },
                    )
                    self._queue_outcome(
                        f"V11N-{t_close}-{fam}{'L' if s > 0 else 'S'}",
                        "NEAR_MISS",
                        t_close,
                        float(eng.X["close"][i]),
                        s,
                    )
        if rec is not None:
            self.state["counters"]["executed" if rec["action"] == "EXECUTED" else "blocked"] += 1
            if "POSITION_OPEN" in (rec.get("blockers") or []):
                self.state["counters"]["skipped_while_open"] += 1
            self.j_sig.append(
                "SIGNAL_" + rec["action"],
                {
                    **rec,
                    "ts": _iso(rec["decision_ms"]),
                    "execution_mode": "SHADOW",
                    "micro": getattr(self, "last_micro", {}),
                },
            )
            self.state["last_signal"] = {
                "signal_id": rec["signal_id"],
                "action": rec["action"],
                "family": rec["family"],
                "side": rec["side"],
                "at": _iso(rec["decision_ms"]),
            }
            self._queue_outcome(
                rec["signal_id"],
                rec["action"],
                int(rec["decision_ms"]),
                float(rec["entry_ref"]),
                int(rec["side_sign"]),
            )

    def _queue_outcome(self, rid: str, kind: str, t_ms: int, p0: float, side: int) -> None:
        if math.isfinite(p0):
            self.state["pending_outcomes"].append(
                {"id": rid, "kind": kind, "t_ms": t_ms, "p0": p0, "side": side, "done": []}
            )

    def _outcomes(self, now_ms: int) -> None:
        pend = self.state.get("pending_outcomes") or []
        if not pend:
            return
        bars = list(self.micro.bars.done)
        keep = []
        for po in pend:
            for h in self.cfg.outcomes.horizons_min:
                if h in po["done"]:
                    continue
                end = po["t_ms"] + h * MS_1M
                if now_ms < end + 65_000:
                    continue
                win = [b for b in bars if po["t_ms"] <= b.open_ms + MS_1M and b.open_ms < end]
                if win:
                    s = float(po["side"])
                    p0 = float(po["p0"])
                    hi, lo, c = max(b.h for b in win), min(b.lo for b in win), win[-1].c
                    self.j_out.append(
                        "OUTCOME",
                        {
                            "id": po["id"],
                            "kind": po["kind"],
                            "horizon_min": h,
                            "t_ms": po["t_ms"],
                            "return_bps": s * (c / p0 - 1) * 1e4,
                            "mfe_bps": (hi / p0 - 1) * 1e4 if s > 0 else (1 - lo / p0) * 1e4,
                            "mae_bps": (lo / p0 - 1) * 1e4 if s > 0 else (1 - hi / p0) * 1e4,
                            "max_up_bps": (hi / p0 - 1) * 1e4,
                            "max_down_bps": (lo / p0 - 1) * 1e4,
                        },
                    )
                po["done"].append(h)
            if len(po["done"]) < len(self.cfg.outcomes.horizons_min):
                keep.append(po)
        self.state["pending_outcomes"] = keep[-5000:]

    def diagnostic(self) -> dict[str, Any]:
        eng = self.engine
        return {
            "strategy": STRATEGY,
            "mode": "SHADOW_ONLY",
            "written_at": datetime.now(UTC).isoformat(),
            "written_ms": int(time.time() * 1000),
            "last_bar": _iso(self.state.get("last_bar_ms")),
            "bars": len(self.micro.bars.done),
            "scores": self.last_scores,
            "indicators": self.last_features,
            "micro": getattr(self, "last_micro", {}),
            "micro_history": self.micro.history_state(),
            "armed": eng.armed if eng else {},
            "position": eng.pos.as_dict() if eng and eng.pos else None,
            "counters": self.state["counters"],
            "groups": list(GROUPS),
        }
