"""Bybit PUBLIC historical market data for V5.1 warm-up seeds (no authentication, no secrets).

Endpoints (https://api.bybit.com, GET, public):
- /v5/market/open-interest  category=linear symbol=BTCUSDT intervalTime=5min (cursor pagination,
  limit 200). `openInterest` is in base coin (BTC) for USDT perpetuals; `timestamp` (ms) is the
  5-minute boundary the value is sampled at, i.e. the close time of our 5m bar: the live row's
  `oi_last` is the last ticker open interest received before that same boundary.
- /v5/market/mark-price-kline and /v5/market/index-price-kline interval=5 (limit 1000, time-window
  pagination). Their closes at a 5m boundary are the last mark / index price of that bar, which is
  exactly how the live row defines `mark_close` / `index_close`; premium = mark_close/index_close-1.
- /v5/market/funding/history (limit 200): the settled funding rate at `fundingRateTimestamp`,
  exactly the (time, rate) event the live pipeline derives from the ticker's funding transition.

Every page is persisted verbatim (immutable JSON with request params, retrieved_at and sha256) and
every table row carries `source`, `retrieved_at` and `page_sha256`. Seed tables are WARM-UP ONLY:
`split_warmup` keeps rows strictly before the V5 observation start for the features and the rest
only for alignment verification against the live collector rows. Nothing is backfilled into the
observation period and no signal or trade is ever derived from seed rows."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

BYBIT_PUBLIC_BASE = "https://api.bybit.com"
PUBLIC_PATHS = (
    "/v5/market/open-interest",
    "/v5/market/mark-price-kline",
    "/v5/market/index-price-kline",
    "/v5/market/funding/history",
    "/v5/market/time",
)
MS_5M = 300_000
DAY_MS = 86_400_000


class HistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Page:
    path: str
    params: dict[str, Any]
    body: dict[str, Any]
    retrieved_at: str
    sha256: str
    http_status: int


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class BybitPublicHistory:
    """GET-only client for the public market-history endpoints (never authenticated)."""

    def __init__(
        self,
        base: str = BYBIT_PUBLIC_BASE,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 20.0,
        page_pause_s: float = 0.12,
    ) -> None:
        if not base.startswith("https://"):
            raise HistoryError("public history base must be https")
        self.base = base.rstrip("/")
        self._http = httpx.Client(base_url=self.base, timeout=timeout_s, transport=transport)
        self._sleep = sleep
        self.page_pause_s = page_pause_s
        self.requests = 0

    def close(self) -> None:
        self._http.close()

    def get(self, path: str, params: dict[str, Any]) -> Page:
        if path not in PUBLIC_PATHS:
            raise HistoryError(f"path not allowed: {path}")
        q = {k: v for k, v in params.items() if v is not None}
        last: Exception | None = None
        for attempt in range(4):
            try:
                r = self._http.get(path, params=q)
                self.requests += 1
                raw = r.content
                body = json.loads(raw or b"{}")
                if r.status_code != 200:
                    raise HistoryError(f"HTTP {r.status_code} for {path}: {raw[:200]!r}")
                if int(body.get("retCode", -1)) == 10006:  # rate limit
                    raise HistoryError("rate limited")
                if int(body.get("retCode", -1)) != 0:
                    raise HistoryError(f"retCode {body.get('retCode')}: {body.get('retMsg')}")
                return Page(
                    path,
                    q,
                    body,
                    datetime.now(UTC).isoformat(),
                    _sha(raw),
                    r.status_code,
                )
            except (httpx.HTTPError, HistoryError, ValueError) as e:
                last = e
                if isinstance(e, HistoryError) and "retCode" in str(e):
                    raise
                self._sleep(0.5 * 2**attempt)
        raise HistoryError(f"{path} failed after retries: {last}")

    # ------------------------------------------------------------------ paginators
    def open_interest_pages(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        interval: str = "5min",
        category: str = "linear",
    ) -> Iterator[Page]:
        cursor: str | None = None
        for _ in range(10_000):
            page = self.get(
                "/v5/market/open-interest",
                {
                    "category": category,
                    "symbol": symbol,
                    "intervalTime": interval,
                    "startTime": int(start_ms),
                    "endTime": int(end_ms),
                    "limit": 200,
                    "cursor": cursor,
                },
            )
            rows = page.body.get("result", {}).get("list") or []
            yield page
            cursor = page.body.get("result", {}).get("nextPageCursor") or None
            if not rows or not cursor:
                return
            oldest = min(int(r["timestamp"]) for r in rows)
            if oldest <= start_ms:
                return
            self._sleep(self.page_pause_s)

    def kline_pages(
        self, kind: str, symbol: str, start_ms: int, end_ms: int, interval: str = "5"
    ) -> Iterator[Page]:
        if kind not in ("mark-price-kline", "index-price-kline"):
            raise HistoryError(f"unknown kline kind {kind}")
        end = int(end_ms)
        for _ in range(10_000):
            page = self.get(
                f"/v5/market/{kind}",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "interval": interval,
                    "start": int(start_ms),
                    "end": end,
                    "limit": 1000,
                },
            )
            rows = page.body.get("result", {}).get("list") or []
            yield page
            if not rows:
                return
            oldest = min(int(r[0]) for r in rows)
            if oldest <= start_ms:
                return
            end = oldest - 1
            self._sleep(self.page_pause_s)

    def funding_pages(self, symbol: str, start_ms: int, end_ms: int) -> Iterator[Page]:
        end = int(end_ms)
        for _ in range(10_000):
            page = self.get(
                "/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "startTime": int(start_ms),
                    "endTime": end,
                    "limit": 200,
                },
            )
            rows = page.body.get("result", {}).get("list") or []
            yield page
            if not rows:
                return
            oldest = min(int(r["fundingRateTimestamp"]) for r in rows)
            if oldest <= start_ms:
                return
            end = oldest - 1
            self._sleep(self.page_pause_s)


# ----------------------------------------------------------------------------- persistence
class SeedStore:
    """Immutable raw pages + a manifest + one merged table per kind."""

    def __init__(self, root: Path, kind: str) -> None:
        self.root = root
        self.kind = kind
        self.raw = root / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.manifest = root / "manifest.jsonl"
        self.table = root / f"{kind}.parquet"
        self.verification = root / "verification.json"

    def write_page(self, page: Page, n_rows: int) -> Path:
        stamp = page.retrieved_at.replace(":", "").replace("-", "")[:15]
        p = self.raw / f"{self.kind}_{stamp}_{page.sha256[:12]}.json"
        if p.exists():  # same bytes, same name: immutable
            return p
        p.write_text(
            json.dumps(
                {
                    "path": page.path,
                    "params": page.params,
                    "retrieved_at": page.retrieved_at,
                    "sha256": page.sha256,
                    "http_status": page.http_status,
                    "body": page.body,
                },
                indent=1,
                sort_keys=True,
            )
        )
        with self.manifest.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "kind": self.kind,
                        "file": p.name,
                        "sha256": page.sha256,
                        "params": page.params,
                        "retrieved_at": page.retrieved_at,
                        "rows": n_rows,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        return p

    def load_table(self) -> pl.DataFrame:
        return pl.read_parquet(self.table) if self.table.exists() else pl.DataFrame()

    def merge_table(self, new: pl.DataFrame, key: str) -> pl.DataFrame:
        """Rows already present (same key) are kept as first retrieved; new rows are appended."""
        old = self.load_table()
        if old.is_empty():
            out = new.sort(key)
        else:
            out = (
                pl.concat([old, new], how="vertical_relaxed")
                .unique(subset=[key], keep="first", maintain_order=True)
                .sort(key)
            )
        out.write_parquet(self.table)
        return out


# ----------------------------------------------------------------------------- row extraction
def oi_rows(page: Page) -> list[dict[str, Any]]:
    src = "bybit_rest_open-interest_" + str(page.params.get("intervalTime"))
    return [
        {
            "time_ms": int(r["timestamp"]),
            "open_interest": float(r["openInterest"]),
            "source": src,
            "retrieved_at": page.retrieved_at,
            "page_sha256": page.sha256,
        }
        for r in page.body.get("result", {}).get("list") or []
    ]


def kline_rows(page: Page, field_name: str) -> list[dict[str, Any]]:
    return [
        {
            "open_time_ms": int(r[0]),
            field_name: float(r[4]),
            "retrieved_at": page.retrieved_at,
            "page_sha256": page.sha256,
        }
        for r in page.body.get("result", {}).get("list") or []
    ]


def funding_rows(page: Page) -> list[dict[str, Any]]:
    return [
        {
            "time_ms": int(r["fundingRateTimestamp"]),
            "funding_rate": float(r["fundingRate"]),
            "source": "bybit_rest_funding_history",
            "retrieved_at": page.retrieved_at,
            "page_sha256": page.sha256,
        }
        for r in page.body.get("result", {}).get("list") or []
    ]


# ----------------------------------------------------------------------------- fetchers
def fetch_oi_seed(
    client: BybitPublicHistory,
    store: SeedStore,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str,
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for page in client.open_interest_pages(symbol, start_ms, end_ms, interval):
        r = oi_rows(page)
        store.write_page(page, len(r))
        rows += r
    new = pl.DataFrame(
        rows,
        schema={
            "time_ms": pl.Int64,
            "open_interest": pl.Float64,
            "source": pl.Utf8,
            "retrieved_at": pl.Utf8,
            "page_sha256": pl.Utf8,
        },
    )
    new = new.filter((pl.col("time_ms") >= start_ms) & (pl.col("time_ms") <= end_ms))
    return store.merge_table(new, "time_ms")


def fetch_premium_seed(
    client: BybitPublicHistory,
    store: SeedStore,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str,
) -> pl.DataFrame:
    parts: dict[str, list[dict[str, Any]]] = {"mark_close": [], "index_close": []}
    for kind, fld in (("mark-price-kline", "mark_close"), ("index-price-kline", "index_close")):
        for page in client.kline_pages(kind, symbol, start_ms, end_ms, interval):
            r = kline_rows(page, fld)
            store.write_page(page, len(r))
            parts[fld] += r
    sch = {"open_time_ms": pl.Int64, "retrieved_at": pl.Utf8, "page_sha256": pl.Utf8}
    m = pl.DataFrame(parts["mark_close"], schema={**sch, "mark_close": pl.Float64}).unique(
        "open_time_ms", keep="first"
    )
    i = pl.DataFrame(parts["index_close"], schema={**sch, "index_close": pl.Float64}).unique(
        "open_time_ms", keep="first"
    )
    new = (
        m.join(
            i.select(
                "open_time_ms", "index_close", pl.col("page_sha256").alias("index_page_sha256")
            ),
            on="open_time_ms",
            how="inner",
        )
        .with_columns(
            (pl.col("open_time_ms") + int(interval) * 60_000).alias("close_time_ms"),
            (pl.col("mark_close") / pl.col("index_close") - 1.0).alias("close"),
            pl.lit("bybit_rest_mark-price-kline/index-price-kline_" + interval).alias("source"),
        )
        .filter((pl.col("open_time_ms") >= start_ms) & (pl.col("open_time_ms") <= end_ms))
        .select(
            "open_time_ms",
            "close_time_ms",
            "mark_close",
            "index_close",
            "close",
            "source",
            "retrieved_at",
            "page_sha256",
            "index_page_sha256",
        )
    )
    return store.merge_table(new, "open_time_ms")


def fetch_funding_seed(
    client: BybitPublicHistory, store: SeedStore, symbol: str, start_ms: int, end_ms: int
) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for page in client.funding_pages(symbol, start_ms, end_ms):
        r = funding_rows(page)
        store.write_page(page, len(r))
        rows += r
    new = pl.DataFrame(
        rows,
        schema={
            "time_ms": pl.Int64,
            "funding_rate": pl.Float64,
            "source": pl.Utf8,
            "retrieved_at": pl.Utf8,
            "page_sha256": pl.Utf8,
        },
    ).filter((pl.col("time_ms") >= start_ms) & (pl.col("time_ms") <= end_ms))
    return store.merge_table(new, "time_ms")


# ----------------------------------------------------------------------------- PIT split
def split_warmup(
    table: pl.DataFrame, time_col: str, live_start_ms: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Warm-up rows (strictly before the V5 observation start) vs verification-only rows."""
    if table.is_empty():
        return table, table
    return (
        table.filter(pl.col(time_col) < live_start_ms),
        table.filter(pl.col(time_col) >= live_start_ms),
    )


