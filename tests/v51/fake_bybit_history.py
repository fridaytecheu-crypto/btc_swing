"""In-process fake of the Bybit PUBLIC history endpoints (httpx.MockTransport): open-interest with
cursor pagination (DESC order, limit 200), mark/index 5m klines (limit 1000, DESC), funding history
(limit 200, DESC). Deterministic series so alignment tests can compare against 'live' rows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

MS_5M = 300_000
H8 = 8 * 3_600_000


def oi_at(t_ms: int) -> float:
    return 58_000.0 + (t_ms // MS_5M % 1000) * 0.5


def mark_at(t_ms: int) -> float:
    return 80_000.0 + (t_ms // MS_5M % 500) * 2.0


def index_at(t_ms: int) -> float:
    return mark_at(t_ms) * (1 - 2e-4)


def funding_at(t_ms: int) -> float:
    return 1e-4 + (t_ms // H8 % 7) * 1e-5


@dataclass
class FakeBybitHistory:
    first_ms: int  # earliest available 5m boundary
    last_ms: int  # latest available 5m boundary
    requests: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0  # inject this many transport failures
    rate_limit_next: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _ok(self, result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": result})

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        p = dict(parse_qsl(u.query))
        self.requests.append({"path": u.path, "params": p})
        if self.fail_next > 0:
            self.fail_next -= 1
            raise httpx.ConnectError("injected", request=req)
        if self.rate_limit_next > 0:
            self.rate_limit_next -= 1
            return httpx.Response(
                200, json={"retCode": 10006, "retMsg": "rate limit", "result": {}}
            )
        if u.path == "/v5/market/open-interest":
            return self._oi(p)
        if u.path in ("/v5/market/mark-price-kline", "/v5/market/index-price-kline"):
            return self._kline(p, u.path.endswith("mark-price-kline"))
        if u.path == "/v5/market/funding/history":
            return self._funding(p)
        return httpx.Response(404, text="not found")

    def _oi(self, p: dict[str, Any]) -> httpx.Response:
        start, end = int(p.get("startTime", self.first_ms)), int(p.get("endTime", self.last_ms))
        ts = [t for t in range(self.first_ms, self.last_ms + 1, MS_5M) if start <= t <= end]
        ts.sort(reverse=True)
        cursor = int(p["cursor"]) if p.get("cursor") else 0
        page = ts[cursor : cursor + 200]
        nxt = str(cursor + 200) if cursor + 200 < len(ts) else ""
        return self._ok(
            {
                "symbol": p.get("symbol"),
                "category": "linear",
                "list": [{"openInterest": f"{oi_at(t):.3f}", "timestamp": str(t)} for t in page],
                "nextPageCursor": nxt,
            }
        )

    def _kline(self, p: dict[str, Any], mark: bool) -> httpx.Response:
        start, end = int(p.get("start", self.first_ms)), int(p.get("end", self.last_ms))
        ts = [
            t for t in range(self.first_ms, self.last_ms + 1, MS_5M) if start <= t <= end
        ]  # open times
        ts.sort(reverse=True)
        page = ts[:1000]
        f = mark_at if mark else index_at
        return self._ok(
            {
                "symbol": p.get("symbol"),
                "category": "linear",
                "list": [
                    [str(t), f"{f(t) - 1:.2f}", f"{f(t) + 5:.2f}", f"{f(t) - 5:.2f}", f"{f(t):.2f}"]
                    for t in page
                ],
            }
        )

    def _funding(self, p: dict[str, Any]) -> httpx.Response:
        start, end = int(p.get("startTime", self.first_ms)), int(p.get("endTime", self.last_ms))
        first = (self.first_ms // H8 + 1) * H8
        ts = [t for t in range(first, self.last_ms + 1, H8) if start <= t <= end]
        ts.sort(reverse=True)
        page = ts[:200]
        return self._ok(
            {
                "category": "linear",
                "list": [
                    {
                        "symbol": p.get("symbol"),
                        "fundingRate": f"{funding_at(t):.8f}",
                        "fundingRateTimestamp": str(t),
                    }
                    for t in page
                ],
            }
        )


def live_bars_matching(start_ms: int, n: int) -> list[dict[str, Any]]:
    """Live processor rows consistent with the fake server (for alignment tests): the live
    `oi_last` of the bar closing at T is the OI sampled at T, mark/index closes are the kline
    closes of the bar opening at T-5m."""
    rows = []
    for i in range(n):
        o = start_ms + i * MS_5M
        c = o + MS_5M
        rows.append(
            {
                "open_time_ms": o,
                "close_time_ms": c,
                "trades": 100,
                "gap_filled": 0.0,
                "oi_last": oi_at(c) * (1 + 1e-5),  # last ticker just before the boundary
                "mark_close": mark_at(o),
                "index_close": index_at(o),
            }
        )
    return rows


def dumps(x: Any) -> str:
    return json.dumps(x)
