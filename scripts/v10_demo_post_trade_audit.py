# ruff: noqa: E501  (report-formatting lines)
"""READ-ONLY V10 Demo post-trade audit, safe on a LIVE append-only V10 root (run on the authoritative Mac,
where the Demo journals live, while V10 keeps running).

Snapshot model: at start one immutable `audit_cutoff` is fixed. Every append-only input (demo_trades,
strategy journal, signals, episodes, outcomes, 1m bars, raw hourly trade files, the V10 config) is read
ONLY up to the byte size it had when the audit first touched it (a prefix); bytes appended later are
ignored, and only complete lines of that prefix are parsed. Records are further restricted to the cutoff
(closed Demo trades with closed_ms <= cutoff, journal records with ts <= cutoff, trade prints and bars
<= cutoff). At the end every prefix is re-read and must be byte-identical (sha256): growth after the
recorded size is allowed; truncation, deletion or any change inside the prefix FAILS the audit. Files that
V10 rewrites atomically by design (state.json, strategy_state.json, diagnostic.json) are not read at all.
Nothing is written anywhere except reports/forward_v10/demo_post_trade_audit_<ts>.{md,json}; no V10
module that can trade is imported; no network call; no tuning.

Usage:  BTC_DATA_DIR=<data dir> uv run python scripts/v10_demo_post_trade_audit.py [--n 6]
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import math
import os
import statistics
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MS_1M = 60_000
HORIZONS = (5, 15, 30, 60)
GROUPS = {  # entry-context feature groups (substring match on the journaled feature keys)
    "taker imbalance": ("imb_",),
    "delta / flow": ("delta_", "buy_", "sell_", "flow_sign", "abs_delta_pct"),
    "intensity": ("intensity",),
    "liquidations": ("liq_",),
    "absorption / impact": ("impact", "ret_sign", "ret_"),
    "order book": ("depth_imb", "bid_depth", "ask_depth", "book_valid"),
    "replenishment / depletion": ("repl",),
    "spread": ("spread",),
    "volatility": ("atr",),
    "location": ("dist_", "hi_60", "lo_60", "swing_", "range_"),
}
DIRECTIONAL = ("imb_", "delta_", "flow_sign", "depth_imb", "liq_imb")  # sign comparable with side


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _ms(iso: Any) -> int | None:
    if iso in (None, ""):
        return None
    if isinstance(iso, int | float):
        return int(iso)
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except ValueError:
        return None


def _iso(ms: int | None) -> str:
    return (
        "N/A"
        if ms is None
        else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    )


class SnapshotIntegrityError(AssertionError):
    """A recorded input prefix was truncated, deleted or modified during the audit."""


class PrefixSnapshot:
    """Immutable byte-prefix view of append-only files. `read(path)` captures, once per file, exactly
    the bytes that existed when the file is first touched; later appends are ignored; `verify()`
    re-reads each recorded prefix and fails on truncation, deletion or modification (growth is fine)."""

    def __init__(self, cutoff_ms: int) -> None:
        self.cutoff_ms = cutoff_ms
        self.entries: dict[str, dict[str, Any]] = {}

    def read(self, path: Path) -> bytes:
        key = str(path)
        if key in self.entries:
            return bytes(self.entries[key]["data"])
        if not path.exists():
            self.entries[key] = {
                "path": path,
                "missing": True,
                "size": 0,
                "sha256": None,
                "data": b"",
            }
            return b""
        size = os.stat(path).st_size
        with path.open("rb") as fh:
            data = fh.read(size)
        if len(data) != size:
            raise SnapshotIntegrityError(f"{path}: shrank while being read ({len(data)} < {size})")
        self.entries[key] = {
            "path": path,
            "missing": False,
            "size": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
        }
        return data

    def lines(self, path: Path) -> list[str]:
        """Complete lines of the prefix only (a line still being written is ignored)."""
        data = self.read(path)
        end = data.rfind(b"\n")
        return [] if end < 0 else data[: end + 1].decode("utf-8", errors="replace").splitlines()

    def jsonl(self, path: Path) -> list[dict[str, Any]]:
        out = []
        for line in self.lines(path):
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    out.append(json.loads(line))
        return out

    def verify(self) -> dict[str, Any]:
        growth = 0
        for e in self.entries.values():
            p: Path = e["path"]
            if e["missing"]:
                continue
            if not p.exists():
                raise SnapshotIntegrityError(f"{p}: deleted during the audit")
            now = os.stat(p).st_size
            if now < e["size"]:
                raise SnapshotIntegrityError(
                    f"{p}: truncated during the audit ({now} < {e['size']})"
                )
            with p.open("rb") as fh:
                head = fh.read(e["size"])
            if hashlib.sha256(head).hexdigest() != e["sha256"]:
                raise SnapshotIntegrityError(f"{p}: existing bytes modified during the audit")
            growth += now - e["size"]
        return {
            "files": sum(1 for e in self.entries.values() if not e["missing"]),
            "prefix_bytes": sum(int(e["size"]) for e in self.entries.values()),
            "appended_bytes_ignored": growth,
            "prefixes_identical": True,
        }

    def manifest(self) -> dict[str, Any]:
        return {
            k: {"size": e["size"], "sha256": e["sha256"], "missing": e["missing"]}
            for k, e in sorted(self.entries.items())
        }


def _ts_ok(rec: dict[str, Any], cutoff_ms: int) -> bool:
    t = _ms(rec.get("ts"))
    return t is None or t <= cutoff_ms


def _v(x: Any, nd: int = 2) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return "N/A" if math.isnan(x) else f"{x:.{nd}f}"
    return str(x)


def _med(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return statistics.median(v) if v else None


# ----------------------------------------------------------------------------- price path (raw trades)
class RawTrades:
    """publicTrade prints from the V10 raw store (hourly files), cached per hour; read-only."""

    def __init__(self, root: Path, snap: PrefixSnapshot) -> None:
        self.root = root
        self.snap = snap
        self.cache: dict[str, list[tuple[int, float]]] = {}
        self.files_read: list[Path] = []

    def _hour(self, ms: int) -> list[tuple[int, float]]:
        d = datetime.fromtimestamp(ms / 1000, tz=UTC)
        key = d.strftime("%Y-%m-%d/%H")
        if key in self.cache:
            return self.cache[key]
        rows: list[tuple[int, float]] = []
        for p in (self.root / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.jsonl",
                  self.root / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.jsonl.gz"):  # fmt: skip
            if not p.exists():
                continue
            self.files_read.append(p)
            if p.suffix == ".gz":
                try:
                    text = gzip.decompress(self.snap.read(p)).decode("utf-8", errors="replace")
                except (OSError, EOFError):
                    continue
                lines = text.splitlines()
            else:
                lines = self.snap.lines(p)
            for line in lines:
                if "publicTrade" not in line:
                    continue
                try:
                    msg = json.loads(json.loads(line)["raw"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                if not str(msg.get("topic", "")).startswith("publicTrade"):
                    continue
                for t in msg.get("data") or []:
                    tm, px = _f(t.get("T")), _f(t.get("p"))
                    if tm is not None and px is not None and tm <= self.snap.cutoff_ms:
                        rows.append((int(tm), px))
        rows.sort()
        self.cache[key] = rows
        return rows

    def between(self, t0: int, t1: int) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        h = t0 - t0 % 3_600_000
        while h <= t1:
            out += [r for r in self._hour(h) if t0 <= r[0] <= t1]
            h += 3_600_000
        return out


class Bars:
    """1m bars journaled by V10 (derived/bars_1m.jsonl): fallback path and regime context."""

    def __init__(self, path: Path, snap: PrefixSnapshot) -> None:
        self.rows = sorted(
            (
                (int(r["open_ms"]), r)
                for r in snap.jsonl(path)
                if r.get("open_ms") is not None
                and int(r["open_ms"]) + MS_1M <= snap.cutoff_ms  # completed before the cutoff
            ),
            key=lambda x: x[0],
        )

    def between(self, t0: int, t1: int) -> list[dict[str, Any]]:
        return [r for t, r in self.rows if t0 <= t < t1]

    def close_at(self, t: int) -> float | None:
        best = None
        for t_open, r in self.rows:
            if t_open + MS_1M <= t:
                best = _f(r.get("c"))
            else:
                break
        return best


def price_path(raw: RawTrades, bars: Bars, t0: int, t1: int) -> tuple[list[tuple[int, float]], str]:
    p = raw.between(t0, t1)
    if p:
        return p, "raw trades"
    rows = bars.between(t0 - MS_1M, t1)
    pts: list[tuple[int, float]] = []
    for r in rows:
        t = int(r["open_ms"])
        pts += [(t, float(r["h"])), (t, float(r["l"])), (t + MS_1M - 1, float(r["c"]))]
    return pts, "1m bars (approximate)" if pts else "none"


# ----------------------------------------------------------------------------- per-trade analysis
def analyse(t: dict[str, Any], sig: dict[str, Any] | None, ep_arm: dict[str, Any] | None,
            outs: dict[int, float], raw: RawTrades, bars: Bars, tp1_r: float) -> dict[str, Any]:  # fmt: skip
    s = 1.0 if t.get("side") == "LONG" else -1.0
    entry, stop = _f(t.get("entry_price")), _f(t.get("stop"))
    ref = _f(t.get("entry_ref"))
    tp1, tp2 = _f(t.get("tp1")), _f(t.get("tp2"))
    qty = _f(t.get("qty")) or 0.0
    exits = t.get("exits") or []
    t_in = _ms(t.get("fill_ts")) or _ms(t.get("decision_ts"))
    ex_ms = [m for m in (_ms(e.get("time")) for e in exits) if m is not None]
    t_out = max(ex_ms) if ex_ms else _ms(t.get("closed_ts"))
    risk_px = abs(entry - stop) if (entry is not None and stop is not None) else None
    risk_usdt = _f(t.get("actual_risk_usdt")) or _f(t.get("planned_risk_usdt"))
    a: dict[str, Any] = {
        "signal_id": t.get("signal_id"), "family": t.get("family"), "side": t.get("side"),
        "entry_ts": _iso(t_in), "exit_ts": _iso(t_out), "entry_fill": entry, "entry_ref": ref,
        "qty": qty, "stop": stop, "tp1": tp1, "tp2": tp2, "exit_reason": t.get("exit_reason"),
        "gross_pnl": _f(t.get("gross_pnl")), "fees": _f(t.get("fees")), "funding": _f(t.get("funding")),
        "net_pnl": _f(t.get("net_pnl")), "risk_usdt_planned": _f(t.get("planned_risk_usdt")),
        "risk_usdt_actual": _f(t.get("actual_risk_usdt")),
        "R_net_actual": _f(t.get("R_net_vs_actual")), "R_net_planned": _f(t.get("R_net_vs_planned")),
        "hold_min": (t_out - t_in) / MS_1M if (t_in and t_out) else None,
    }  # fmt: skip
    # ---- execution: entry slippage vs the decision price, stop-leg slippage vs the stop level
    a["entry_slip_bps"] = s * (entry / ref - 1) * 1e4 if (entry and ref) else None
    a["entry_slip_usd"] = qty * s * (entry - ref) if (entry and ref) else None
    stop_slip_usd = 0.0
    stop_legs: list[float] = []
    legs = []
    for e in exits:
        px, q, link = _f(e.get("price")), _f(e.get("qty")) or 0.0, str(e.get("link") or "")
        kind = (
            "TP1" if link.endswith("-T1") else "TP2" if link.endswith("-T2")
            else "MARKET_CLOSE(TIME/HARD)" if link.endswith("-TC")
            else "EXCHANGE_STOP"
        )  # fmt: skip
        legs.append({"kind": kind, "price": px, "qty": q, "time": e.get("time")})
        if kind == "EXCHANGE_STOP" and px is not None and stop is not None:
            stop_legs.append(px)
            stop_slip_usd += q * s * (stop - px)  # positive = filled worse than the stop level
    a["exit_fills"] = legs
    a["stop_exit_slip_bps"] = (
        statistics.fmean(s * (stop - px) / stop * 1e4 for px in stop_legs)
        if stop_legs and stop
        else None
    )
    a["exit_slip_usd"] = stop_slip_usd if stop_legs else 0.0
    a["fees_R"] = (a["fees"] / risk_usdt) if (a["fees"] is not None and risk_usdt) else None
    # ---- path: MFE / MAE during the trade, post-exit 60 min
    if t_in and t_out and entry and risk_px:
        path, src = price_path(raw, bars, t_in, t_out)
        a["path_source"] = src
        if path:
            fav = max(path, key=lambda r: s * r[1])
            adv = min(path, key=lambda r: s * r[1])
            a["max_favourable_price"], a["max_adverse_price"] = fav[1], adv[1]
            a["mfe_bps"] = s * (fav[1] / entry - 1) * 1e4
            a["mae_bps"] = s * (adv[1] / entry - 1) * 1e4
            a["mfe_R"] = s * (fav[1] - entry) / risk_px
            a["mae_R"] = s * (adv[1] - entry) / risk_px
            a["min_to_mfe"] = (fav[0] - t_in) / MS_1M
            a["min_to_mae"] = (adv[0] - t_in) / MS_1M
            a["tp1_touched_in_trade"] = bool(tp1 is not None and s * (fav[1] - tp1) >= 0)
            first_adverse_half = next(
                (r for r in path if s * (r[1] - entry) <= -0.5 * risk_px), None
            )
            a["min_to_minus_half_R"] = (
                (first_adverse_half[0] - t_in) / MS_1M if first_adverse_half else None
            )
        cutoff = raw.snap.cutoff_ms
        a["post_exit_window_complete"] = t_out + 60 * MS_1M <= cutoff
        post, _src = price_path(raw, bars, t_out + 1, min(t_out + 60 * MS_1M, cutoff))
        if post:
            pf = max(post, key=lambda r: s * r[1])
            a["post_exit_60m_best_R_from_entry"] = s * (pf[1] - entry) / risk_px
            a["post_exit_60m_tp1_touched"] = bool(tp1 is not None and s * (pf[1] - tp1) >= 0)
            a["post_exit_60m_tp2_touched"] = bool(tp2 is not None and s * (pf[1] - tp2) >= 0)
        for h in HORIZONS:
            if t_in + h * MS_1M > cutoff:  # horizon not yet reached at the audit cutoff
                a[f"fwd_{h}m_bps_from_fill"] = a[f"fwd_{h}m_R_from_fill"] = None
                continue
            pts = raw.between(t_in + h * MS_1M - 30_000, t_in + h * MS_1M)
            px = pts[-1][1] if pts else bars.close_at(t_in + h * MS_1M)
            a[f"fwd_{h}m_bps_from_fill"] = s * (px / entry - 1) * 1e4 if px else None
            a[f"fwd_{h}m_R_from_fill"] = s * (px - entry) / risk_px if px else None
    a["tp1_filled"] = any(str(e.get("link") or "").endswith("-T1") for e in exits)
    for h in HORIZONS:
        a[f"outcome_{h}m_bps_from_decision"] = outs.get(h)
    # ---- regime context from 1m bars before entry
    if t_in:
        pre = bars.between(t_in - 240 * MS_1M, t_in)
        c = [float(r["c"]) for r in pre if _f(r.get("c"))]
        if len(c) >= 61:
            r1 = [(c[i] / c[i - 1] - 1) * 1e4 for i in range(len(c) - 60, len(c))]
            a["pre_vol_60m_bps"] = statistics.pstdev(r1)
            a["pre_ret_60m_bps_side_adj"] = s * (c[-1] / c[-61] - 1) * 1e4
            hi = max(float(r["h"]) for r in pre[-60:])
            lo = min(float(r["l"]) for r in pre[-60:])
            a["pre_range_60m_bps"] = (hi / lo - 1) * 1e4
        if len(c) >= 240:
            a["pre_ret_240m_bps_side_adj"] = s * (c[-1] / c[-240] - 1) * 1e4
    # ---- entry context (journaled features, conditions)
    f = (sig or {}).get("features") or {}
    last = _f(f.get("last"))
    ctx: dict[str, dict[str, Any]] = {}
    for g, keys in GROUPS.items():
        ctx[g] = {
            k: v
            for k, v in sorted(f.items())
            if any(x in k for x in keys) and not isinstance(v, dict)
        }
    a["context"] = ctx
    a["atr5_bps"] = (_f(f.get("atr5")) / last * 1e4) if (last and _f(f.get("atr5"))) else None
    a["spread_bps"] = _f(f.get("spread_bps"))
    a["trigger_conditions"] = (sig or {}).get("trigger_conditions")
    a["arm_conditions"] = (ep_arm or {}).get("conditions")
    a["stop_note"] = (sig or {}).get("stop_note")
    a["stop_pct"] = _f((sig or {}).get("stop_pct"))
    against = {}
    for k, v in f.items():
        x = _f(v)
        if x is not None and x != 0 and any(k.startswith(d) or d in k for d in DIRECTIONAL):
            against[k] = "WITH side" if x * s > 0 else "AGAINST side"
    a["directional_alignment"] = dict(sorted(against.items()))
    a["decision_latency_ms"] = (sig or {}).get("decision_latency_ms")
    a["tp1_r"] = tp1_r
    return a


# ----------------------------------------------------------------------------- report
def run_audit(
    root: Path,
    out_dir: Path,
    n_last: int = 6,
    symbol: str = "BTCUSDT",
    cutoff_ms: int | None = None,
    config_path: Path = Path("config/btc_swing_v10.yaml"),
    during: Callable[[], None] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Audit an append-only V10 root as of one immutable cutoff. `during` (tests only) runs after the
    analysis and before the integrity verification, i.e. while the audit is 'in flight'."""
    cutoff = int(datetime.now(UTC).timestamp() * 1000) if cutoff_ms is None else int(cutoff_ms)
    snap = PrefixSnapshot(cutoff)
    files = {
        "demo_trades": root / "demo" / "demo_trades.jsonl",
        "demo_journal": root / "demo" / "strategy_journal.jsonl",
        "signals": root / "journals" / "signals.jsonl",
        "episodes": root / "journals" / "episodes.jsonl",
        "outcomes": root / "journals" / "outcomes.jsonl",
        "bars_1m": root / "derived" / "bars_1m.jsonl",
    }
    for p in files.values():  # capture every journal prefix at the start, before any analysis
        snap.read(p)
    tp1_r = 1.0
    for line in snap.lines(config_path):
        if line.strip().startswith("tp1_r:"):
            tp1_r = float(line.split(":")[1].split("#")[0])
    trades = sorted(
        (
            t
            for t in snap.jsonl(files["demo_trades"])
            if t.get("signal_id")
            and (_ms(t.get("closed_ms")) or _ms(t.get("closed_ts")) or 10**15) <= cutoff
        ),
        key=lambda t: _ms(t.get("closed_ms")) or _ms(t.get("closed_ts")) or 0,
    )
    if not trades:
        raise SystemExit(f"no closed Demo trades before the cutoff in {files['demo_trades']}")
    sigs = {
        r["data"]["signal_id"]: r["data"]
        for r in snap.jsonl(files["signals"])
        if r.get("kind") == "SIGNAL_EXECUTED"
        and isinstance(r.get("data"), dict)
        and _ts_ok(r, cutoff)
    }
    arms = {
        r["data"].get("episode_id"): r["data"]
        for r in snap.jsonl(files["episodes"])
        if r.get("kind") == "EPISODE_ARMED"
        and isinstance(r.get("data"), dict)
        and _ts_ok(r, cutoff)
    }
    outs: dict[str, dict[int, float]] = {}
    for r in snap.jsonl(files["outcomes"]):
        d = r.get("data") or {}
        if (
            d.get("kind") == "EXECUTED"
            and _f(d.get("return_bps")) is not None
            and _ts_ok(r, cutoff)
        ):
            outs.setdefault(str(d.get("id")), {})[int(d.get("horizon_min") or 0)] = float(
                d["return_bps"]
            )
    raw = RawTrades(root / "raw" / symbol, snap)
    bars = Bars(files["bars_1m"], snap)
    res = []
    for n, t in enumerate(trades, 1):
        sig = sigs.get(t["signal_id"])
        a = analyse(t, sig, arms.get(t.get("episode_id") or (sig or {}).get("episode_id")),
                    outs.get(t["signal_id"], {}), raw, bars, tp1_r)  # fmt: skip
        a["trade_no"] = n
        res.append(a)
    last_n = list(reversed(res[-n_last:]))
    losses = [a for a in reversed(res) if (a["net_pnl"] or 0) < 0][:3]
    first_loss_no = min((a["trade_no"] for a in losses), default=10**9)
    winners = [a for a in res if (a["net_pnl"] or 0) > 0 and a["trade_no"] < first_loss_no]
    if during is not None:
        during()
    integrity = snap.verify()  # raises SnapshotIntegrityError on truncation / modification

    out: list[str] = ["# V10 Demo post-trade audit (READ-ONLY)\n"]
    out.append(
        f"Generated {datetime.now(UTC).isoformat()} · root `{root}` · audit cutoff {_iso(cutoff)} UTC · closed Demo "
        f"trades completed before the cutoff {len(trades)} · snapshot: {integrity['files']} files, "
        f"{integrity['prefix_bytes']} prefix bytes, all prefixes byte-identical at the end; "
        f"{integrity['appended_bytes_ignored']} bytes appended by the live runner during the audit were ignored · "
        f"raw hourly files read {len(set(raw.files_read))}.\n"
    )
    out.append(f"## 1. Last {len(last_n)} closed Demo trades (newest first)\n")
    for a in last_n:
        out.append(f"### Trade #{a['trade_no']} · {a['family']} {a['side']} · {a['signal_id']}\n")
        rows = [
            ("entry / exit", f"{a['entry_ts']} -> {a['exit_ts']} (hold {_v(a['hold_min'], 1)} min)"),
            ("entry ref / fill / qty", f"{_v(a['entry_ref'], 1)} / {_v(a['entry_fill'], 1)} / {_v(a['qty'], 4)}"),
            ("stop / TP1 / TP2", f"{_v(a['stop'], 1)} / {_v(a['tp1'], 1)} / {_v(a['tp2'], 1)} (stop {_v(a['stop_pct'])}% {a['stop_note']})"),
            ("exit reason / fills", f"{a['exit_reason']} / " + "; ".join(f"{x['kind']} {_v(x['price'], 1)} x {_v(x['qty'], 4)} at {str(x['time'])[11:19]}" for x in a["exit_fills"])),
            ("gross / fees / funding / net USDT", f"{_v(a['gross_pnl'])} / {_v(a['fees'])} / {_v(a['funding'])} / {_v(a['net_pnl'])}"),
            ("risk planned / actual USDT · R (actual / planned)", f"{_v(a['risk_usdt_planned'])} / {_v(a['risk_usdt_actual'])} · {_v(a['R_net_actual'])} / {_v(a['R_net_planned'])}"),
            ("entry slippage", f"{_v(a['entry_slip_bps'])} bps ({_v(a['entry_slip_usd'])} USDT; + = worse) · decision latency {a['decision_latency_ms']} ms"),
            ("stop-leg slippage", f"{_v(a['stop_exit_slip_bps'])} bps ({_v(a['exit_slip_usd'])} USDT) · fees {_v(a['fees_R'])} R"),
            ("MFE", f"{_v(a.get('mfe_R'))} R / {_v(a.get('mfe_bps'))} bps at {_v(a.get('max_favourable_price'), 1)} after {_v(a.get('min_to_mfe'), 1)} min"),
            ("MAE", f"{_v(a.get('mae_R'))} R / {_v(a.get('mae_bps'))} bps at {_v(a.get('max_adverse_price'), 1)} after {_v(a.get('min_to_mae'), 1)} min"),
            ("path source", a.get("path_source", "N/A")),
            ("TP1 filled / touched in trade", f"{_v(a['tp1_filled'])} / {_v(a.get('tp1_touched_in_trade'))}"),
            ("minutes to -0.5R", _v(a.get("min_to_minus_half_R"), 1)),
            ("forward from fill +5/+15/+30/+60 (bps)", " / ".join(_v(a.get(f"fwd_{h}m_bps_from_fill"), 1) for h in HORIZONS)),
            ("outcome journal from decision +5/+15/+30/+60 (bps)", " / ".join(_v(a.get(f"outcome_{h}m_bps_from_decision"), 1) for h in HORIZONS)),
            ("after exit (60 min): best R from entry · TP1 / TP2 touched", f"{_v(a.get('post_exit_60m_best_R_from_entry'))} · {_v(a.get('post_exit_60m_tp1_touched'))} / {_v(a.get('post_exit_60m_tp2_touched'))}"),
            ("regime before entry: vol60 / range60 / ret60 / ret240 (bps, ret side-adjusted)", f"{_v(a.get('pre_vol_60m_bps'))} / {_v(a.get('pre_range_60m_bps'), 1)} / {_v(a.get('pre_ret_60m_bps_side_adj'), 1)} / {_v(a.get('pre_ret_240m_bps_side_adj'), 1)}"),
            ("ATR5 / spread (bps)", f"{_v(a.get('atr5_bps'), 1)} / {_v(a.get('spread_bps'))}"),
        ]  # fmt: skip
        out += ["| field | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows] + [""]
        out.append("**entry context (journaled V10 features)**\n")
        for g, d in a["context"].items():
            if d:
                out.append(
                    f"- {g}: "
                    + ", ".join(
                        f"{k}={_v(_f(v), 3) if _f(v) is not None else v}" for k, v in d.items()
                    )
                )
        out.append(f"- directional alignment vs trade side: {a['directional_alignment']}")
        out.append(f"- trigger conditions: {a['trigger_conditions']}")
        out.append(f"- arm conditions: {a['arm_conditions']}\n")

    # ---- last 3 losses vs previous winners
    out.append(f"## 2. Last {len(losses)} losses vs {len(winners)} previous winners (medians)\n")
    keys = [
        "mfe_R", "mae_R", "min_to_mfe", "min_to_minus_half_R", "hold_min", "entry_slip_bps",
        "stop_exit_slip_bps", "fees_R", "atr5_bps", "spread_bps", "pre_vol_60m_bps",
        "pre_range_60m_bps", "pre_ret_60m_bps_side_adj", "pre_ret_240m_bps_side_adj",
        "fwd_5m_R_from_fill", "fwd_15m_R_from_fill", "fwd_30m_R_from_fill", "fwd_60m_R_from_fill",
        "R_net_actual",
    ]  # fmt: skip
    out += ["| metric | last losses | " + " | ".join(f"#{a['trade_no']}" for a in losses) + " | previous winners (median) |",
            "|---|---|" + "---|" * len(losses) + "---|"]  # fmt: skip
    for k in keys:
        out.append(
            f"| {k} | {_v(_med([a.get(k) for a in losses]))} | "
            + " | ".join(_v(a.get(k)) for a in losses)
            + f" | {_v(_med([a.get(k) for a in winners]))} |"
        )
    out.append(
        "| family / side | | " + " | ".join(f"{a['family']} {a['side']}" for a in losses)
        + f" | {sorted((a['family'], a['side']) for a in winners)} |"
    )  # fmt: skip
    out.append("| exit reason | | " + " | ".join(str(a["exit_reason"]) for a in losses) + " | |")
    out.append("")
    # numeric entry features that separate ALL losses from ALL winners (descriptive only; tiny n)
    sep = []
    fl = [{k: _f(v) for g in a["context"].values() for k, v in g.items()} for a in losses]
    fw = [{k: _f(v) for g in a["context"].values() for k, v in g.items()} for a in winners]
    for k in sorted({k for d in fl for k in d}):
        lv = [d.get(k) for d in fl]
        wv = [d.get(k) for d in fw if d.get(k) is not None]
        if None in lv or not wv:
            continue
        lv_ = [x for x in lv if x is not None]
        if min(lv_) > max(wv) or max(lv_) < min(wv):
            sep.append(
                f"{k}: losses {[round(x, 3) for x in lv_]} vs winners range [{min(wv):.3f}, {max(wv):.3f}]"
            )
    out.append(
        "**entry features where all losses fall outside the winners' range** (descriptive, n tiny):\n"
    )
    out += [f"- {x}" for x in sep] or ["- none"]
    out.append("")
    # ---- mechanical answers
    out.append("## 3. Mechanical answers (descriptive; no rule change is implied by this sample)\n")
    for a in losses:
        mfe = a.get("mfe_R")
        kind = (
            "N/A (no price path)" if mfe is None else
            "worked first, then reversed (MFE >= 0.5R)" if mfe >= 0.5 else
            "small favourable move, then lost (0.1R <= MFE < 0.5R)" if mfe >= 0.1 else
            "adverse almost immediately (MFE < 0.1R)"
        )  # fmt: skip
        slip = (a.get("entry_slip_usd") or 0) + (a.get("exit_slip_usd") or 0)
        net = a.get("net_pnl") or 0
        contra = [k for k, v in a["directional_alignment"].items() if v == "AGAINST side"]
        out.append(
            f"- #{a['trade_no']} {a['family']} {a['side']}: {kind}; MFE {_v(mfe)} R, MAE {_v(a.get('mae_R'))} R; "
            f"price first at -0.5R after {_v(a.get('min_to_minus_half_R'), 1)} min, hold {_v(a.get('hold_min'), 1)} min; "
            f"slippage {_v(slip)} USDT of net {_v(net)} USDT ({_v(100 * slip / abs(net) if net else None, 0)}%), fees {_v(a.get('fees_R'))} R; "
            f"after exit TP1 touched within 60 min: {_v(a.get('post_exit_60m_tp1_touched'))}; "
            f"directional features against the side: {contra or 'none'}"
        )  # fmt: skip
    out.append(
        "\nNote: families A (liquidation absorption reversal) and B (flow absorption at a level) are DESIGNED to enter "
        "against the recent flow / liquidation side; an 'AGAINST side' flow reading is contradictory only for family C "
        "(liquidity break continuation)."
    )
    md = "\n".join(out) + "\n"
    od = out_dir
    od.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    md_path, js_path = (
        od / f"demo_post_trade_audit_{ts}.md",
        od / f"demo_post_trade_audit_{ts}.json",
    )
    rep = {
        "audit_cutoff": _iso(cutoff),
        "audit_cutoff_ms": cutoff,
        "trades": res,
        "last_n": [a["trade_no"] for a in last_n],
        "losses": [a["trade_no"] for a in losses],
        "winners": [a["trade_no"] for a in winners],
        "integrity": integrity,
        "input_prefixes": snap.manifest(),
    }
    md_path.write_text(md)
    js_path.write_text(json.dumps(rep, indent=1, default=str))
    return md_path, js_path, rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("BTC_DATA_DIR", "./data"))
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--out-dir", default="reports/forward_v10")
    ap.add_argument("--root", default=None, help="override the V10 root (tests)")
    args = ap.parse_args()
    root = Path(args.root) if args.root else Path(args.data_dir) / "btc" / "forward_v10"
    md_path, js_path, _rep = run_audit(root, Path(args.out_dir), args.n, args.symbol)
    print(md_path.read_text())
    print(f"written: {md_path} / {js_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