# ----------------------------------------------------------------------------- alignment
def _live_valid(bars: pl.DataFrame) -> pl.DataFrame:
    cond = pl.col("trades") > 0
    if "gap_filled" in bars.columns:
        cond &= pl.col("gap_filled").fill_null(0.0) == 0
    return bars.filter(cond)


def _fl(x: Any) -> float:
    return float(x)


def _rel_stats(x: pl.Series) -> dict[str, Any]:
    v = x.drop_nans().drop_nulls().abs()
    if v.is_empty():
        return {"n": 0, "median": None, "p95": None, "max": None}
    return {
        "n": v.len(),
        "median": _fl(v.median() or 0.0),
        "p95": _fl(v.quantile(0.95) or 0.0),
        "max": _fl(v.max() or 0.0),
    }


def verify_oi_alignment(
    seed: pl.DataFrame, live_bars: pl.DataFrame, max_rel_diff: float
) -> dict[str, Any]:
    """Seed `timestamp` vs the live row's `oi_last` (ticker open interest at the bar close).
    Compares the seed value with the live bar closing at the same boundary (expected alignment)
    and, as a control, with the bar opening at it. Units: BTC in both (ratio ~ 1)."""
    live = _live_valid(live_bars).filter(
        pl.col("oi_last").is_not_null() & pl.col("oi_last").is_not_nan()
    )
    out: dict[str, Any] = {"kind": "open_interest", "max_rel_diff": max_rel_diff}
    if seed.is_empty() or live.is_empty():
        return out | {"ok": False, "reason": "no overlap rows", "n_overlap": 0}
    by_close = seed.join(
        live.select(pl.col("close_time_ms").alias("time_ms"), "oi_last"), on="time_ms", how="inner"
    )
    by_open = seed.join(
        live.select(pl.col("open_time_ms").alias("time_ms"), "oi_last"), on="time_ms", how="inner"
    )
    rc = _rel_stats(
        (by_close["open_interest"] / by_close["oi_last"] - 1.0)
        if by_close.height
        else pl.Series([], dtype=pl.Float64)
    )
    ro = _rel_stats(
        (by_open["open_interest"] / by_open["oi_last"] - 1.0)
        if by_open.height
        else pl.Series([], dtype=pl.Float64)
    )
    ratio = (
        _fl((by_close["open_interest"] / by_close["oi_last"]).median() or 0.0)
        if by_close.height
        else None
    )
    ok = (
        rc["n"] >= 12
        and rc["median"] is not None
        and rc["median"] <= max_rel_diff
        and ratio is not None
        and 0.98 <= ratio <= 1.02
    )
    return out | {
        "ok": bool(ok),
        "n_overlap": rc["n"],
        "alignment": "seed timestamp == live bar close_time (used)",
        "rel_diff_vs_bar_closing_at_timestamp": rc,
        "rel_diff_vs_bar_opening_at_timestamp (control)": ro,
        "unit_ratio_seed_over_live": ratio,
        "units": "BTC (base coin) on both sides",
        "first_overlap": int(_fl(by_close["time_ms"].min())) if by_close.height else None,
        "last_overlap": int(_fl(by_close["time_ms"].max())) if by_close.height else None,
    }


