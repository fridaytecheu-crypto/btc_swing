"""Bybit PUBLIC WebSocket collector for forward V5 research. No authentication, no orders.

Persists immutable raw events as hourly JSONL files under
`<data_dir>/btc/forward/bybit/<symbol>/<YYYY-MM-DD>/<HH>.jsonl`, one line per message:
  {"ts_received_ms", "ts_exchange_ms", "symbol", "channel", "type", "schema_version",
   "sha256" (of the raw payload), "raw" (the payload exactly as received)}
plus a state file (`state.json`: last order-book update id, counters) for resumability.
Guarantees: reconnect with backoff, heartbeat (ping + stale timeout), duplicate suppression
(topic + exchange ts + update id/seq), order-book sequence-gap detection (orderbook `u` must
increase by one between deltas; a snapshot resets), latency statistics, append-only storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.config import COLLECTOR_SCHEMA_VERSION, CollectorCfg

log = logging.getLogger(__name__)


@dataclass
class CollectorStats:
    started_at: float = field(default_factory=time.time)
    messages: int = 0
    by_topic: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    sequence_gaps: int = 0
    orderbook_deltas: int = 0
    reconnects: int = 0
    stale_timeouts: int = 0
    errors: int = 0
    bytes_written: int = 0
    latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=50_000))
    last_message_at: float = 0.0

    def latency_quantiles(self) -> dict[str, float]:
        if not self.latency_ms:
            return {}
        xs = sorted(self.latency_ms)

        def q(p: float) -> float:
            return xs[min(len(xs) - 1, int(p * len(xs)))]

        return {"p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": xs[-1], "n": float(len(xs))}

    def as_dict(self) -> dict[str, Any]:
        up = time.time() - self.started_at
        return {
            "uptime_seconds": up,
            "messages": self.messages,
            "messages_per_second": self.messages / up if up > 0 else 0.0,
            "by_topic": dict(self.by_topic),
            "duplicates": self.duplicates,
            "orderbook_deltas": self.orderbook_deltas,
            "sequence_gaps": self.sequence_gaps,
            "sequence_gap_rate": self.sequence_gaps / self.orderbook_deltas
            if self.orderbook_deltas
            else 0.0,
            "reconnects": self.reconnects,
            "stale_timeouts": self.stale_timeouts,
            "errors": self.errors,
            "bytes_written": self.bytes_written,
            "latency_ms": self.latency_quantiles(),
        }


class RawStore:
    """Append-only hourly JSONL files + a small state file. Verifiable by re-hashing payloads."""

    def __init__(self, root: Path, symbol: str) -> None:
        self.root = root / "btc" / "forward" / "bybit" / symbol
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self._fh: Any = None
        self._fh_key = ""

    def _file_for(self, ts_ms: int) -> Path:
        d = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        p = self.root / d.strftime("%Y-%m-%d")
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{d.strftime('%H')}.jsonl"

    def append(self, row: dict[str, Any]) -> int:
        path = self._file_for(int(row["ts_received_ms"]))
        key = str(path)
        if key != self._fh_key:
            if self._fh is not None:
                self._fh.close()
            self._fh = path.open("a", encoding="utf-8")
            self._fh_key = key
        line = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        self._fh.write(line)
        return len(line)

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._fh_key = ""

    def load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return dict(json.loads(self.state_path.read_text()))
        return {}

    def save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def verify(self) -> dict[str, Any]:
        """Re-read every stored line, re-hash payloads and count rows per channel."""
        files = sorted(self.root.glob("*/*.jsonl"))
        n = bad = 0
        by_channel: dict[str, int] = {}
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                n += 1
                try:
                    r = json.loads(line)
                    if hashlib.sha256(r["raw"].encode("utf-8")).hexdigest() != r["sha256"]:
                        bad += 1
                    by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + 1
                except (json.JSONDecodeError, KeyError):
                    bad += 1
        return {"files": len(files), "rows": n, "hash_mismatches": bad, "by_channel": by_channel}


class BybitPublicCollector:
    def __init__(self, cfg: CollectorCfg, data_dir: Path) -> None:
        self.cfg = cfg
        self.store = RawStore(data_dir, cfg.symbol)
        self.stats = CollectorStats()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._last_u: int | None = None
        self._stop = asyncio.Event()
        self._force_reconnect = asyncio.Event()
        st = self.store.load_state()
        self._last_u = st.get("last_orderbook_u")
        self._resumed_from = st

    # ------------------------------------------------------------------ dedupe / sequence
    def _key(self, d: dict[str, Any]) -> str:
        topic = d.get("topic", "")
        data = d.get("data")
        uid = ""
        if isinstance(data, dict):
            uid = str(data.get("u", data.get("seq", "")))
        elif isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            uid = str(first.get("i", first.get("T", "")))
        return f"{topic}|{d.get('ts', '')}|{uid}"

    def _is_duplicate(self, key: str) -> bool:
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > self.cfg.dedupe_cache_size:
            self._seen.popitem(last=False)
        return False

    def _check_sequence(self, d: dict[str, Any]) -> None:
        if not str(d.get("topic", "")).startswith("orderbook."):
            return
        data = d.get("data") or {}
        u = data.get("u")
        if u is None:
            return
        if d.get("type") == "snapshot":
            self._last_u = int(u)
            return
        self.stats.orderbook_deltas += 1
        if self._last_u is not None and int(u) != self._last_u + 1:
            self.stats.sequence_gaps += 1
        self._last_u = int(u)

    # ------------------------------------------------------------------ run
    async def run(
        self, duration_seconds: float | None = None, reconnect_after_seconds: float | None = None
    ) -> CollectorStats:
        import websockets

        deadline = time.time() + duration_seconds if duration_seconds else None
        backoff = list(self.cfg.reconnect_backoff_seconds)
        attempt = 0
        if reconnect_after_seconds:
            asyncio.get_running_loop().call_later(
                reconnect_after_seconds, self._force_reconnect.set
            )
        while not self._stop.is_set() and (deadline is None or time.time() < deadline):
            try:
                async with websockets.connect(
                    self.cfg.url,
                    open_timeout=20,
                    ping_interval=self.cfg.ping_seconds,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    attempt = 0
                    await ws.send(json.dumps({"op": "subscribe", "args": list(self.cfg.topics)}))
                    log.info("subscribed to %s", self.cfg.topics)
                    while not self._stop.is_set() and (deadline is None or time.time() < deadline):
                        if self._force_reconnect.is_set():
                            self._force_reconnect.clear()
                            log.info("forced reconnect (test)")
                            raise ConnectionResetError("forced reconnect")
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=self.cfg.stale_seconds)
                        except TimeoutError:
                            self.stats.stale_timeouts += 1
                            raise ConnectionResetError(
                                "stale: no message within stale_seconds"
                            ) from None
                        self._on_message(msg if isinstance(msg, str) else msg.decode("utf-8"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stats.errors += 1
                if self._stop.is_set() or (deadline is not None and time.time() >= deadline):
                    break
                wait = backoff[min(attempt, len(backoff) - 1)]
                attempt += 1
                self.stats.reconnects += 1
                log.warning(
                    "connection lost (%s: %s); reconnecting in %.0fs",
                    type(e).__name__,
                    str(e)[:120],
                    wait,
                )
                self.store.flush()
                self._save()
                await asyncio.sleep(wait)
        self.store.flush()
        self._save()
        self.store.close()
        return self.stats

    def stop(self) -> None:
        self._stop.set()

    def _save(self) -> None:
        self.store.save_state(
            {
                "last_orderbook_u": self._last_u,
                "stats": self.stats.as_dict(),
                "schema_version": COLLECTOR_SCHEMA_VERSION,
                "saved_at": datetime.now(UTC).isoformat(),
            }
        )

    def _on_message(self, raw: str) -> None:
        now_ms = int(time.time() * 1000)
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            self.stats.errors += 1
            return
        topic = str(d.get("topic") or d.get("op") or "unknown")
        self.stats.messages += 1
        self.stats.by_topic[topic] = self.stats.by_topic.get(topic, 0) + 1
        self.stats.last_message_at = time.time()
        if "topic" not in d:
            return  # subscription acks / pong are not market data
        key = self._key(d)
        if self._is_duplicate(key):
            self.stats.duplicates += 1
            return
        self._check_sequence(d)
        ts_ex = d.get("ts")
        if isinstance(ts_ex, int | float):
            self.stats.latency_ms.append(float(now_ms - ts_ex))
        row = {
            "ts_received_ms": now_ms,
            "ts_exchange_ms": ts_ex,
            "symbol": self.cfg.symbol,
            "channel": topic,
            "type": d.get("type"),
            "schema_version": COLLECTOR_SCHEMA_VERSION,
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "raw": raw,
        }
        self.stats.bytes_written += self.store.append(row)
        if self.stats.messages % 2000 == 0:
            self.store.flush()
            self._save()


def run_collector_test(
    cfg: CollectorCfg,
    data_dir: Path,
    duration_seconds: float,
    reconnect_after_seconds: float | None,
) -> dict[str, Any]:
    """Bounded verification run: uptime, message counts, gap rate, forced reconnect, storage check."""
    col = BybitPublicCollector(cfg, data_dir)
    stats = asyncio.run(
        col.run(duration_seconds=duration_seconds, reconnect_after_seconds=reconnect_after_seconds)
    )
    out = stats.as_dict()
    out["storage"] = col.store.verify()
    out["resumed_from_state"] = bool(col._resumed_from)
    out["forced_reconnect_requested"] = reconnect_after_seconds is not None
    return out
