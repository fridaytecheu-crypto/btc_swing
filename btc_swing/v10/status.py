"""`btc-swing v10 forward status [--live]` rows and `diagnostic` text (read-only)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from btc_swing.v5.demo.activation import activation_state as v5_activation_state
from btc_swing.v5.forward.host import authority_state
from btc_swing.v10.activation import activation_state, conflict_checks, effective_activation
from btc_swing.v10.config import V10Config
from btc_swing.v10.engine import read_jsonl
from btc_swing.v10.freeze import load_freeze
from btc_swing.v10.paths import V10Paths
from btc_swing.v10.runner import diagnostic_snapshot, latest_raw_age_s, runner_alive

DAY_MS = 86_400_000


def _iso(ms: float | int | None) -> str:
    return (
        "n/a"
        if ms is None
        else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    )


def _age(iso: str | None) -> str:
    if not iso:
        return "n/a"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return "n/a"
    return f"{(datetime.now(UTC) - t).total_seconds():.0f} s ago"


def exchange_snapshot(cfg_demo_path: str = "config/btc_swing_v5_demo.yaml") -> dict[str, Any]:
    """Read-only Demo account view (GET only; needs credentials; never on production)."""
    from btc_swing.v5.demo.client import BybitDemoClient, DemoApiError, DemoTransportError
    from btc_swing.v5.demo.config import ExecutionMode, load_demo_config
    from btc_swing.v5.demo.credentials import DemoCredentialsMissingError, credentials_present
    from btc_swing.v5.demo.journal import HashChainJournal

    dcfg = load_demo_config(cfg_demo_path, ExecutionMode.DISABLED)
    if not credentials_present(dcfg):
        return {"reachable": False, "error": "demo credentials missing (env)", "credentials": False}
    try:
        from pathlib import Path

        j = HashChainJournal(Path(".v10_readonly_journal.jsonl"), "v10_readonly")
        c = BybitDemoClient(dcfg, ExecutionMode.DISABLED, j, "V10_READONLY", read_only=True)
        pos = c.position()
        oo = c.open_orders()
        c.close()
        return {
            "reachable": True,
            "credentials": True,
            "position_size": pos["size"],
            "position_side": pos["side"],
            "position": pos,
            "open_orders": len(oo),
            "open_order_links": [o.get("orderLinkId") for o in oo],
        }
    except (DemoApiError, DemoTransportError, DemoCredentialsMissingError) as e:
        return {"reachable": False, "credentials": True, "error": str(e)[:200]}


def warmup_reconstruction(cfg: V10Config, paths: V10Paths) -> dict[str, Any]:
    """When the live engine's `warm` flag first held, reconstructed from persisted facts (read-only):
    histories need `history_min_samples` 10-s samples (= observation start + 30 min with continuous
    ticks), ATR1m needs 15 completed 1m bars, ATR5m needs 15 completed aligned 5m bars (+2 s grace).
    Exact to the second for the bar parts; the history part assumes uninterrupted ticks."""
    fc = cfg.features
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    start = st.get("observation_start_ms")
    rows = read_jsonl(paths.bars_1m)
    opens = sorted(int(r["open_ms"]) for r in rows)
    open_set = set(opens)
    out: dict[str, Any] = {"observation_start": _iso(start), "bars_1m": len(opens)}
    hist_ms = int(start) + fc.history_min_samples * fc.history_sample_s * 1000 if start else None
    out["histories_ready_at"] = _iso(hist_ms)
    need = fc.atr_period + 1
    atr1_ms = opens[need - 1] + 60_000 + 2000 if len(opens) >= need else None
    out["atr1_ready_at"] = _iso(atr1_ms)
    done5 = [
        k
        for k in sorted({(o // 300_000) * 300_000 for o in opens})
        if all((k + i * 60_000) in open_set for i in range(5))
    ]
    atr5_ms = done5[need - 1] + 300_000 + 2000 if len(done5) >= need else None
    out["atr5_ready_at"] = _iso(atr5_ms)
    out["completed_5m_bars"] = len(done5)
    parts = [x for x in (hist_ms, atr1_ms, atr5_ms) if x is not None]
    out["warm_at"] = _iso(max(parts)) if len(parts) == 3 else None
    hist = st.get("history") or {}
    out["warm_now_by_state"] = bool(
        hist
        and all(
            len(v) >= fc.history_min_samples
            for k, v in hist.items()
            if not k.startswith("abs_impact")
        )
        and atr5_ms is not None
        and atr1_ms is not None
    )
    out["history_samples"] = {k: len(v) for k, v in hist.items()}
    return out


def trades_table(cfg: V10Config, paths: V10Paths) -> list[dict[str, Any]]:
    """Every closed shadow trade joined with its decision record (warm / valid flags at trigger)."""
    sigs = {
        r["data"]["signal_id"]: r["data"]
        for r in read_jsonl(paths.signals)
        if r["kind"].startswith("SIGNAL_")
    }
    out = []
    for r in read_jsonl(paths.shadow_trades):
        if r["kind"] != "SHADOW_CLOSED":
            continue
        t = r["data"]
        sg = sigs.get(t["signal_id"], {})
        f = sg.get("features") or {}
        fa = sg.get("arm_features") or {}
        out.append(
            {
                "signal_id": t["signal_id"],
                "decision_ts": sg.get("decision_ts"),
                "armed_ts": sg.get("armed_ts"),
                "family": t["family"],
                "side": "LONG" if int(t["side"]) > 0 else "SHORT",
                "entry_ref": t["entry_ref"],
                "entry_fill": t["entry_fill"],
                "stop": t["stop"],
                "tp1": t["tp1"],
                "tp2": t["tp2"],
                "exit_ts": _iso(t["exit_ms"]),
                "exit_reason": t["exit_reason"],
                "legs": [(lg["kind"], lg["ref"]) for lg in t["legs"]],
                "net_usd": t["net_usd"],
                "net_r": t["net_r"],
                "hold_min": t["hold_min"],
                "warm_at_trigger": f.get("warm"),
                "atr5_at_trigger": f.get("atr5"),
                "atr1_at_trigger": f.get("atr1"),
                "book_valid_at_trigger": f.get("book_valid"),
                "trade_age_s_at_trigger": f.get("trade_age_s"),
                "warm_at_arm": fa.get("warm"),
                "stop_pct": sg.get("stop_pct"),
                "stop_note": sg.get("stop_note"),
                "risk_usdt": sg.get("risk_usdt"),
                "blockers": sg.get("blockers"),
            }
        )
    return out


def status_rows(cfg: V10Config, paths: V10Paths, live: bool = False) -> list[tuple[str, str]]:
    st = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    alive, pid = runner_alive(paths)
    raw_age = latest_raw_age_s(paths)
    diag = diagnostic_snapshot(paths) or {}
    f = diag.get("features") or {}
    diag_age: float | None = None
    if diag.get("written_ms"):
        diag_age = (time.time() * 1000 - float(diag["written_ms"])) / 1000
    elif diag.get("written_at"):
        try:
            diag_age = (
                datetime.now(UTC) - datetime.fromisoformat(diag["written_at"])
            ).total_seconds()
        except ValueError:
            diag_age = None
    wu = warmup_reconstruction(cfg, paths)
    stale = alive and diag_age is not None and diag_age > 60
    fz = load_freeze()
    act = activation_state(paths)
    eff = effective_activation(paths)
    dst = json.loads(paths.demo_state.read_text()) if paths.demo_state.exists() else {}
    now_ms = int(time.time() * 1000)
    day0 = (now_ms // DAY_MS) * DAY_MS
    sigs = [r["data"] for r in read_jsonl(paths.signals) if r["kind"].startswith("SIGNAL_")]
    today = [s for s in sigs if int(s.get("ts_ms") or 0) >= day0]
    sh = [r["data"] for r in read_jsonl(paths.shadow_trades) if r["kind"] == "SHADOW_CLOSED"]
    demo_tr = read_jsonl(paths.demo_trades) if paths.demo_trades.exists() else []
    demo_today = [t for t in demo_tr if int(t.get("closed_ms") or 0) >= day0]
    counters = st.get("counters") or {}
    fam_counts = {fam: sum(1 for s in sigs if s.get("family") == fam) for fam in "ABC"}
    v51 = conflict_checks(paths)
    rows = [
        ("runner", f"ALIVE (pid {pid})" if alive else "DEAD"),
        (
            "collector / feed",
            "CONNECTED" if raw_age is not None and raw_age <= 30 and alive else "DISCONNECTED",
        ),
        (
            "observation start",
            _iso(st.get("observation_start_ms"))
            + (" (VERIFICATION root)" if paths.verification else ""),
        ),
        (
            "freeze",
            f"{fz['config_hash'][:12]} frozen {fz['frozen_at'][:19]}"
            if fz
            else "NONE (shadow verification only)",
        ),
        ("mode", str(diag.get("mode") or st.get("mode") or "n/a")),
        ("V10 Demo active", f"YES since {eff['activated_at']}" if eff else f"NO ({act['status']})"),
        ("demo refused reason", str(diag.get("demo_refused") or "-")),
        (
            "latest event",
            f"{_iso(st.get('last_event_ms'))} ({'n/a' if raw_age is None else f'{raw_age:.0f} s ago'})",
        ),
        ("latest tick", _iso(st.get("last_tick_ms"))),
        ("latest signal", json.dumps(st.get("last_signal")) if st.get("last_signal") else "none"),
        (
            "signals today (executed / blocked)",
            f"{sum(1 for s in today if s['action'] == 'EXECUTED')} / {sum(1 for s in today if s['action'] == 'BLOCKED')}",
        ),
        ("counters since start", json.dumps(counters)),
        ("family counts (signals)", json.dumps(fam_counts)),
        (
            "open V10 shadow position",
            "YES "
            + json.dumps(
                {
                    k: st["shadow_position"][k]
                    for k in ("signal_id", "family", "side", "entry_fill", "stop")
                }
            )
            if st.get("shadow_position")
            else "none",
        ),
        (
            "open V10 demo position (local)",
            "YES "
            + json.dumps(
                {k: dst["position"].get(k) for k in ("signal_id", "family", "side", "status")}
            )
            if dst.get("position")
            else "none",
        ),
        (
            "trades today (shadow / demo)",
            f"{sum(1 for t in sh if int(t['exit_ms']) >= day0)} / {len(demo_today)}",
        ),
        (
            "daily PnL (shadow / demo USDT)",
            f"{sum(t['net_usd'] for t in sh if int(t['exit_ms']) >= day0):.2f} / {sum(float(t.get('net_pnl') or 0) for t in demo_today):.2f}",
        ),
        (
            "cumulative V10 Demo PnL (USDT)",
            f"{float(dst.get('cum_net_pnl') or 0.0):.2f} over {int(dst.get('closed_trades') or 0)} trades",
        ),
        (
            "cumulative shadow PnL (USDT)",
            f"{float(st.get('cum_shadow_net_usd') or 0.0):.2f} over {len(sh)} trades",
        ),
        ("wins / losses (demo)", f"{dst.get('wins', 0)} / {dst.get('losses', 0)}"),
        ("wins / losses (shadow)", f"{st.get('shadow_wins', 0)} / {st.get('shadow_losses', 0)}"),
        ("current loss streak (full-risk)", str(st.get("consecutive_full_losses", 0))),
        (
            "daily safety",
            f"BLOCKED until {_iso(st.get('blocked_until_ms'))} ({st.get('blocked_reason')})"
            if st.get("blocked_until_ms") and now_ms < int(st["blocked_until_ms"])
            else f"OK (executed in 24h: {len(st.get('executed_ms') or [])}/{cfg.safety.max_trades_rolling_24h}, day realised {float(st.get('day_realised_usd') or 0):.2f} USDT)",
        ),
        ("feed gaps (receive > 5 s)", str(diag.get("receive_gaps", "see shadow-report"))),
        ("trade feed age", f"{f.get('trade_age_s', 'n/a')} s"),
        ("liquidation feed age", f"{f.get('liq_age_s', 'n/a')} s"),
        (
            "order-book feed age / valid",
            f"{f.get('book_age_ms', 'n/a')} ms / {f.get('book_valid', 'n/a')}",
        ),
        (
            "warm-up (from state + bars)",
            f"{'READY' if wu['warm_now_by_state'] else 'WARMING'} since {wu['warm_at']} "
            f"(histories {wu['histories_ready_at']}, atr1 {wu['atr1_ready_at']}, atr5 {wu['atr5_ready_at']}); "
            f"samples {json.dumps(wu['history_samples'])}",
        ),
        (
            "diagnostic snapshot",
            f"written {diag.get('written_at')} ({'n/a' if diag_age is None else f'{diag_age:.0f} s ago'})"
            f"{' STALE: runner alive but snapshot old' if stale else ''}; snapshot warm flag {f.get('warm')}",
        ),
        (
            "reconciliation (V10 demo)",
            "REQUIRED: " + str(dst.get("reconcile_reason"))
            if dst.get("reconcile_required")
            else "none pending",
        ),
        (
            "V5.1 / V5 conflict gates",
            "; ".join(f"{k}: {'ok' if v['ok'] else 'FAIL'}" for k, v in v51.items()),
        ),
        (
            "authority lease (V5 root)",
            json.dumps({k: authority_state(paths.v5_root).get(k) for k in ("status", "hostname")})
            if paths.v5_root.exists()
            else "no V5 root",
        ),
    ]
    if live:
        ex = exchange_snapshot()
        rows.append(
            (
                "Bybit Demo live position",
                json.dumps(ex.get("position"))
                if ex.get("reachable")
                else f"unavailable: {ex.get('error')}",
            )
        )
        rows.append(
            (
                "Bybit Demo open orders",
                str(ex.get("open_orders")) if ex.get("reachable") else "unavailable",
            )
        )
        rows.append(
            (
                "V5.1 activation journal",
                json.dumps(
                    {k: v5_activation_state(paths.v51_root).get(k) for k in ("status", "events")}
                )
                if paths.v51_root.exists()
                else "no V5.1 root",
            )
        )
    return rows


def status_text(cfg: V10Config, paths: V10Paths, live: bool = False) -> str:
    rows = status_rows(cfg, paths, live)
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(w)}  {v}" for k, v in rows)


def diagnostic_text(paths: V10Paths) -> str:
    d = diagnostic_snapshot(paths)
    if d is None:
        return "no diagnostic snapshot (runner not running?)"
    f = d.get("features") or {}
    keys = [
        "last",
        "trade_age_s",
        "liq_age_s",
        "imb_10",
        "imb_30",
        "delta_30",
        "abs_delta_pct_30",
        "intensity_10",
        "intensity_pct_10",
        "liq_total_30",
        "liq_burst_pct_30",
        "liq_imb_30",
        "ret_30",
        "impact_30",
        "abs_impact_pct_30",
        "atr5",
        "atr1",
        "atr1h",
        "swing_hi_5m",
        "swing_lo_5m",
        "range_hi_15",
        "range_lo_15",
        "hi_60",
        "lo_60",
        "dist_lo",
        "dist_hi",
        "book_valid",
        "bid1",
        "ask1",
        "spread_bps",
        "bid_depth",
        "ask_depth",
        "depth_imb",
        "bid_repl",
        "ask_repl",
        "warm",
        "bars_1m",
    ]
    out = [
        f"V10 diagnostic written {d.get('written_at')} mode {d.get('mode')} queue {d.get('queue')} open_position {d.get('open_position')} blocked_until {d.get('blocked_until')} ({d.get('blocked_reason')})",
        "",
        "features:",
    ]
    out += [f"  {k:20s} {f.get(k)}" for k in keys]
    out += ["", "families:"]
    for fam in d.get("families") or []:
        conds = ", ".join(
            f"{k}={'Y' if v else 'n'}" for k, v in (fam.get("conditions") or {}).items()
        )
        out.append(
            f"  {fam['family']}-{fam['side']:5s} {fam['state']:12s} {fam['reason'][:60]:60s} [{conds}]"
        )
    out += ["", f"history samples: {json.dumps(d.get('history'))}"]
    return "\n".join(out)
