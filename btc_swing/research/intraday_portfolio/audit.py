"""E4 collection audit: what the CURRENT Bybit public collectors preserve, read-only, from (a) the
frozen V10 feed source text and config (never imported, never modified), (b) the V5 collector source
text and (c) the raw store files that exist locally. Every status is PRESERVED / DERIVABLE / PARTIAL /
MISSING with the evidence that produced it; the proposed change is a text proposal only."""

from __future__ import annotations

import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED: tuple[tuple[str, str], ...] = (
    ("exchange_event_time", "per-message exchange timestamp (and per-trade / per-liquidation `T`)"),
    ("local_utc_receipt_time", "local wall-clock receipt time in UTC ms"),
    ("monotonic_receipt_time", "a monotonic receipt clock immune to wall-clock steps"),
    ("connection_reconnect_ids", "a connection / reconnect identifier on every row"),
    ("gap_clock_health_telemetry", "per-row sequence-gap flag and periodic clock-health rows"),
    ("bybit_allLiquidation_raw", "raw allLiquidation messages"),
    ("bybit_public_trades", "raw publicTrade messages with aggressor side"),
    ("bybit_bbo", "best bid / ask (orderbook.1 or derivable from a depth book)"),
    ("bybit_depth_snapshots_deltas", "orderbook.50 snapshot + delta stream"),
    ("bybit_sequence_update_fields", "orderbook `u` / `seq` / `cts` kept verbatim"),
    ("binance_perp_trades_bbo", "Binance USDT-perp trades / BBO feed"),
    ("spot_venue_feeds_two_independent", "at least two independent spot venue feeds"),
    ("mark_index_price", "mark and index price stream"),
    ("open_interest", "open interest stream"),
    ("funding_estimate", "current estimated funding rate"),
    ("funding_actual_settlement", "actual funding settlement events"),
    ("next_funding_time", "next funding time"),
    ("contract_metadata", "instrument info (tick size, lot size, contract status)"),
    ("raw_units", "raw message string kept verbatim (units untouched)"),
    ("schema_version", "schema version on every row"),
    ("timestamp_units", "timestamp unit declared (ms)"),
    ("aggressor_semantics", "documented meaning of `S` on trades and liquidations"),
)

