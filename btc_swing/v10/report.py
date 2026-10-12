"""V10 reports: shadow verification (the checklist the owner asked for), daily report, and the
summary used by `status`. Everything is read from the journals, the state file and the raw store."""

from __future__ import annotations

import gzip
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v10.config import V10Config
from btc_swing.v10.engine import read_jsonl
from btc_swing.v10.paths import V10Paths


def _iso(ms: float | int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _read(f: Path) -> str:
    if f.suffix == ".gz":
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            return str(fh.read())
    return f.read_text(encoding="utf-8")


def feed_audit(paths: V10Paths, max_files: int = 400) -> dict[str, Any]:
    """Re-reads the own raw store: trade duplicates, liquidation side check, book sequence gaps,
    receive gaps > 5 s. Bounded to the last `max_files` hourly files."""
    files = sorted(paths.raw.glob("*/*.jsonl*"))[-max_files:]
    trades = 0
    ids: set[str] = set()
    dup = 0
    liq: list[tuple[int, str]] = []
    path: list[tuple[int, float]] = []
    ob_last: int | None = None
    ob_gaps = ob_deltas = ob_snaps = 0
    last_rx: int | None = None
    gaps: list[tuple[int, int]] = []
    first_rx: int | None = None
    by_channel: dict[str, int] = {}
    for f in files:
        for line in _read(f).splitlines():
            if not line:
                continue
            try:
                r = json.loads(line)
                raw = json.loads(r["raw"])
            except (json.JSONDecodeError, KeyError):
                continue
            ch = str(r["channel"])
            by_channel[ch] = by_channel.get(ch, 0) + 1
            rx = int(r["ts_received_ms"])
            first_rx = rx if first_rx is None else first_rx
            if last_rx is not None and rx - last_rx > 5000:
                gaps.append((last_rx, rx))
            last_rx = rx
            data = raw.get("data")
            if ch.startswith("publicTrade"):
                for t in data or []:
                    trades += 1
                    i = str(t.get("i"))
                    if i in ids:
                        dup += 1
                    ids.add(i)
                    path.append((int(t["T"]), float(t["p"])))
            elif ch.startswith("allLiquidation"):
                for q in data or []:
                    liq.append((int(q.get("T", r.get("ts_exchange_ms") or 0)), str(q.get("S"))))
            elif ch.startswith("orderbook.50"):
                u = (data or {}).get("u")
                if r.get("type") == "snapshot":
                    ob_snaps += 1
                    ob_last = int(u) if u is not None else ob_last
                else:
                    ob_deltas += 1
                    if u is not None and ob_last is not None and int(u) != ob_last + 1:
                        ob_gaps += 1
                    ob_last = int(u) if u is not None else ob_last
    import bisect

    pt = [t for t, _ in path]
    pp = [p for _, p in path]

    def px(t: int) -> float:
        i = bisect.bisect_right(pt, t) - 1
        return pp[i] if i >= 0 else math.nan

    side_check: dict[str, dict[str, Any]] = {}
    for s in ("Buy", "Sell"):
        xs = []
        for t, side in liq:
            if side != s:
                continue
            a, b = px(t - 60_000), px(t)
            if math.isfinite(a) and math.isfinite(b) and a > 0:
                xs.append((b - a) / a * 1e4)
        side_check[s] = {
            "n": len(xs),
            "median_60s_move_bps": statistics.median(xs) if xs else None,
            "share_negative": (sum(1 for x in xs if x < 0) / len(xs)) if xs else None,
        }
    buy, sell = side_check["Buy"], side_check["Sell"]
    consistent = None
    if buy["n"] >= 3 and sell["n"] >= 3:
        consistent = bool(buy["share_negative"] >= 0.7 and sell["share_negative"] <= 0.3)
    return {
        "files": len(files),
        "first_rx": _iso(first_rx),
        "last_rx": _iso(last_rx),
        "by_channel": by_channel,
        "trades": trades,
        "duplicate_trade_ids": dup,
        "liquidations": len(liq),
        "liquidation_side_check": side_check,
        "liquidation_side_consistent": consistent,
        "orderbook": {
            "snapshots": ob_snaps,
            "deltas": ob_deltas,
            "seq_gaps": ob_gaps,
            "gap_rate": ob_gaps / ob_deltas if ob_deltas else None,
        },
        "receive_gaps_over_5s": len(gaps),
        "receive_gap_minutes": sum(b - a for a, b in gaps) / 60000,
        "largest_gap_min": max([(b - a) / 60000 for a, b in gaps], default=0.0),
    }


def _signals(paths: V10Paths) -> list[dict[str, Any]]:
    return [
        r["data"]
        for r in read_jsonl(paths.signals)
        if r["kind"] in ("SIGNAL_EXECUTED", "SIGNAL_BLOCKED")
    ]


def shadow_report(cfg: V10Config, paths: V10Paths, min_hours: float = 3.0) -> dict[str, Any]:
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    eps = [r for r in read_jsonl(paths.episodes)]
    sigs = _signals(paths)
    nms = read_jsonl(paths.nearmiss)
    sh = [r["data"] for r in read_jsonl(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    outs = [r["data"] for r in read_jsonl(paths.outcomes)]
    audit = feed_audit(paths)
    start = st.get("observation_start_ms")
    last = st.get("last_tick_ms")
    dur_s = ((last or 0) - (start or 0)) / 1000 if start and last else 0.0
    days = max(dur_s / 86400, 1e-9)
    armed = [e for e in eps if e["kind"] == "EPISODE_ARMED"]
    trig = [e for e in eps if e["kind"] == "EPISODE_TRIGGERED"]
    fam_dist = {
        f"{fam}-{side}": sum(
            1 for e in armed if e["data"]["family"] == fam and e["data"]["side"] == side
        )
        for fam in "ABC"
        for side in ("LONG", "SHORT")
    }
    trig_dist = {
        f"{fam}-{side}": sum(
            1 for e in trig if e["data"]["family"] == fam and e["data"]["side"] == side
        )
        for fam in "ABC"
        for side in ("LONG", "SHORT")
    }
    sig_ids = [s["signal_id"] for s in sigs]
    dup_signals = len(sig_ids) - len(set(sig_ids))
    # price / risk sanity on executed signals
    bad_price = bad_risk = bad_stop_side = bad_stop_pct = bad_ts = 0
    executed = [s for s in sigs if s["action"] == "EXECUTED"]
    for s in sigs:
        f = s.get("features") or {}
        e, stp, side = float(s["entry_ref"]), float(s["stop"]), float(s["side_sign"])
        b1, a1 = f.get("bid1"), f.get("ask1")
        if b1 and a1 and not (b1 * (1 - 1e-3) <= e <= a1 * (1 + 1e-3)):
            bad_price += 1
        if side * (e - stp) <= 0:
            bad_stop_side += 1
        if (
            not (cfg.stop.floor_pct - 1e-9 <= float(s["stop_pct"]) <= cfg.stop.cap_pct + 1e-9)
            and s["action"] == "EXECUTED"
        ):
            bad_stop_pct += 1
        if (
            s["action"] == "EXECUTED"
            and abs(float(s["risk_usdt"]) - cfg.risk.initial_equity * cfg.risk.risk_per_trade)
            > 0.01
        ):
            bad_risk += 1
        if (s.get("decision_ts") or "") < (s.get("trigger_ts") or ""):
            bad_ts += 1
    chains = {
        n: verify_chain(p)
        for n, p in (
            ("episodes", paths.episodes),
            ("signals", paths.signals),
            ("nearmiss", paths.nearmiss),
            ("shadow_trades", paths.shadow_trades),
            ("outcomes", paths.outcomes),
        )
        if p.exists()
    }
    checks = {
        "duration >= min hours": dur_s >= min_hours * 3600,
        "trade feed received": audit["trades"] > 0 and audit["duplicate_trade_ids"] == 0,
        "liquidation feed received": audit["liquidations"] > 0,
        "liquidation side interpretation consistent (or too few to test)": audit[
            "liquidation_side_consistent"
        ]
        in (True, None),
        "order book sequence integrity": (audit["orderbook"]["gap_rate"] or 0.0) < 1e-3,
        "no duplicate signal ids": dup_signals == 0,
        "no timestamp leakage (decision after trigger)": bad_ts == 0,
        "no impossible order prices": bad_price == 0 and bad_stop_side == 0,
        "stop within floor/cap on executed": bad_stop_pct == 0,
        "risk sizing = 5 USDT on executed": bad_risk == 0,
        "journals hash-chain valid": all(c.get("ok") for c in chains.values()) if chains else True,
        "warm-up reached": bool(
            st.get("counters", {}).get("armed", 0) >= 0
            and (st.get("history") or {}).get("abs_delta_30", [])
            and len(st["history"]["abs_delta_30"]) >= cfg.features.history_min_samples
        ),
    }
    status = "PASSED" if all(checks.values()) else "NOT_PASSED"
    net = [t["net_r"] for t in sh if t.get("net_r") is not None]
    rep = {
        "run_id": f"v10shadow-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "generated_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "checks": checks,
        "duration_s": dur_s,
        "observation_start": _iso(start),
        "last_tick": _iso(last),
        "verification_root": paths.verification,
        "armed": len(armed),
        "triggered": len(trig),
        "executed": len(executed),
        "blocked": sum(1 for s in sigs if s["action"] == "BLOCKED"),
        "blockers": {
            b: sum(1 for s in sigs if b in (s.get("blockers") or []))
            for b in {x for s in sigs for x in (s.get("blockers") or [])}
        },
        "invalidated": sum(1 for e in eps if e["kind"] == "EPISODE_INVALIDATED"),
        "expired": sum(1 for e in eps if e["kind"] == "EPISODE_EXPIRED"),
        "family_armed": fam_dist,
        "family_triggered": trig_dist,
        "armed_per_day": len(armed) / days,
        "triggers_per_day": len(trig) / days,
        "executed_per_day": len(executed) / days,
        "near_misses": len(nms),
        "duplicate_signals": dup_signals,
        "feed": audit,
        "shadow_trades": len(sh),
        "shadow_net_r_mean": statistics.mean(net) if net else None,
        "shadow_net_usd": sum(t["net_usd"] for t in sh),
        "shadow_exit_reasons": {
            k: sum(1 for t in sh if t["exit_reason"] == k) for k in {t["exit_reason"] for t in sh}
        },
        "outcomes_appended": len(outs),
        "sanity": {
            "bad_price": bad_price,
            "bad_stop_side": bad_stop_side,
            "bad_stop_pct": bad_stop_pct,
            "bad_risk": bad_risk,
            "bad_ts": bad_ts,
        },
        "chains": {k: v.get("ok") for k, v in chains.items()},
        "counters": st.get("counters"),
        "history_samples": {k: len(v) for k, v in (st.get("history") or {}).items()},
    }
    return rep


def render_shadow_report(rep: dict[str, Any]) -> str:
    out = [
        f"# BTC V10 — shadow verification report ({rep['run_id']})\n",
        f"Generated {rep['generated_utc']} · status **{rep['status']}** · root {'VERIFICATION' if rep['verification_root'] else 'OBSERVATION'} · from {rep['observation_start']} to {rep['last_tick']} ({rep['duration_s'] / 3600:.2f} h).\n",
        "No order was placed. Nothing here is historical evidence; it verifies feed quality and signal mechanics.\n",
        "## Checks\n",
        "| check | result |",
        "|---|---|",
    ]
    out += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in rep["checks"].items()]
    fa = rep["feed"]
    out += [
        "",
        "## Feed",
        f"- trades {fa['trades']} (duplicate ids {fa['duplicate_trade_ids']}); liquidations {fa['liquidations']}; side check {json.dumps(fa['liquidation_side_check'])}; consistent: {fa['liquidation_side_consistent']}",
        f"- order book: {fa['orderbook']}; receive gaps > 5 s: {fa['receive_gaps_over_5s']} ({fa['receive_gap_minutes']:.1f} min, largest {fa['largest_gap_min']:.1f} min)",
        f"- channels: {fa['by_channel']}",
        "",
        "## Signals",
        f"- ARMED {rep['armed']} ({rep['armed_per_day']:.1f}/day), TRIGGERED {rep['triggered']} ({rep['triggers_per_day']:.1f}/day), EXECUTED (shadow) {rep['executed']} ({rep['executed_per_day']:.1f}/day), BLOCKED {rep['blocked']} {rep['blockers']}, invalidated {rep['invalidated']}, expired {rep['expired']}, near-misses {rep['near_misses']}, duplicate signals {rep['duplicate_signals']}",
        f"- family ARMED: {rep['family_armed']}",
        f"- family TRIGGERED: {rep['family_triggered']}",
        f"- shadow trades closed {rep['shadow_trades']}: exit reasons {rep['shadow_exit_reasons']}, mean net R {rep['shadow_net_r_mean']}, net {rep['shadow_net_usd']:.2f} USDT; outcomes appended {rep['outcomes_appended']}",
        f"- sanity: {rep['sanity']}; journals: {rep['chains']}; history samples: {rep['history_samples']}",
        "",
    ]
    return "\n".join(out)


def daily_report(cfg: V10Config, paths: V10Paths, day: str) -> str:
    """Immutable daily summary for a UTC day (written once by the runner at 00:05 UTC)."""
    d0 = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    d1 = d0 + 86_400_000
    eps = [r for r in read_jsonl(paths.episodes) if d0 <= int(r["data"].get("ts_ms") or 0) < d1]
    sigs = [s for s in _signals(paths) if d0 <= int(s.get("ts_ms") or 0) < d1]
    sh = [
        r["data"]
        for r in read_jsonl(paths.shadow_trades)
        if r["kind"] == "SHADOW_CLOSED" and d0 <= int(r["data"]["exit_ms"]) < d1
    ]
    demo = (
        [t for t in read_jsonl(paths.demo_trades) if d0 <= int(t.get("closed_ms") or 0) < d1]
        if paths.demo_trades.exists()
        else []
    )
    lines = [
        f"# V10 daily report {day}\n",
        f"ARMED {sum(1 for e in eps if e['kind'] == 'EPISODE_ARMED')}, TRIGGERED {sum(1 for e in eps if e['kind'] == 'EPISODE_TRIGGERED')}, EXECUTED {sum(1 for s in sigs if s['action'] == 'EXECUTED')}, BLOCKED {sum(1 for s in sigs if s['action'] == 'BLOCKED')}\n",
        "| signal | family | side | action | entry | stop % | blockers |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {s['signal_id']} | {s['family']} | {s['side']} | {s['action']} | {s['entry_ref']:.1f} | {s['stop_pct']:.2f} | {', '.join(s.get('blockers') or [])} |"
        for s in sigs
    ]
    lines += [
        "",
        f"Shadow trades closed: {len(sh)}, net {sum(t['net_usd'] for t in sh):.2f} USDT; Demo trades closed: {len(demo)}, net {sum(float(t.get('net_pnl') or 0) for t in demo):.2f} USDT.",
        "",
    ]
    return "\n".join(lines)
