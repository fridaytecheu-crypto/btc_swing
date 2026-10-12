"""V10 public WebSocket feed: its own connection, its own immutable hourly raw store and an in-process
event callback (the sniper must react within the second; it never tails files). No authentication."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.collector import CollectorStats
from btc_swing.v5.config import COLLECTOR_SCHEMA_VERSION, CollectorCfg

log = logging.getLogger(__name__)
Event = dict[str, Any]


class V10RawStore:
    """Append-only hourly JSONL under <root>/<YYYY-MM-DD>/<HH>.jsonl, one line per message with the
    sha256 of the raw payload; verifiable by re-hashing."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self._fh: Any = None
        self._key = ""

    def _file_for(self, ts_ms: int) -> Path:
        d = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        p = self.root / d.strftime("%Y-%m-%d")
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{d.strftime('%H')}.jsonl"

    def append(self, row: dict[str, Any]) -> int:
        path = self._file_for(int(row["ts_received_ms"]))
        if str(path) != self._key:
            self.close()
            self._fh = path.open("a", encoding="utf-8")
            self._key = str(path)
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
            self._key = ""

    def latest_mtime(self) -> float | None:
        files = sorted(self.root.glob("*/*.jsonl*"), key=lambda p: p.stat().st_mtime)
        return files[-1].stat().st_mtime if files else None

    def save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def verify(self) -> dict[str, Any]:
        files = sorted(p for p in self.root.glob("*/*.jsonl*") if p.suffix in (".jsonl", ".gz"))
        n = bad = 0
        by_channel: dict[str, int] = {}
        for f in files:
            for line in _read_text(f).splitlines():
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


def _read_text(f: Path) -> str:
    if f.suffix == ".gz":
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return str(fh.read())
    return f.read_text(encoding="utf-8")


def parse_raw_line(line: str) -> Event | None:
    """A stored raw row -> the same event dict the live callback receives (replay = live)."""
    try:
        r = json.loads(line)
        d = json.loads(r["raw"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if "topic" not in d:
        return None
    return {
        "topic": str(d["topic"]),
        "type": d.get("type"),
        "ts": d.get("ts"),
        "data": d.get("data"),
        "rx_ms": int(r["ts_received_ms"]),
    }


class V10Collector:
    """Reconnect with backoff, heartbeat, duplicate suppression, order-book `u` continuity, latency
    statistics, append-only storage, and an `on_event` callback with the parsed message."""

    def __init__(
        self, cfg: CollectorCfg, raw_root: Path, on_event: Callable[[Event], None] | None = None
    ) -> None:
        self.cfg = cfg
        self.store = V10RawStore(raw_root)
        self.stats = CollectorStats()
        self.on_event = on_event
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._last_u: dict[str, int] = {}
        self._stop = asyncio.Event()
        self.connected = False

    def _key(self, d: dict[str, Any]) -> str:
        topic = d.get("topic", "")
        data = d.get("data")
        uid = ""
        if isinstance(data, dict):
            uid = str(data.get("u", data.get("seq", "")))
        elif isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            uid = str(first.get("i", first.get("start", first.get("T", ""))))
            if len(data) > 1:
                uid += f"|{len(data)}"
        return f"{topic}|{d.get('ts', '')}|{uid}"

    def _is_duplicate(self, key: str) -> bool:
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > self.cfg.dedupe_cache_size:
            self._seen.popitem(last=False)
        return False

    def _check_sequence(self, d: dict[str, Any]) -> bool:
        """True when an order-book delta broke `u` continuity (the book owner must invalidate)."""
        topic = str(d.get("topic", ""))
        if not topic.startswith("orderbook."):
            return False
        data = d.get("data") or {}
        u = data.get("u")
        if u is None:
            return False
        if d.get("type") == "snapshot":
            self._last_u[topic] = int(u)
            return False
        self.stats.orderbook_deltas += 1
        last = self._last_u.get(topic)
        gap = last is not None and int(u) != last + 1
        if gap:
            self.stats.sequence_gaps += 1
        self._last_u[topic] = int(u)
        return gap

    def on_message(self, raw: str, now_ms: int | None = None) -> Event | None:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            self.stats.errors += 1
            return None
        topic = str(d.get("topic") or d.get("op") or "unknown")
        self.stats.messages += 1
        self.stats.by_topic[topic] = self.stats.by_topic.get(topic, 0) + 1
        self.stats.last_message_at = time.time()
        if "topic" not in d:
            return None
        if self._is_duplicate(self._key(d)):
            self.stats.duplicates += 1
            return None
        gap = self._check_sequence(d)
        ts_ex = d.get("ts")
        if isinstance(ts_ex, int | float):
            self.stats.latency_ms.append(float(now - ts_ex))
        self.stats.bytes_written += self.store.append(
            {
                "ts_received_ms": now,
                "ts_exchange_ms": ts_ex,
                "symbol": self.cfg.symbol,
                "channel": topic,
                "type": d.get("type"),
                "schema_version": COLLECTOR_SCHEMA_VERSION,
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "raw": raw,
            }
        )
        if self.stats.messages % 2000 == 0:
            self.store.flush()
            self._save()
        ev: Event = {
            "topic": topic,
            "type": d.get("type"),
            "ts": ts_ex,
            "data": d.get("data"),
            "rx_ms": now,
            "seq_gap": gap,
        }
        if self.on_event is not None:
            self.on_event(ev)
        return ev

    def _save(self) -> None:
        self.store.save_state(
            {
                "last_orderbook_u": self._last_u,
                "stats": self.stats.as_dict(),
                "schema_version": COLLECTOR_SCHEMA_VERSION,
                "saved_at": datetime.now(UTC).isoformat(),
            }
        )

    def stop(self) -> None:
        self._stop.set()

    async def run(self, duration_seconds: float | None = None) -> CollectorStats:
        import websockets

        deadline = time.time() + duration_seconds if duration_seconds else None
        backoff = list(self.cfg.reconnect_backoff_seconds)
        attempt = 0
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
                    self.connected = True
                    log.info("V10 feed subscribed to %s", self.cfg.topics)
                    while not self._stop.is_set() and (deadline is None or time.time() < deadline):
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=self.cfg.stale_seconds)
                        except TimeoutError:
                            self.stats.stale_timeouts += 1
                            raise ConnectionResetError("stale feed") from None
                        self.on_message(msg if isinstance(msg, str) else msg.decode("utf-8"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.stats.errors += 1
                if self._stop.is_set() or (deadline is not None and time.time() >= deadline):
                    break
                wait = backoff[min(attempt, len(backoff) - 1)]
                attempt += 1
                self.stats.reconnects += 1
                log.warning(
                    "V10 feed lost (%s: %s); reconnect in %ss", type(e).__name__, str(e)[:120], wait
                )
                self.store.flush()
                self._save()
                await asyncio.sleep(wait)
        self.connected = False
        self.store.flush()
        self._save()
        self.store.close()
        return self.stats