def verify_premium_alignment(
    seed: pl.DataFrame, live_bars: pl.DataFrame, max_rel_diff: float
) -> dict[str, Any]:
    live = _live_valid(live_bars).filter(
        pl.col("mark_close").is_not_nan() & pl.col("index_close").is_not_nan()
    )
    out: dict[str, Any] = {"kind": "premium", "max_rel_diff": max_rel_diff}
    if seed.is_empty() or live.is_empty():
        return out | {"ok": False, "reason": "no overlap rows", "n_overlap": 0}
    j = seed.join(
        live.select(
            "open_time_ms",
            pl.col("mark_close").alias("live_mark"),
            pl.col("index_close").alias("live_index"),
        ),
        on="open_time_ms",
        how="inner",
    )
    rm = _rel_stats(
        (j["mark_close"] / j["live_mark"] - 1.0) if j.height else pl.Series([], dtype=pl.Float64)
    )
    ri = _rel_stats(
        (j["index_close"] / j["live_index"] - 1.0) if j.height else pl.Series([], dtype=pl.Float64)
    )
    ok = (
        rm["n"] >= 12
        and rm["median"] is not None
        and rm["median"] <= max_rel_diff
        and ri["median"] is not None
        and ri["median"] <= max_rel_diff
    )
    return out | {
        "ok": bool(ok),
        "n_overlap": rm["n"],
        "alignment": "kline open_time == live bar open_time; close = last value of the bar",
        "mark_rel_diff": rm,
        "index_rel_diff": ri,
    }


def verify_funding_alignment(
    seed: pl.DataFrame, live_funding: pl.DataFrame | None
) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": "funding"}
    if seed.is_empty() or live_funding is None or live_funding.is_empty():
        return out | {"ok": False, "reason": "no overlap rows", "n_overlap": 0}
    j = seed.join(
        live_funding.select("time_ms", pl.col("funding_rate").alias("live_rate")),
        on="time_ms",
        how="inner",
    )
    if j.is_empty():
        return out | {
            "ok": False,
            "reason": "no funding event at a common timestamp",
            "n_overlap": 0,
        }
    d = (j["funding_rate"] - j["live_rate"]).abs()
    return out | {
        "ok": bool(_fl(d.max() or 0.0) <= 1e-7),
        "n_overlap": j.height,
        "max_abs_diff": _fl(d.max() or 0.0),
        "alignment": "settlement timestamp == live funding event time; settled rate",
    }
