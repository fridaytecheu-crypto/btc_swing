# ruff: noqa: E501
"""READ-ONLY forensic audit of the V11-LITE-WARM forward SHADOW trades (owner request 2026-10-17).

Reads the LITE root's journals (shadow_trades, signals, control_twin, nearmiss, outcomes, episodes),
derived 1m bars and raw trades through an immutable byte-prefix snapshot (safe on a LIVE root: later
appends are ignored, any truncation / modification fails the audit). Writes ONLY
reports/forward_v11_lite_warm/forensic_<run>.{json,md}. Nothing in the strategy, config, freeze,
journals or runner is touched or re-decided; every number is derived from what the frozen runner
journaled plus the price path after the decision.

Per closed trade: timestamp, side, selected template, score and the 7 group contributions, the frozen
A/B/C structure labels for BOTH sides, strict-gate PASS/FAIL of the selected template, entry context
(RSI, ADX, ATR / tercile, VWAP, range location, taker flow, order book, liquidations, OI / funding,
Fibonacci), outcome (net R / USDT, exit reason, hold, MFE / MAE in R, +0.25 / +0.5 / +1R reached before
the loss, time to MFE / MAE, post-exit 60 min), and a loss class (A immediately wrong / B small
favourable then failed / C materially favourable then gave back / D stop-exit geometry flag /
E execution-cost). Aggregates, the strict-only and soft-filter counterfactuals (executed-trade subset,
exact; near-miss additions, directional approximation only), the control-twin comparison and the
implementation pathology checks follow. 14 trades decide nothing: no threshold is proposed."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import itertools
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MS_1M = 60_000
WEIGHTS = {
    "trend": 20.0,
    "momentum": 15.0,
    "volatility": 10.0,
    "volume": 15.0,
    "location": 15.0,
    "derivatives": 10.0,
    "microstructure": 15.0,
}
GROUPS = tuple(WEIGHTS)
ENTRY, ARMED = 75.0, 70.0
TP1_R, TP2_R = 0.60, 1.20
LEVELS_R = (0.25, 0.5, 1.0)
TEMPLATES = ("A", "B", "C")
NA = "N/A"


# ----------------------------------------------------------------------------- helpers
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _iso(ms: int | float | None) -> str:
    return (
        NA if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
    )


def _v(x: Any, nd: int = 2) -> str:
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int | float) and x is not None and math.isfinite(float(x)):
        return f"{x:.{nd}f}"
    return NA if x is None else str(x)


def _sign_word(x: float | None, s: float) -> str:
    if x is None or x == 0:
        return NA
    return "WITH" if x * s > 0 else "AGAINST"


class SnapshotIntegrityError(AssertionError):
    pass


def _stat_sig(st: os.stat_result) -> tuple[int, int, int]:
    """Identity of one file version: inode, size, mtime (ns). A tmp + rename replacement changes it."""
    return (int(st.st_ino), int(st.st_size), int(st.st_mtime_ns))


class PrefixSnapshot:
    """Two kinds of input, two guarantees (see scripts/v10_demo_post_trade_audit.py for the first):

    - APPEND-ONLY files (journals, derived 1m bars, raw store): `read` captures the byte prefix that
      existed when the file was first touched; later appends are ignored; `verify` re-reads each
      prefix at the end and fails on truncation, deletion or modification.
    - MUTABLE files (the runner's `state.json`, rewritten atomically by tmp + rename every tick):
      `read_mutable` captures ONE internally consistent version at audit start: the inode / size /
      mtime seen before opening, on the open descriptor and after reading must agree and the bytes
      must parse as JSON, otherwise the read is retried; after `tries` attempts the audit fails
      explicitly. Stability is required during the capture only, never for the rest of the audit.
    A path is either kind, never both."""

    def __init__(self, cutoff_ms: int) -> None:
        self.cutoff_ms = cutoff_ms
        self.entries: dict[str, dict[str, Any]] = {}

    def read(self, path: Path) -> bytes:
        key = str(path)
        if key in self.entries:
            if self.entries[key]["kind"] != "append_only":
                raise SnapshotIntegrityError(
                    f"{path}: captured as a mutable file; prefix read refused"
                )
            return bytes(self.entries[key]["data"])
        if not path.exists():
            self.entries[key] = {
                "path": path,
                "kind": "append_only",
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
            raise SnapshotIntegrityError(f"{path}: shrank while being read")
        self.entries[key] = {
            "path": path,
            "kind": "append_only",
            "missing": False,
            "size": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": data,
        }
        return data

    def read_mutable(self, path: Path, tries: int = 25, delay_s: float = 0.04) -> bytes:
        """One internally consistent version of a file that a live writer replaces atomically."""
        key = str(path)
        if key in self.entries:
            if self.entries[key]["kind"] != "mutable":
                raise SnapshotIntegrityError(
                    f"{path}: captured as append-only; mutable read refused"
                )
            return bytes(self.entries[key]["data"])
        reasons: list[str] = []
        for attempt in range(1, tries + 1):
            try:
                before = _stat_sig(os.stat(path))
                with path.open("rb") as fh:
                    opened = _stat_sig(os.fstat(fh.fileno()))
                    data = fh.read()
                after = _stat_sig(os.stat(path))
            except FileNotFoundError:
                if not path.exists() and attempt == 1 and not reasons:
                    self.entries[key] = {
                        "path": path,
                        "kind": "mutable",
                        "missing": True,
                        "size": 0,
                        "sha256": None,
                        "data": b"",
                        "attempts": attempt,
                    }
                    return b""
                reasons.append("replaced while opening")
                time.sleep(delay_s)
                continue
            if before != opened or opened != after or len(data) != before[1]:
                reasons.append(f"version changed during read ({before} -> {after})")
                time.sleep(delay_s)
                continue
            try:
                json.loads(data)
            except ValueError:
                reasons.append("not valid JSON (partial write)")
                time.sleep(delay_s)
                continue
            self.entries[key] = {
                "path": path,
                "kind": "mutable",
                "missing": False,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": data,
                "version": {"inode": after[0], "size": after[1], "mtime_ns": after[2]},
                "attempts": attempt,
            }
            return data
        raise SnapshotIntegrityError(
            f"{path}: no internally consistent snapshot after {tries} attempts "
            f"(last: {reasons[-1] if reasons else 'n/a'})"
        )

    def lines(self, path: Path) -> list[str]:
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
        """Append-only prefixes unchanged (growth is fine); mutable captures are not re-checked: they
        were required to be stable during the capture only."""
        growth = 0
        mutable = 0
        for e in self.entries.values():
            p: Path = e["path"]
            if e["kind"] == "mutable":
                mutable += 1
                continue
            if e["missing"]:
                continue
            if not p.exists():
                raise SnapshotIntegrityError(f"{p}: deleted during the audit")
            now = os.stat(p).st_size
            if now < e["size"]:
                raise SnapshotIntegrityError(f"{p}: truncated during the audit")
            with p.open("rb") as fh:
                again = fh.read(e["size"])
            if hashlib.sha256(again).hexdigest() != e["sha256"]:
                raise SnapshotIntegrityError(f"{p}: prefix modified during the audit")
            growth += now - e["size"]
        return {
            "files": len(self.entries),
            "append_only_files": len(self.entries) - mutable,
            "mutable_files_captured": mutable,
            "bytes_appended_during_audit": growth,
        }

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "path": str(e["path"]),
                "kind": e["kind"],
                "size": e["size"],
                "sha256": e["sha256"],
                "missing": e["missing"],
                "version": e.get("version"),
                "capture_attempts": e.get("attempts"),
            }
            for e in self.entries.values()
        ]


def _rec_ms(r: dict[str, Any]) -> int | None:
    """Journal record time (the hash-chained `ts`), in ms."""
    ts = r.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1000)
    except ValueError:
        return None


def at_cutoff(recs: list[dict[str, Any]], cutoff_ms: int) -> list[dict[str, Any]]:
    """Only records journaled at or before the fixed audit cutoff (records without a parseable
    `ts` are kept: they cannot be placed, and dropping them silently would hide data)."""
    out = []
    for r in recs:
        t = _rec_ms(r)
        if t is None or t <= cutoff_ms:
            out.append(r)
    return out


def chain_links_ok(recs: list[dict[str, Any]]) -> bool:
    """prev_hash continuity of the snapshot prefix (cheap link check; the full recompute is `verify_chain`)."""
    prev = None
    for r in recs:
        if prev is not None and r.get("prev_hash") != prev:
            return False
        prev = r.get("hash")
    return True


class RawTrades:
    def __init__(self, root: Path, snap: PrefixSnapshot) -> None:
        self.root, self.snap = root, snap
        self.cache: dict[str, list[tuple[int, float]]] = {}

    def _hour(self, ms: int) -> list[tuple[int, float]]:
        d = datetime.fromtimestamp(ms / 1000, tz=UTC)
        key = d.strftime("%Y-%m-%d/%H")
        if key in self.cache:
            return self.cache[key]
        rows: list[tuple[int, float]] = []
        for p in (
            self.root / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.jsonl",
            self.root / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.jsonl.gz",
        ):
            if not p.exists():
                continue
            if p.suffix == ".gz":
                try:
                    lines = (
                        gzip.decompress(self.snap.read(p))
                        .decode("utf-8", errors="replace")
                        .splitlines()
                    )
                except (OSError, EOFError):
                    continue
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
    def __init__(self, path: Path, snap: PrefixSnapshot) -> None:
        self.rows = sorted(
            (
                (int(r["open_ms"]), r)
                for r in snap.jsonl(path)
                if r.get("open_ms") is not None and int(r["open_ms"]) + MS_1M <= snap.cutoff_ms
            ),
            key=lambda x: x[0],
        )
        self.by_open = {t: r for t, r in self.rows}

    def between(self, t0: int, t1: int) -> list[dict[str, Any]]:
        return [r for t, r in self.rows if t0 <= t < t1]


def price_path(raw: RawTrades, bars: Bars, t0: int, t1: int) -> tuple[list[tuple[int, float]], str]:
    p = raw.between(t0, t1)
    if p:
        return p, "raw trades"
    pts: list[tuple[int, float]] = []
    for r in bars.between(t0, t1):
        t = int(r["open_ms"])
        # a 1m bar cannot order its own high and low: both are stamped at the bar open (ambiguous)
        pts += [(t, float(r["h"])), (t, float(r["l"])), (t + MS_1M - 1, float(r["c"]))]
    return pts, "1m bars (approximate)" if pts else "none"


# ----------------------------------------------------------------------------- structure labels (both sides)
def labels_both_sides(detail: dict[str, Any]) -> dict[str, dict[str, bool | None]]:
    """The frozen A/B/C structure for LONG and SHORT from the journaled sub-conditions."""
    rules = {
        "A": ("data_valid", "impulse_pullback", "struct15_aligned", "turn"),
        "B": ("data_valid", "compression", "near_boundary"),
        "C": ("data_valid", "sweep_recent", "rsi_exhausted"),
    }
    out: dict[str, dict[str, bool | None]] = {}
    for t, keys in rules.items():
        out[t] = {}
        for side in ("LONG", "SHORT"):
            d = (detail.get(t) or {}).get(side)
            out[t][side] = None if not isinstance(d, dict) else all(bool(d.get(k)) for k in keys)
    return out


def score_from_contrib(contrib: dict[str, Any]) -> float | None:
    avail = [g for g in GROUPS if _f(contrib.get(g)) is not None]
    if not avail:
        return None
    return 100.0 * sum(float(contrib[g]) for g in avail) / sum(WEIGHTS[g] for g in avail)


def b_trigger_from_indicators(ind: dict[str, Any], s: float) -> bool | None:
    """Family B trigger recomputed from the journaled indicators (close beyond hi60 / lo60 by <= 0.5 ATR5, rvol5 >= 1.2)."""
    c, a5, rv = _f(ind.get("close")), _f(ind.get("atr5")), _f(ind.get("rvol5"))
    lvl = _f(ind.get("hi60")) if s > 0 else _f(ind.get("lo60"))
    if None in (c, a5, rv, lvl):
        return None
    assert c is not None and a5 is not None and rv is not None and lvl is not None
    return bool(s * (c - lvl) > 0 and rv >= 1.2 and s * (c - lvl) <= 0.5 * a5)


# ----------------------------------------------------------------------------- per-trade analysis
def analyse_trade(
    t: dict[str, Any], raw: RawTrades, bars: Bars, outs: dict[int, dict[str, Any]], cutoff_ms: int
) -> dict[str, Any]:
    d = t.get("decision") or {}
    s = float(t["side"])
    side = "LONG" if s > 0 else "SHORT"
    ref, stop0 = float(t["entry_ref"]), float(t["stop0"])
    dist = abs(ref - stop0)
    contrib = d.get("contrib") or {}
    ind = d.get("indicators") or {}
    ctx = d.get("context") or {}
    micro = ctx.get("microstructure") or d.get("micro") or {}
    flow = ctx.get("flow") or {}
    fib = ctx.get("fib") or {}
    vw = ctx.get("vwap") or {}
    rng60 = (ctx.get("range") or {}).get("60m") or {}
    loc = ctx.get("location_atr") or {}
    ctl = d.get("control_twin") or {}
    tmpl = t.get("selected_template") or d.get("selected_template")
    labels = labels_both_sides(ctx.get("structure_detail") or {})
    own_labels = d.get("structure_labels") or {}
    strict = own_labels.get(str(tmpl))
    if strict is None:
        strict = (labels.get(str(tmpl)) or {}).get(side)
    entry_ms, exit_ms = int(t["entry_ms"]), int(t["exit_ms"])
    a: dict[str, Any] = {
        "signal_id": t["signal_id"],
        "decision_utc": _iso(int(t["decision_ms"])),
        "decision_ms": int(t["decision_ms"]),
        "hour_utc": datetime.fromtimestamp(int(t["decision_ms"]) / 1000, tz=UTC).hour,
        "side": side,
        "selected_template": tmpl,
        "score": _f(t.get("score_entry")) or _f(d.get("score")),
        "score_armed": _f(d.get("score_armed")),
        "armed_min_before_entry": ((int(t["decision_ms"]) - int(d["arm_since_ms"])) / MS_1M)
        if d.get("arm_since_ms")
        else None,
        "arm_episode_id": d.get("arm_episode_id"),
        "score_other_side": _f(d.get("score_short") if s > 0 else d.get("score_long")),
        "contrib": {g: _f(contrib.get(g)) for g in GROUPS},
        "contrib_other_side": {g: _f((d.get("contrib_other_side") or {}).get(g)) for g in GROUPS},
        "score_recomputed": score_from_contrib(contrib),
        "labels": labels,
        "own_side_labels": {k: own_labels.get(k) for k in TEMPLATES},
        "strict_gate": "PASS" if strict else ("FAIL" if strict is False else NA),
        "strict_failed_conditions": ctl.get("failed_conditions"),
        "control_reason": ctl.get("reason"),
        "control_would_take": ctl.get("would_take"),
        "twin_decision_same_bar": ctl.get("twin_decision"),
        "incremental": d.get("incremental"),
        "entry_ref": ref,
        "entry_fill": _f(t.get("entry_fill")),
        "stop0": stop0,
        "stop_final": _f(t.get("stop")),
        "stop_pct": _f(t.get("stop_pct")),
        "stop_note": d.get("stop_note"),
        "stop_floored": "FLOORED" in str(d.get("stop_note") or ""),
        "structural_stop_pct": (abs(ref - float(d["structural_stop"])) / ref * 100)
        if _f(d.get("structural_stop")) is not None
        else None,
        "tp1": _f(t.get("tp1")),
        "tp2": _f(t.get("tp2")),
        "qty": _f(t.get("qty")),
        "leverage": _f(t.get("leverage")),
        "risk_usdt": _f(t.get("risk_usdt")),
        # ---- entry context
        "rsi7": _f(ind.get("rsi7")),
        "rsi14": _f(ind.get("rsi14")),
        "adx14": _f(ind.get("adx14")),
        "atr5_bps": (float(ind["atr5"]) / ref * 1e4) if _f(ind.get("atr5")) else None,
        "vol_tercile": _f(ind.get("vol_tercile")),
        "struct15": _f(ind.get("struct15")),
        "ema21_slope": _f(ind.get("ema21_slope")),
        "vwap_dist_atr": _f(vw.get("dist_atr")) if vw else _f(ind.get("dist_vwap")),
        "vwap_dist_bps": _f(vw.get("dist_bps")),
        "vwap_slope_atr": _f(vw.get("slope_atr")) if vw else _f(ind.get("vwap_slope")),
        "range60_position": _f(rng60.get("position")),
        "range60_width_bps": _f(rng60.get("width_bps")),
        "to_hi60_atr": _f(loc.get("to_hi60")),
        "to_lo60_atr": _f(loc.get("to_lo60")),
        "imb5": _f(flow.get("imb5")) if flow else _f(ind.get("imb5")),
        "imb15": _f(flow.get("imb15")) if flow else _f(ind.get("imb15")),
        "delta5": _f(flow.get("delta5")) if flow else _f(ind.get("delta5")),
        "delta15": _f(flow.get("delta15")) if flow else _f(ind.get("delta15")),
        "rvol5": _f(flow.get("rvol5")) if flow else _f(ind.get("rvol5")),
        "depth_imb": _f(micro.get("depth_imb")),
        "ob_imb_10": _f(micro.get("imb_10")),
        "ob_imb_30": _f(micro.get("imb_30")),
        "ob_imb_60": _f(micro.get("imb_60")),
        "delta_30s": _f(micro.get("delta_30")),
        "absorption_30": _f(micro.get("absorption_30")),
        "liq_burst_pct_120": _f(micro.get("liq_burst_pct_120")),
        "liq_imb_120": _f(micro.get("liq_imb_120")),
        "spread_bps": _f(micro.get("spread_bps")),
        "oi": _f(ind.get("oi")),
        "oi_chg_15m": _f(ind.get("oi_chg_15m")),
        "oi_chg_1h": _f(ind.get("oi_chg_1h")),
        "funding": _f(ind.get("funding")),
        "fib_available": fib.get("available"),
        "fib_bucket_atr": fib.get("bucket_atr"),
        "fib_direction": fib.get("direction"),
        "fib_nearest_ratio": fib.get("nearest_ratio"),
        "fib_nearest_dist_bps": _f(fib.get("nearest_dist_bps")),
        "fib_retrace_frac": _f(fib.get("retrace_frac")),
        "fib_leg_bps": _f(fib.get("leg_bps")),
        "fib_swing_rows": (fib.get("swing_high_row"), fib.get("swing_low_row")),
        "fib_leg_with_side": (
            None if not fib.get("direction") else ((fib.get("direction") == "UP") == (s > 0))
        ),
        # ---- outcome (frozen engine accounting)
        "exit_utc": _iso(exit_ms),
        "exit_reason": t.get("exit_reason"),
        "hold_min": _f(t.get("hold_min")),
        "net_r": _f(t.get("net_r")),
        "gross_r": _f(t.get("gross_r")),
        "cost_r": _f(t.get("cost_r")),
        "net_usd": _f(t.get("net_usd")),
        "gross_usd": _f(t.get("gross_usd")),
        "fee_usd": _f(t.get("fee_usd")),
        "slip_usd": _f(t.get("slip_usd")),
        "funding_usd": _f(t.get("funding_usd")),
        "mfe_r_engine": _f(t.get("mfe_r")),
        "mae_r_engine": _f(t.get("mae_r")),
        "tp1_done": bool(t.get("tp1_done")),
        "tp2_done": bool(t.get("tp2_done")),
        "legs": [
            {
                "kind": lg.get("kind"),
                "frac": lg.get("frac"),
                "ref": lg.get("ref"),
                "min": (int(lg["t_ms"]) - entry_ms) / MS_1M if lg.get("t_ms") else None,
            }
            for lg in (t.get("legs") or [])
        ],
    }
    # ---- price path during the trade (decision close -> exit) in R of the plan geometry
    a["r_from_ref"] = {}
    path, src = price_path(raw, bars, entry_ms, exit_ms)
    a["path_source"] = src
    if path and dist > 0:
        r_path = [(tm, s * (px - ref) / dist) for tm, px in path]
        fav = max(r_path, key=lambda x: x[1])
        adv = min(r_path, key=lambda x: x[1])
        a["mfe_r_path"], a["mae_r_path"] = fav[1], adv[1]
        a["min_to_mfe"], a["min_to_mae"] = (fav[0] - entry_ms) / MS_1M, (adv[0] - entry_ms) / MS_1M
        first_hit: dict[str, float | None] = {}
        for lv in LEVELS_R:
            hit = next((x for x in r_path if x[1] >= lv), None)
            first_hit[f"+{lv}R"] = (hit[0] - entry_ms) / MS_1M if hit else None
        for lv in (-0.5, -1.0):
            hit = next((x for x in r_path if x[1] <= lv), None)
            first_hit[f"{lv}R"] = (hit[0] - entry_ms) / MS_1M if hit else None
        a["first_touch_min"] = first_hit
        stop_or_exit = (
            first_hit["-1.0R"] if first_hit["-1.0R"] is not None else (exit_ms - entry_ms) / MS_1M
        )
        reached: dict[str, bool] = {}
        for lv in LEVELS_R:
            fh = first_hit[f"+{lv}R"]
            reached[f"+{lv}R"] = fh is not None and fh <= stop_or_exit
        a["reached_before_loss"] = reached
        a["same_bar_ambiguity"] = src.startswith("1m") and any(
            v is not None and first_hit["-1.0R"] is not None and v == first_hit["-1.0R"]
            for v in (first_hit["+0.25R"], first_hit["+0.5R"])
        )
    else:
        a["mfe_r_path"] = a["mae_r_path"] = a["min_to_mfe"] = a["min_to_mae"] = None
        a["first_touch_min"], a["reached_before_loss"], a["same_bar_ambiguity"] = {}, {}, None
    # ---- after the exit: did the plan's targets print within 60 min (missed move / geometry)?
    post_end = min(exit_ms + 60 * MS_1M, cutoff_ms)
    a["post_exit_window_complete"] = exit_ms + 60 * MS_1M <= cutoff_ms
    post, _ = price_path(raw, bars, exit_ms + 1, post_end)
    if post and dist > 0:
        best = max(s * (px - ref) / dist for _, px in post)
        worst = min(s * (px - ref) / dist for _, px in post)
        a["post_exit_60m_best_r_from_ref"], a["post_exit_60m_worst_r_from_ref"] = best, worst
        a["post_exit_tp1_touched"], a["post_exit_tp2_touched"] = best >= TP1_R, best >= TP2_R
    else:
        a["post_exit_60m_best_r_from_ref"] = a["post_exit_60m_worst_r_from_ref"] = None
        a["post_exit_tp1_touched"] = a["post_exit_tp2_touched"] = None
    a["outcomes_from_decision_bps"] = {h: _f(o.get("return_bps")) for h, o in sorted(outs.items())}
    a["outcomes_mfe_bps"] = {h: _f(o.get("mfe_bps")) for h, o in sorted(outs.items())}
    # ---- B trigger recomputed (template order bias check) + contradictions
    a["b_trigger_also_true"] = b_trigger_from_indicators(ind, s)
    opp = "SHORT" if s > 0 else "LONG"
    a["opposite_side_labels_true"] = [k for k in TEMPLATES if (labels.get(k) or {}).get(opp)]
    contra = {
        "struct15": _sign_word(a["struct15"], s),
        "ema21_slope": _sign_word(a["ema21_slope"], s),
        "vwap_slope": _sign_word(a["vwap_slope_atr"], s),
        "vwap_side": _sign_word(a["vwap_dist_atr"], s),
        "delta15": _sign_word(a["delta15"], s),
        "imb5": _sign_word(a["imb5"], s),
        "depth_imb": _sign_word(a["depth_imb"], s),
        "ob_imb_30": _sign_word(a["ob_imb_30"], s),
        "oi_chg_15m_x_side": _sign_word(a["oi_chg_15m"], s),
    }
    a["directional_alignment"] = contra
    a["n_against"] = sum(1 for v in contra.values() if v == "AGAINST")
    a["null_indicator_fields"] = sorted(k for k, v in ind.items() if v is None)
    a["null_micro_fields"] = sorted(k for k, v in micro.items() if v is None)
    a["null_groups"] = [g for g in GROUPS if a["contrib"][g] is None]
    a["score_mismatch"] = (
        a["score_recomputed"] is not None
        and a["score"] is not None
        and abs(a["score_recomputed"] - a["score"]) > 1e-6
    )
    bar = bars.by_open.get(int(t["decision_ms"]) - MS_1M)
    a["decision_bar_src"] = (bar or {}).get("src", "live" if bar else None)
    a["decision_close_matches_bar"] = (abs(float(bar["c"]) - ref) < 1e-6) if bar else None
    pre = bars.between(int(t["decision_ms"]) - 60 * MS_1M, int(t["decision_ms"]))
    a["pre60_non_live_bars"] = sum(1 for r in pre if r.get("src") not in (None, "live"))
    a["pre60_bars_present"] = len(pre)
    # ---- loss class
    a["loss_class"], a["loss_flags"] = classify(a)
    return a


def classify(a: dict[str, Any]) -> tuple[str | None, list[str]]:
    net, gross, mfe = a.get("net_r"), a.get("gross_r"), a.get("mfe_r_engine")
    if net is None or net > 0:
        return None, []
    mfe = mfe if mfe is not None else (a.get("mfe_r_path") or 0.0)
    flags: list[str] = []
    if gross is not None and gross > 0 >= net:
        primary = "E"  # gross positive: costs made the loss
    elif mfe >= 0.5:
        primary = "C"
    elif mfe >= 0.15:
        primary = "B"
    else:
        primary = "A"
    post = a.get("post_exit_60m_best_r_from_ref")
    if post is not None and post >= TP1_R:
        flags.append(
            "D: TP1 level printed within 60 min after the exit (stop / exit geometry or timing)"
        )
    if a.get("stop_floored") and a.get("exit_reason") in ("STOP", "BE", "TRAIL"):
        flags.append("D: stop was FLOORED to 0.35 % (structural stop closer than the floor)")
    if a.get("exit_reason") in ("TIME", "STALL") and (post or -9) >= 0.3:
        flags.append("D: time / stall exit with a favourable move afterwards")
    if a.get("cost_r") is not None and gross is not None and abs(gross) < a["cost_r"]:
        flags.append("E: |gross R| smaller than the cost R")
    return primary, flags


# ----------------------------------------------------------------------------- aggregation
def agg(rows: list[dict[str, Any]], key: Any) -> dict[str, dict[str, Any]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        k = key(r)
        g[str(k) if k is not None else NA].append(r)
    out = {}
    for k, rs in sorted(g.items()):
        out[k] = summary(rs)
    return out


def summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
    net = [float(r["net_r"]) for r in rs if r.get("net_r") is not None]
    usd = [float(r["net_usd"]) for r in rs if r.get("net_usd") is not None]
    gross = [float(r["gross_r"]) for r in rs if r.get("gross_r") is not None]
    cost = [float(r["cost_r"]) for r in rs if r.get("cost_r") is not None]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    return {
        "n": len(rs),
        "wins": len(wins),
        "win_rate": (len(wins) / len(net)) if net else None,
        "net_r_sum": sum(net),
        "net_r_mean": statistics.fmean(net) if net else None,
        "gross_r_mean": statistics.fmean(gross) if gross else None,
        "cost_r_mean": statistics.fmean(cost) if cost else None,
        "net_usd": sum(usd),
        "pf": (sum(wins) / -sum(losses))
        if losses and sum(losses) < 0
        else (math.inf if wins else None),
        "mfe_r_mean": statistics.fmean(
            [float(r["mfe_r_engine"]) for r in rs if r.get("mfe_r_engine") is not None]
        )
        if rs
        else None,
    }


def bucket_score(x: float | None) -> str:
    if x is None:
        return NA
    if x < 75:
        return "70-75"
    if x < 80:
        return "75-80"
    if x < 85:
        return "80-85"
    return "85+"


def bucket_adx(x: float | None) -> str:
    return NA if x is None else ("<20" if x < 20 else "20-30" if x < 30 else ">=30")


def bucket_hour(h: int) -> str:
    return "00-08 UTC" if h < 8 else "08-16 UTC" if h < 16 else "16-24 UTC"


def bucket_vwap(r: dict[str, Any]) -> str:
    d = r.get("vwap_dist_atr")
    if d is None:
        return NA
    s = 1.0 if r["side"] == "LONG" else -1.0
    sd = s * d
    mag = "<0.5 ATR" if abs(sd) < 0.5 else "0.5-1.5 ATR" if abs(sd) < 1.5 else ">1.5 ATR"
    return f"{'WITH side' if sd > 0 else 'AGAINST side'} {mag}"


def bucket_trend(r: dict[str, Any]) -> str:
    x = r["contrib"].get("trend")
    return (
        NA
        if x is None
        else ("trend >= 15 (strong)" if x >= 15 else "trend 5-15" if x >= 5 else "trend < 5")
    )


def bucket_momentum(r: dict[str, Any]) -> str:
    x = r["contrib"].get("momentum")
    return (
        NA
        if x is None
        else ("momentum >= 12" if x >= 12 else "momentum 6-12" if x >= 6 else "momentum < 6")
    )


def terciles(rows: list[dict[str, Any]], key: Any) -> dict[str, dict[str, Any]]:
    vals = sorted((key(r), i) for i, r in enumerate(rows) if key(r) is not None)
    if len(vals) < 3:
        return {}
    n = len(vals)
    lo, hi = vals[n // 3 - 1][0], vals[2 * n // 3][0]

    def b(r: dict[str, Any]) -> str:
        x = key(r)
        return (
            NA
            if x is None
            else (f"T1 (<= {lo:.2f})" if x <= lo else f"T3 (>= {hi:.2f})" if x >= hi else "T2")
        )

    return agg(rows, b)


# ----------------------------------------------------------------------------- counterfactuals
def counterfactuals(
    rows: list[dict[str, Any]],
    nearmiss: list[dict[str, Any]],
    outcomes: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    base = summary(rows)
    strict_only = summary([r for r in rows if r["strict_gate"] == "PASS"])
    fail_only = summary([r for r in rows if r["strict_gate"] == "FAIL"])
    soft: dict[str, Any] = {}
    for delta in (5.0, 10.0):
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for r in rows:
            if r.get("score") is None:
                continue
            adj = r["score"] + (delta if r["strict_gate"] == "PASS" else -delta)
            (kept if adj >= ENTRY else removed).append(r)
        adds = []
        for nm in nearmiss:
            sc = _f(nm.get("score"))
            tmpl = nm.get("selected_template")
            lab = (nm.get("structure_labels") or {}).get(tmpl)
            if sc is None or not lab:
                continue
            if sc + delta >= ENTRY:
                side = nm.get("side")
                rid = f"V11LWN-{nm.get('ts_ms')}-{str(side)[0]}"
                o = outcomes.get(rid, {})
                adds.append(
                    {
                        "ts": nm.get("ts"),
                        "side": side,
                        "selected_template": tmpl,
                        "score": sc,
                        "ret_30m_bps": _f((o.get(30) or {}).get("return_bps")),
                        "ret_60m_bps": _f((o.get(60) or {}).get("return_bps")),
                        "mfe_60m_bps": _f((o.get(60) or {}).get("mfe_bps")),
                        "mae_60m_bps": _f((o.get(60) or {}).get("mae_bps")),
                    }
                )
        r60 = [x["ret_60m_bps"] for x in adds if x["ret_60m_bps"] is not None]
        soft[f"+/-{int(delta)}"] = {
            "executed_kept": summary(kept),
            "executed_removed": summary(removed),
            "removed_ids": [r["signal_id"] for r in removed],
            "near_miss_additions_n": len(adds),
            "near_miss_additions_60m_mean_bps": statistics.fmean(r60) if r60 else None,
            "near_miss_additions_60m_positive_share": (sum(1 for x in r60 if x > 0) / len(r60))
            if r60
            else None,
            "near_miss_additions": adds,
            "caveat": "additions are near misses whose first-triggered template passed the strict structure; their stop validity, sizing and position-open blocking were never evaluated, so only the 60-min directional outcome from the decision close is shown (NOT a trade PnL)",
        }
    return {
        "baseline": base,
        "strict_only_subset": strict_only,
        "strict_fail_subset": fail_only,
        "soft_filter": soft,
    }


# ----------------------------------------------------------------------------- twin comparison + pathologies
def twin_section(
    rows: list[dict[str, Any]], twin: list[dict[str, Any]], lite_trades: list[dict[str, Any]]
) -> dict[str, Any]:
    tw_dec = [r["data"] for r in twin if r.get("kind") in ("TWIN_EXECUTED", "TWIN_BLOCKED")]
    tw_closed = [r["data"] for r in twin if r.get("kind") == "TWIN_CLOSED"]
    in_pos = [(int(t["entry_ms"]), int(t["exit_ms"]), t["signal_id"]) for t in lite_trades]
    tw_rows = []
    for d in tw_dec:
        dm = int(d.get("decision_ms") or 0)
        lite_open = next((sid for a, b, sid in in_pos if a <= dm < b), None)
        same = next((r["signal_id"] for r in rows if r["decision_ms"] == dm), None)
        tw_rows.append(
            {
                "ts": _iso(dm),
                "family": d.get("family"),
                "side": d.get("side"),
                "action": d.get("action"),
                "blockers": d.get("blockers"),
                "score": _f(d.get("score")),
                "lite_same_bar_trade": same,
                "lite_in_position": lite_open,
            }
        )
    tw_perf = [
        {
            "ts": _iso(int(t["decision_ms"])),
            "family": t.get("family"),
            "side": "LONG" if int(t["side"]) > 0 else "SHORT",
            "exit_reason": t.get("exit_reason"),
            "net_r": _f(t.get("net_r")),
            "net_usd": _f(t.get("net_usd")),
            "mfe_r": _f(t.get("mfe_r")),
        }
        for t in tw_closed
    ]
    return {
        "twin_decisions": tw_rows,
        "twin_closed_trades": tw_perf,
        "twin_net_usd": sum(x["net_usd"] or 0.0 for x in tw_perf),
        "lite_trades_twin_would_take": sum(1 for r in rows if r.get("control_would_take")),
        "lite_trades_twin_would_reject": sum(
            1 for r in rows if r.get("control_would_take") is False
        ),
        "twin_executions_while_lite_in_position": sum(
            1
            for x in tw_rows
            if x["action"] == "EXECUTED" and x["lite_in_position"] and not x["lite_same_bar_trade"]
        ),
        "lite_reject_reasons": dict(
            Counter(str(r.get("control_reason")).split("|")[0].split(":")[0] for r in rows)
        ),
    }


def pathologies(rows: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
    rows_s = sorted(rows, key=lambda r: r["decision_ms"])
    repeats = []
    for p, q in itertools.pairwise(rows_s):
        gap = (q["decision_ms"] - (p["decision_ms"] + (p["hold_min"] or 0) * MS_1M)) / MS_1M
        same_swing = p.get("fib_swing_rows") == q.get("fib_swing_rows") and p.get(
            "fib_swing_rows"
        ) not in (None, (None, None))
        if p["side"] == q["side"] and (
            gap <= 45 or same_swing or p.get("arm_episode_id") == q.get("arm_episode_id")
        ):
            repeats.append(
                {
                    "first": p["signal_id"],
                    "second": q["signal_id"],
                    "same_side": True,
                    "min_after_previous_exit": gap,
                    "same_fib_swing": same_swing,
                    "same_arm_episode": p.get("arm_episode_id") == q.get("arm_episode_id"),
                    "second_net_r": q["net_r"],
                }
            )
    tmpl = Counter(r["selected_template"] for r in rows)
    b_also = [
        r["signal_id"]
        for r in rows
        if r["selected_template"] != "B" and r.get("b_trigger_also_true")
    ]
    blocked: Counter[str] = Counter()
    for sg in signals:
        d = sg.get("data") or {}
        if sg.get("kind") == "SIGNAL_BLOCKED":
            for b in d.get("blockers") or []:
                blocked[b] += 1
    return {
        "repeated_same_setup_pairs": repeats,
        "selected_template_counts": dict(tmpl),
        "non_B_selections_where_B_trigger_was_also_true": b_also,
        "template_order_note": "the frozen decision loop evaluates A LONG, A SHORT, B LONG, B SHORT, C LONG, C SHORT and takes the first template whose trigger fires; A's trigger (close beyond the 3-bar high / low) is the loosest, so A wins most ties by construction",
        "score_mismatches": [r["signal_id"] for r in rows if r.get("score_mismatch")],
        "null_groups_at_decision": dict(Counter(g for r in rows for g in r["null_groups"])),
        "null_micro_fields_at_decision": dict(
            Counter(f for r in rows for f in r["null_micro_fields"])
        ),
        "decision_close_mismatch_bar": [
            r["signal_id"] for r in rows if r.get("decision_close_matches_bar") is False
        ],
        "decision_bar_src": dict(Counter(str(r.get("decision_bar_src")) for r in rows)),
        "trades_with_non_live_bars_in_prior_60m": [
            (r["signal_id"], r["pre60_non_live_bars"]) for r in rows if r.get("pre60_non_live_bars")
        ],
        "opposite_side_label_true_at_entry": [
            (r["signal_id"], r["opposite_side_labels_true"])
            for r in rows
            if r["opposite_side_labels_true"]
        ],
        "contradiction_count_distribution": dict(Counter(r["n_against"] for r in rows)),
        "both_sides_high_score": [
            (r["signal_id"], r["score"], r["score_other_side"])
            for r in rows
            if (r.get("score_other_side") or 0) >= ARMED
        ],
        "blocked_decisions_by_reason": dict(blocked),
        "armed_minutes_before_entry": [(r["signal_id"], r["armed_min_before_entry"]) for r in rows],
    }


# ----------------------------------------------------------------------------- report
def run_audit(
    root: Path, out_dir: Path, cutoff_ms: int | None = None
) -> tuple[Path, Path, dict[str, Any]]:
    cutoff = cutoff_ms if cutoff_ms is not None else int(time.time() * 1000) - MS_1M
    snap = PrefixSnapshot(cutoff)
    J = root / "journals"  # noqa: N806
    # the runner's state.json is rewritten atomically every tick: one consistent capture, first
    state = json.loads(snap.read_mutable(root / "state.json") or b"{}")
    # append-only journals: byte-prefix snapshot, then only records journaled at or before the cutoff
    trades_j = at_cutoff(snap.jsonl(J / "shadow_trades.jsonl"), cutoff)
    signals = at_cutoff(snap.jsonl(J / "signals.jsonl"), cutoff)
    twin = at_cutoff(snap.jsonl(J / "control_twin.jsonl"), cutoff)
    nearmiss = [
        r["data"]
        for r in at_cutoff(snap.jsonl(J / "nearmiss.jsonl"), cutoff)
        if r.get("kind") == "NEAR_MISS"
    ]
    outs_raw = at_cutoff(snap.jsonl(J / "outcomes.jsonl"), cutoff)
    chains = {  # link continuity of the whole captured prefix (not of the time-filtered subset)
        n: chain_links_ok(snap.jsonl(J / f"{n}.jsonl"))
        for n in ("shadow_trades", "signals", "control_twin", "nearmiss", "outcomes", "episodes")
    }
    outcomes: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for r in outs_raw:
        d = r.get("data") or {}
        if d.get("id") and d.get("horizon_min") is not None:
            outcomes[str(d["id"])][int(d["horizon_min"])] = d
    bars = Bars(root / "derived" / "bars_1m.jsonl", snap)
    raw = RawTrades(root / "raw" / "BTCUSDT", snap)
    closed = [
        r["data"]
        for r in trades_j
        if r.get("kind") == "SHADOW_CLOSED" and int(r["data"].get("exit_ms") or 0) <= cutoff
    ]
    closed.sort(key=lambda t: int(t["decision_ms"]))
    rows = [
        analyse_trade(t, raw, bars, outcomes.get(str(t["signal_id"]), {}), cutoff) for t in closed
    ]
    losses = [r for r in rows if r.get("net_r") is not None and r["net_r"] <= 0]
    rep: dict[str, Any] = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "root": str(root),
        "cutoff_utc": _iso(cutoff),
        "observation_start_utc": _iso(state.get("observation_start_ms")),
        "state_counters": state.get("counters"),
        "state_totals": {k: state.get(k) for k in ("trades", "wins", "losses", "cum_net_usd")},
        "open_position_at_cutoff": state.get("position"),
        "journal_chain_links_ok": chains,
        "n_closed": len(rows),
        "trades": rows,
        "overall": summary(rows),
        "loss_classes": agg(losses, lambda r: r["loss_class"]),
        "loss_flags": dict(
            Counter(
                f.split(":")[0] + ":" + f.split(":")[1] for r in losses for f in r["loss_flags"]
            )
        ),
        "by_template": agg(rows, lambda r: r["selected_template"]),
        "by_side": agg(rows, lambda r: r["side"]),
        "by_strict_gate": agg(rows, lambda r: r["strict_gate"]),
        "by_score_bucket": agg(rows, lambda r: bucket_score(r["score"])),
        "by_micro_tercile": terciles(rows, lambda r: r["contrib"].get("microstructure")),
        "by_trend": agg(rows, bucket_trend),
        "by_momentum": agg(rows, bucket_momentum),
        "by_adx": agg(rows, lambda r: bucket_adx(r["adx14"])),
        "by_vol_tercile": agg(rows, lambda r: r["vol_tercile"]),
        "by_hour": agg(rows, lambda r: bucket_hour(r["hour_utc"])),
        "by_vwap": agg(rows, bucket_vwap),
        "by_fib_bucket": agg(rows, lambda r: r["fib_bucket_atr"]),
        "by_fib_leg_direction": agg(
            rows,
            lambda r: (
                None
                if r["fib_leg_with_side"] is None
                else ("leg WITH side" if r["fib_leg_with_side"] else "leg AGAINST side")
            ),
        ),
        "by_exit_reason": agg(rows, lambda r: r["exit_reason"]),
        "by_stop_floored": agg(
            rows, lambda r: "FLOORED 0.35%" if r["stop_floored"] else "structural"
        ),
        "by_incremental": agg(
            rows, lambda r: "incremental (twin rejects)" if r["incremental"] else "twin would take"
        ),
        "cost_decomposition": {
            "gross_r_sum": sum(r["gross_r"] or 0 for r in rows),
            "cost_r_sum": sum(r["cost_r"] or 0 for r in rows),
            "net_r_sum": sum(r["net_r"] or 0 for r in rows),
            "fees_usd": sum(r["fee_usd"] or 0 for r in rows),
            "slip_usd": sum(r["slip_usd"] or 0 for r in rows),
            "funding_usd": sum(r["funding_usd"] or 0 for r in rows),
            "trades_gross_positive_net_negative": [
                r["signal_id"] for r in rows if (r["gross_r"] or 0) > 0 >= (r["net_r"] or 0)
            ],
        },
        "path_stats": {
            "reached_+0.25R_before_loss": sum(
                1 for r in losses if (r.get("reached_before_loss") or {}).get("+0.25R")
            ),
            "reached_+0.5R_before_loss": sum(
                1 for r in losses if (r.get("reached_before_loss") or {}).get("+0.5R")
            ),
            "reached_+1R_before_loss": sum(
                1 for r in losses if (r.get("reached_before_loss") or {}).get("+1.0R")
            ),
            "losses_n": len(losses),
            "post_exit_tp1_touched_losses": sum(
                1 for r in losses if r.get("post_exit_tp1_touched")
            ),
            "median_min_to_mae_losses": statistics.median(
                [r["min_to_mae"] for r in losses if r.get("min_to_mae") is not None]
            )
            if any(r.get("min_to_mae") is not None for r in losses)
            else None,
            "median_min_to_mfe_losses": statistics.median(
                [r["min_to_mfe"] for r in losses if r.get("min_to_mfe") is not None]
            )
            if any(r.get("min_to_mfe") is not None for r in losses)
            else None,
            "mean_mfe_r_losses": statistics.fmean(
                [r["mfe_r_engine"] for r in losses if r.get("mfe_r_engine") is not None]
            )
            if losses
            else None,
            "mean_mae_r_wins": statistics.fmean(
                [
                    r["mae_r_engine"]
                    for r in rows
                    if (r.get("net_r") or 0) > 0 and r.get("mae_r_engine") is not None
                ]
            )
            if any((r.get("net_r") or 0) > 0 for r in rows)
            else None,
        },
        "counterfactuals": counterfactuals(rows, nearmiss, outcomes),
        "control_twin": twin_section(rows, twin, closed),
        "pathologies": pathologies(rows, signals),
        "snapshot": {"cutoff_ms": cutoff, "verify": snap.verify(), "files": snap.manifest()},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    js, md = out_dir / f"forensic_{run_id}.json", out_dir / f"forensic_{run_id}.md"
    js.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    md.write_text(render(rep))
    return md, js, rep


def _tbl(title: str, d: dict[str, dict[str, Any]]) -> list[str]:
    out = [
        f"### {title}",
        "",
        "| bucket | n | wins | win rate | net R sum | net R mean | gross R mean | cost R mean | net USDT | PF | MFE R mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in d.items():
        out.append(
            f"| {k} | {v['n']} | {v['wins']} | {_v(v['win_rate'])} | {_v(v['net_r_sum'])} | {_v(v['net_r_mean'])} | {_v(v['gross_r_mean'])} | {_v(v['cost_r_mean'])} | {_v(v['net_usd'])} | {_v(v['pf'])} | {_v(v['mfe_r_mean'])} |"
        )
    return [*out, ""]


def render(rep: dict[str, Any]) -> str:
    o = [
        "# V11-LITE-WARM forward SHADOW forensic audit (read-only)",
        "",
        f"Generated {rep['generated_utc']} · cutoff {rep['cutoff_utc']} · observation start {rep['observation_start_utc']} · root `{rep['root']}`",
        f"state: {rep['state_totals']} · counters {rep['state_counters']} · open position at cutoff: {'yes' if rep['open_position_at_cutoff'] else 'no'} · journal chain links {rep['journal_chain_links_ok']}",
        "",
    ]
    ov = rep["overall"]
    o += [
        f"**{rep['n_closed']} closed trades · {ov['wins']} wins · net {_v(ov['net_r_sum'])} R / {_v(ov['net_usd'])} USDT · mean net {_v(ov['net_r_mean'])} R (gross {_v(ov['gross_r_mean'])} R, cost {_v(ov['cost_r_mean'])} R) · PF {_v(ov['pf'])}**",
        "",
    ]
    o += ["## 1. Trades", ""]
    for i, r in enumerate(rep["trades"], 1):
        c = r["contrib"]
        lab = r["labels"]
        o += [
            f"### {i}. {r['decision_utc']} UTC · {r['side']} · template {r['selected_template']} · score {_v(r['score'], 1)} · strict gate **{r['strict_gate']}** · net {_v(r['net_r'])} R ({_v(r['net_usd'])} USDT) · {r['exit_reason']} · class {r['loss_class'] or 'WIN'}",
            "",
        ]
        o += [
            "| | |",
            "|---|---|",
            f"| signal | `{r['signal_id']}` · arm episode `{r['arm_episode_id']}` · armed {_v(r['armed_min_before_entry'], 0)} min before entry (score at arm {_v(r['score_armed'], 1)}) · incremental {_v(r['incremental'])} |",
            f"| contributions | trend {_v(c['trend'], 1)} · momentum {_v(c['momentum'], 1)} · volatility {_v(c['volatility'], 1)} · volume {_v(c['volume'], 1)} · location {_v(c['location'], 1)} · derivatives {_v(c['derivatives'], 1)} · microstructure {_v(c['microstructure'], 1)} · recomputed {_v(r['score_recomputed'], 2)} · other side score {_v(r['score_other_side'], 1)} |",
            f"| frozen structure labels | A L/S {_v(lab['A']['LONG'])}/{_v(lab['A']['SHORT'])} · B L/S {_v(lab['B']['LONG'])}/{_v(lab['B']['SHORT'])} · C L/S {_v(lab['C']['LONG'])}/{_v(lab['C']['SHORT'])} · selected {r['selected_template']} strict {r['strict_gate']} (failed: {r['strict_failed_conditions']}) · control: {r['control_reason']} |",
            f"| geometry | entry ref {_v(r['entry_ref'], 1)} · stop0 {_v(r['stop0'], 1)} ({_v(r['stop_pct'])} %, {r['stop_note']}; structural {_v(r['structural_stop_pct'])} %) · TP1 {_v(r['tp1'], 1)} · TP2 {_v(r['tp2'], 1)} · lev {_v(r['leverage'], 0)} |",
            f"| trend / momentum | RSI7 {_v(r['rsi7'], 1)} · RSI14 {_v(r['rsi14'], 1)} · ADX14 {_v(r['adx14'], 1)} · struct15 {_v(r['struct15'], 0)} · EMA21 slope {_v(r['ema21_slope'], 3)} |",
            f"| volatility | ATR5 {_v(r['atr5_bps'], 1)} bps · vol tercile {_v(r['vol_tercile'], 0)} · spread {_v(r['spread_bps'], 3)} bps |",
            f"| location | VWAP dist {_v(r['vwap_dist_atr'])} ATR ({_v(r['vwap_dist_bps'], 1)} bps, slope {_v(r['vwap_slope_atr'], 3)}) · 60m range pos {_v(r['range60_position'])} (width {_v(r['range60_width_bps'], 1)} bps) · to hi60 {_v(r['to_hi60_atr'])} ATR · to lo60 {_v(r['to_lo60_atr'])} ATR |",
            f"| flow | imb5 {_v(r['imb5'])} · imb15 {_v(r['imb15'])} · delta5 {_v(r['delta5'], 1)} · delta15 {_v(r['delta15'], 1)} · rvol5 {_v(r['rvol5'])} |",
            f"| order book / liq | depth imb {_v(r['depth_imb'])} · OB imb 10/30/60 s {_v(r['ob_imb_10'])}/{_v(r['ob_imb_30'])}/{_v(r['ob_imb_60'])} · delta30s {_v(r['delta_30s'], 1)} · absorption {_v(r['absorption_30'])} · liq burst pct {_v(r['liq_burst_pct_120'])} · liq imb {_v(r['liq_imb_120'])} |",
            f"| derivatives | OI {_v(r['oi'], 0)} · OI chg 15m {_v(r['oi_chg_15m'], 4)} · OI chg 1h {_v(r['oi_chg_1h'], 4)} · funding {_v(r['funding'], 6)} |",
            f"| Fibonacci | available {_v(r['fib_available'])} · bucket {r['fib_bucket_atr']} · leg {r['fib_direction']} ({'WITH' if r['fib_leg_with_side'] else 'AGAINST' if r['fib_leg_with_side'] is False else NA} side, {_v(r['fib_leg_bps'], 1)} bps) · nearest {r['fib_nearest_ratio']} at {_v(r['fib_nearest_dist_bps'], 1)} bps · retrace {_v(r['fib_retrace_frac'])} |",
            f"| alignment | {r['directional_alignment']} · against count {r['n_against']} · opposite-side labels true {r['opposite_side_labels_true']} · B trigger also true {_v(r['b_trigger_also_true'])} |",
            f"| outcome | hold {_v(r['hold_min'], 0)} min · gross {_v(r['gross_r'])} R · cost {_v(r['cost_r'])} R · net {_v(r['net_r'])} R / {_v(r['net_usd'])} USDT · legs {[(lg['kind'], lg['frac'], _v(lg['min'], 0)) for lg in r['legs']]} |",
            f"| excursion | MFE {_v(r['mfe_r_engine'])} R (path {_v(r['mfe_r_path'])}, at {_v(r['min_to_mfe'], 1)} min) · MAE {_v(r['mae_r_engine'])} R (path {_v(r['mae_r_path'])}, at {_v(r['min_to_mae'], 1)} min) · first touch {r['first_touch_min']} · reached before loss {r['reached_before_loss']} · path {r['path_source']} |",
            f"| after exit (60 min) | best {_v(r['post_exit_60m_best_r_from_ref'])} R / worst {_v(r['post_exit_60m_worst_r_from_ref'])} R from entry ref · TP1 printed {_v(r['post_exit_tp1_touched'])} · TP2 {_v(r['post_exit_tp2_touched'])} · window complete {_v(r['post_exit_window_complete'])} · outcomes from decision (bps) {r['outcomes_from_decision_bps']} |",
            f"| data quality | decision bar src {r['decision_bar_src']} · close matches bar {_v(r['decision_close_matches_bar'])} · non-live bars in prior 60 {r['pre60_non_live_bars']}/{r['pre60_bars_present']} · null groups {r['null_groups']} · null micro {r['null_micro_fields']} · score mismatch {_v(r['score_mismatch'])} |",
            f"| loss class | **{r['loss_class'] or 'WIN'}** {r['loss_flags']} |",
            "",
        ]
    o += ["## 2. Aggregates", ""]
    o += _tbl(
        "Loss classes (A immediately wrong · B small favourable then failed · C materially favourable then gave back · E cost-made loss)",
        rep["loss_classes"],
    )
    o += [f"loss flags: {rep['loss_flags']}", ""]
    for k, title in (
        ("by_template", "Selected template"),
        ("by_side", "Side"),
        ("by_strict_gate", "Strict V11 structure gate of the selected template"),
        ("by_score_bucket", "Score bucket"),
        ("by_micro_tercile", "Microstructure contribution terciles"),
        ("by_trend", "Trend contribution"),
        ("by_momentum", "Momentum contribution"),
        ("by_adx", "ADX14"),
        ("by_vol_tercile", "Volatility tercile"),
        ("by_hour", "Time of day"),
        ("by_vwap", "Distance from VWAP (side-adjusted)"),
        ("by_fib_bucket", "Fibonacci proximity bucket (|dist| / ATR5)"),
        ("by_fib_leg_direction", "Fibonacci leg direction vs side"),
        ("by_exit_reason", "Exit reason"),
        ("by_stop_floored", "Stop geometry"),
        ("by_incremental", "Incremental cohort"),
    ):
        o += _tbl(title, rep[k])
    cd = rep["cost_decomposition"]
    o += [
        "### Cost decomposition",
        "",
        f"gross R sum {_v(cd['gross_r_sum'])} · cost R sum {_v(cd['cost_r_sum'])} · net R sum {_v(cd['net_r_sum'])} · fees {_v(cd['fees_usd'])} USDT · slippage {_v(cd['slip_usd'])} USDT · funding {_v(cd['funding_usd'])} USDT · gross-positive-net-negative trades {cd['trades_gross_positive_net_negative']}",
        "",
    ]
    o += ["### Path statistics (losses)", "", f"{rep['path_stats']}", ""]
    cf = rep["counterfactuals"]
    o += ["## 3. Counterfactuals (executed-trade subsets are exact; no threshold is proposed)", ""]
    o += _tbl(
        "A. strict-structure subset",
        {
            "all executed": cf["baseline"],
            "ONLY selected template strict PASS": cf["strict_only_subset"],
            "ONLY strict FAIL": cf["strict_fail_subset"],
        },
    )
    for k, v in cf["soft_filter"].items():
        o += _tbl(
            f"B. soft filter {k}: executed trades kept vs removed (adj score = score ± {k[3:]} by strict label; entry 75 unchanged)",
            {"kept": v["executed_kept"], "removed": v["executed_removed"]},
        )
        o += [
            f"removed: {v['removed_ids']}",
            f"near-miss additions (approximate, directional only): n {v['near_miss_additions_n']} · mean 60-min return {_v(v['near_miss_additions_60m_mean_bps'], 1)} bps · positive share {_v(v['near_miss_additions_60m_positive_share'])}",
            f"caveat: {v['caveat']}",
            "",
        ]
    tw = rep["control_twin"]
    o += [
        "## 4. Strict control twin",
        "",
        f"twin closed trades {len(tw['twin_closed_trades'])} · twin net {_v(tw['twin_net_usd'])} USDT · LITE trades the twin would take {tw['lite_trades_twin_would_take']} / reject {tw['lite_trades_twin_would_reject']} · twin executions while LITE was already in a position (LITE could not take them) {tw['twin_executions_while_lite_in_position']} · LITE reject reasons {tw['lite_reject_reasons']}",
        "",
        "| twin decision | family | side | action | blockers | score | same-bar LITE trade | LITE in position |",
        "|---|---|---|---|---|---|---|---|",
    ]
    o += [
        f"| {x['ts']} | {x['family']} | {x['side']} | {x['action']} | {x['blockers']} | {_v(x['score'], 1)} | {x['lite_same_bar_trade']} | {x['lite_in_position']} |"
        for x in tw["twin_decisions"]
    ]
    o += [
        "",
        "| twin trade | family | side | exit | net R | net USDT | MFE R |",
        "|---|---|---|---|---|---|---|",
    ]
    o += [
        f"| {x['ts']} | {x['family']} | {x['side']} | {x['exit_reason']} | {_v(x['net_r'])} | {_v(x['net_usd'])} | {_v(x['mfe_r'])} |"
        for x in tw["twin_closed_trades"]
    ]
    o += ["", "## 5. Implementation / pathology checks", ""]
    for k, v in rep["pathologies"].items():
        o.append(f"- **{k}**: {v}")
    o += ["", f"snapshot: {rep['snapshot']['verify']}", ""]
    return "\n".join(o)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="LITE forward root (default <BTC_DATA_DIR>/btc/forward_v11_lite_warm)",
    )
    ap.add_argument(
        "--verification", action="store_true", help="audit the verification root instead"
    )
    ap.add_argument("--out-dir", type=Path, default=Path("reports/forward_v11_lite_warm"))
    ap.add_argument("--cutoff", type=str, default=None, help="ISO UTC cutoff (default now - 1 min)")
    args = ap.parse_args()
    data_dir = Path(os.environ.get("BTC_DATA_DIR", "./data"))
    root = args.root or (
        data_dir
        / "btc"
        / ("forward_v11_lite_warm_verify" if args.verification else "forward_v11_lite_warm")
    )
    cutoff = int(datetime.fromisoformat(args.cutoff).timestamp() * 1000) if args.cutoff else None
    md, js, _rep = run_audit(root, args.out_dir, cutoff)
    sys.stdout.write(md.read_text())
    sys.stdout.write(f"\nwritten: {md}\n         {js}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