TICKER_FIELDS = (
    "markPrice",
    "indexPrice",
    "openInterest",
    "fundingRate",
    "nextFundingTime",
    "bid1Price",
    "ask1Price",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _scan_store(root: Path, max_lines: int = 20000) -> dict[str, Any]:
    """Keys, topics and ticker fields actually present in the first raw hour file of a store."""
    files = sorted(p for p in root.glob("*/*.jsonl*") if p.suffix in (".jsonl", ".gz"))
    if not files:
        return {"files": 0}
    f = files[0]
    if f.suffix == ".gz":
        with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = f.read_text()
    keys: set[str] = set()
    topics: set[str] = set()
    tick: set[str] = set()
    n = 0
    rx_prev = None
    rx_monotone = True
    for line in text.splitlines()[:max_lines]:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        keys |= set(r.keys())
        rx = r.get("ts_received_ms")
        if isinstance(rx, int):
            if rx_prev is not None and rx < rx_prev:
                rx_monotone = False
            rx_prev = rx
        try:
            d = json.loads(r.get("raw", "{}"))
        except json.JSONDecodeError:
            continue
        t = str(d.get("topic", ""))
        topics.add(t.split(".")[0] + ("." + t.split(".")[1] if t.startswith("orderbook") else ""))
        if t.startswith("tickers"):
            tick |= {k for k in TICKER_FIELDS if k in (d.get("data") or {})}
    return {
        "files": len(files),
        "sample_file": str(f),
        "rows_scanned": n,
        "row_keys": sorted(keys),
        "topics": sorted(topics),
        "ticker_fields_seen": sorted(tick),
        "receipt_time_non_decreasing_in_sample": rx_monotone,
    }


def e4_collection_audit(data_dir: Path, repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or _repo_root()
    v10_cfg = yaml.safe_load(_read(root / "config" / "btc_swing_v10.yaml")) or {}
    v10_topics = [str(t) for t in (v10_cfg.get("collector") or {}).get("topics", [])]
    feed_src = _read(root / "btc_swing" / "v10" / "feed.py")
    v5_src = _read(root / "btc_swing" / "v5" / "collector.py")
    design = _read(root / "docs" / "BTC_V10_LIVE_MICROSTRUCTURE_DESIGN.md")
    stores = {
        rel: _scan_store(data_dir / "btc" / rel)
        for rel in ("forward_v10_verify/raw/BTCUSDT", "forward/bybit/BTCUSDT")
        if (data_dir / "btc" / rel).is_dir()
    }
    row_keys_src = set(
        re.findall(
            r'"(ts_received_ms|ts_exchange_ms|symbol|channel|type|schema_version|sha256|raw)"',
            feed_src,
        )
    )
    has = {
        t.split(".")[0] + ("." + t.split(".")[1] if t.startswith("orderbook") else "")
        for t in v10_topics
    }
    for (
        s_
    ) in stores.values():  # topics observed in the local V10-format stores corroborate the config
        has |= set(s_.get("topics", []))
    v5_has_ob1 = "orderbook.1" in v5_src
    seen_tick: set[str] = set()
    for s in stores.values():
        seen_tick |= set(s.get("ticker_fields_seen", []))

    def st(status: str, evidence: str) -> dict[str, str]:
        return {"status": status, "evidence": evidence}

    fields: dict[str, dict[str, str]] = {
        "exchange_event_time": st(
            "PRESERVED" if "ts_exchange_ms" in row_keys_src else "MISSING",
            "row.ts_exchange_ms = message `ts`; per-event `T` kept inside `raw`",
        ),
        "local_utc_receipt_time": st(
            "PRESERVED" if "ts_received_ms" in row_keys_src else "MISSING",
            "row.ts_received_ms = int(time.time()*1000) at receipt (wall clock, UTC epoch ms)",
        ),
        "monotonic_receipt_time": st(
            "PRESERVED" if "monotonic" in feed_src else "MISSING",
            "no time.monotonic()/perf_counter field on the row; wall clock only",
        ),
        "connection_reconnect_ids": st(
            "PRESERVED"
            if re.search(r'"(conn_id|connection_id|session_id)"', feed_src)
            else "MISSING",
            "reconnects are only counted in state.json stats; no per-row connection id",
        ),
        "gap_clock_health_telemetry": st(
            "PARTIAL",
            "sequence gaps counted + latency stats in state.json (every 2000 messages); the per-event "
            "seq_gap flag is in-memory only, never written to the raw row; no clock-sync telemetry",
        ),
        "bybit_allLiquidation_raw": st(
            "PRESERVED" if "allLiquidation" in has else "MISSING", f"V10 topics {v10_topics}"
        ),
        "bybit_public_trades": st(
            "PRESERVED" if "publicTrade" in has else "MISSING", f"V10 topics {v10_topics}"
        ),
        "bybit_bbo": st(
            "PRESERVED"
            if "orderbook.1" in has
            else ("DERIVABLE" if "orderbook.50" in has else "MISSING"),
            "V10 subscribes orderbook.50 only (BBO = top of the maintained book; V5 collector had orderbook.1: "
            f"{v5_has_ob1})",
        ),
        "bybit_depth_snapshots_deltas": st(
            "PRESERVED" if "orderbook.50" in has else "MISSING", "orderbook.50 snapshot + delta"
        ),
        "bybit_sequence_update_fields": st(
            "PRESERVED", "`u`, `seq`, `cts` stay inside the verbatim `raw` string"
        ),
        "binance_perp_trades_bbo": st("MISSING", "no Binance feed in any collector"),
        "spot_venue_feeds_two_independent": st("MISSING", "no spot venue feed configured (0 of 2)"),
        "mark_index_price": st(
            "PARTIAL" if {"markPrice", "indexPrice"} & seen_tick or "tickers" in has else "MISSING",
            "tickers delta carries markPrice / indexPrice only when they change; no dedicated stream",
        ),
        "open_interest": st(
            "PARTIAL" if "tickers" in has else "MISSING",
            "tickers delta carries openInterest when it changes (seen: %s)"
            % ("openInterest" in seen_tick),
        ),
        "funding_estimate": st(
            "PARTIAL" if "tickers" in has else "MISSING",
            "tickers delta carries fundingRate when it changes (seen: %s)"
            % ("fundingRate" in seen_tick),
        ),
        "funding_actual_settlement": st(
            "MISSING", "no funding-settlement / execution feed; no REST funding history journal"
        ),
        "next_funding_time": st(
            "PARTIAL" if "tickers" in has else "MISSING",
            "tickers delta carries nextFundingTime when it changes (seen: %s)"
            % ("nextFundingTime" in seen_tick),
        ),
        "contract_metadata": st(
            "MISSING", "instrument-info (tick / lot size, status) is never journaled"
        ),
        "raw_units": st(
            "PRESERVED" if "raw" in row_keys_src else "MISSING",
            "the full message string is stored verbatim with its sha256",
        ),
        "schema_version": st(
            "PRESERVED" if "schema_version" in row_keys_src else "MISSING",
            "bybit-public-raw-1 on every row",
        ),
        "timestamp_units": st(
            "PRESERVED",
            "field names carry the unit (ts_received_ms / ts_exchange_ms); Bybit `ts`/`T` are ms",
        ),
        "aggressor_semantics": st(
            "PRESERVED"
            if "LONG position was liquidated" in design or "long liquidated" in design
            else "PARTIAL",
            "publicTrade `S` = taker side; allLiquidation `S=Buy` = a LONG was liquidated (V10 design section 1, verified empirically)",
        ),
    }
    gaps = [k for k, v in fields.items() if v["status"] in ("MISSING", "PARTIAL")]
    return {
        "schema": "e4_collection_audit-1",
        "generated_at_utc": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "scope": "read-only audit of collector source text, config and local raw stores; nothing modified",
        "v10_feed_topics": v10_topics,
        "v10_row_keys_in_source": sorted(row_keys_src),
        "local_stores": stores,
        "fields": fields,
        "required_fields_with_gaps": gaps,
        "e4_blocking_gaps": [
            "30 complete receipt-timed UTC days of publicTrade + allLiquidation (threshold history) do not exist yet",
            "no monotonic clock / connection id on raw rows (reconnect boundaries cannot be reconstructed per row)",
            "no second venue (Binance perp or any spot feed) for cross-market attribution",
            "no funding settlement / instrument metadata journal",
        ],
        "proposed_minimal_collector_only_change_NOT_IMPLEMENTED": {
            "where": "a NEW module (e.g. btc_swing/research/collector_v2/) with its own raw root; the frozen V10 feed, "
            "its store and every strategy decision stay byte-identical",
            "row_additions": [
                "rx_monotonic_ns",
                "conn_id (uuid4 per WebSocket connection)",
                "conn_seq (per-connection counter)",
                "seq_gap (bool)",
            ],
            "periodic_rows": [
                "clock_health every 60 s: wall vs monotonic drift, last message age, reconnect count"
            ],
            "subscriptions": [
                "orderbook.1.BTCUSDT (explicit BBO) next to orderbook.50",
                "optional: Binance USDT-perp aggTrade + bookTicker, one or two spot venue trade feeds, each in its own raw root",
            ],
            "daily_rest_journal": [
                "Bybit instruments-info (contract metadata)",
                "Bybit funding-rate history (actual settlements)",
                "Bybit open-interest history",
            ],
            "invariants": [
                "schema bybit-public-raw-2 with raw kept verbatim",
                "never run on the cloud container",
                "never imported by V10 / V11 runners",
            ],
            "repo_rule": "requires an explicit owner decision; this audit implements nothing",
        },
    }
