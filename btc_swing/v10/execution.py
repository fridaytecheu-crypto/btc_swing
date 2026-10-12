"""V10 Bybit DEMO executor (designed now, refused until an owner activation event exists for this
host). Reuses the V5 demo client (demo endpoint enforced), fill confirmation helpers and the
hash-chained journal; V10 ids `V10D-<sha(signal_id)[:20]>-<role>`; own journal, state and trades
under forward_v10/demo/. Frozen exits: TP1 +1R 50%, TP2 +2R 50%, time stop, hard maximum."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.client import (
    RET_DUPLICATE_LINK_ID,
    BybitDemoClient,
    DemoApiError,
    DemoTransportError,
    fmt_price,
    fmt_qty,
)
from btc_swing.v5.demo.config import DemoExecConfig
from btc_swing.v5.demo.fills import (
    ExecutionConfirmationTimeoutError,
    backoff_schedule,
    confirm_executions,
    confirmed_record,
    dedupe_executions,
)
from btc_swing.v5.demo.ids import is_smoke_link_id
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v10.config import PREFIX_ORDER, STRATEGY, V10Config

ROLES = ("EN", "T1", "T2", "TC", "XC")
QTY_EPS = 1e-9


def v10_link_id(signal_id: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(role)
    return f"{PREFIX_ORDER}{hashlib.sha256(signal_id.encode()).hexdigest()[:20]}-{role}"


def is_v10_link_id(s: str | None) -> bool:
    return bool(s) and str(s).startswith(PREFIX_ORDER)


def _iso(ms: float | int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _bybit_side(side_sign: int, closing: bool = False) -> str:
    long = side_sign > 0
    return ("Sell" if long else "Buy") if closing else ("Buy" if long else "Sell")


class V10DemoExecutor:
    def __init__(
        self,
        cfg: V10Config,
        dcfg: DemoExecConfig,
        client: BybitDemoClient,
        journal: HashChainJournal,
        state_path: Path,
        trades_path: Path,
        activated_at_ms: int,
    ) -> None:
        self.cfg, self.dcfg, self.client, self.journal = cfg, dcfg, client, journal
        self.state_path, self.trades_path = state_path, trades_path
        self.activated_at_ms = activated_at_ms
        self.state: dict[str, Any] = self._load()
        self._inst: dict[str, Any] | None = None

    # ------------------------------------------------------------------ state
    def _default(self) -> dict[str, Any]:
        return {
            "strategy": STRATEGY,
            "position": None,
            "processed_signals": [],
            "reconcile_required": False,
            "reconcile_reason": None,
            "last_order": None,
            "last_fill": None,
            "closed_trades": 0,
            "cum_net_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "last_exchange_position": None,
        }

    def _load(self) -> dict[str, Any]:
        if self.state_path.exists():
            return {**self._default(), **json.loads(self.state_path.read_text())}
        return self._default()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, sort_keys=True, default=str))
        tmp.replace(self.state_path)

    def _j(self, kind: str, data: dict[str, Any]) -> None:
        self.journal.append(kind, data, "STRATEGY_DEMO_V10")

    def alert(self, kind: str, data: dict[str, Any], reconcile: bool = False) -> None:
        self._j("ALERT_" + kind, data)
        if reconcile:
            self.state["reconcile_required"] = True
            self.state["reconcile_reason"] = kind

    def acknowledge_reconciliation(self, note: str) -> None:
        self._j(
            "RECONCILIATION_ACKNOWLEDGED",
            {"note": note, "reason": self.state.get("reconcile_reason")},
        )
        self.state["reconcile_required"] = False
        self.state["reconcile_reason"] = None
        self.save()

    def has_position(self) -> bool:
        return self.state.get("position") is not None

    def closed_trades_since(self, t_ms: int) -> list[dict[str, Any]]:
        if not self.trades_path.exists():
            return []
        out = []
        with self.trades_path.open() as fh:
            for line in fh:
                if line.strip():
                    t = json.loads(line)
                    if int(t.get("closed_ms") or 0) >= t_ms:
                        out.append(t)
        return out

    def _instrument(self) -> dict[str, Any]:
        if self._inst is None:
            self._inst = self.client.instrument()
        return self._inst

    # ------------------------------------------------------------------ recovery
    def recover(self) -> dict[str, Any]:
        p = self.state.get("position")
        try:
            pos = self.client.position()
        except (DemoApiError, DemoTransportError) as e:
            self.alert("RECOVERY_EXCHANGE_UNREACHABLE", {"error": str(e)[:200]}, reconcile=True)
            self.save()
            return {"action": "exchange_unreachable"}
        self.state["last_exchange_position"] = pos
        if p is None and pos["size"] > 0:
            self.alert("UNEXPECTED_OPEN_POSITION_AT_START", {"position": pos}, reconcile=True)
            self.save()
            return {"action": "reconcile_required"}
        if p is not None and pos["size"] <= 0:
            out = self._finalize(p)
            self.save()
            return {"action": "finalized_at_start", "result": out.get("action")}
        if p is not None:
            self._ensure_protection(p, pos)
        self.save()
        return {"action": "ok", "position_open": p is not None}

    # ------------------------------------------------------------------ entry
    def enter(self, sig: dict[str, Any], f: dict[str, float]) -> dict[str, Any]:
        sid = str(sig["signal_id"])
        side = int(sig["side_sign"])
        out: dict[str, Any] = {"signal_id": sid}
        if sid in self.state["processed_signals"]:
            self.alert("DUPLICATE_SIGNAL", {"signal_id": sid})
            return out | {"action": "SKIPPED_DUPLICATE"}
        self.state["processed_signals"] = [*self.state["processed_signals"], sid][-2000:]
        if int(sig["decision_ms"]) <= self.activated_at_ms:
            self._j("PRE_ACTIVATION_SIGNAL_REFUSED", {"signal_id": sid})
            self.save()
            return out | {"action": "REFUSED_BEFORE_ACTIVATION"}
        blockers: list[str] = []
        if self.state["reconcile_required"]:
            blockers.append("RECONCILIATION_REQUIRED")
        if self.state["position"] is not None:
            blockers.append("POSITION_OPEN")
        try:
            pos = self.client.position()
            if pos["size"] > 0:
                blockers.append("EXCHANGE_POSITION_NOT_FLAT")
            if any(not is_v10_link_id(o.get("orderLinkId")) for o in self.client.open_orders()):
                blockers.append("FOREIGN_OPEN_ORDERS")
            inst = self._instrument()
        except (DemoApiError, DemoTransportError) as e:
            self._j(
                "ENTRY_BLOCKED",
                {"signal_id": sid, "blockers": ["EXCHANGE_UNREACHABLE"], "error": str(e)[:200]},
            )
            self.save()
            return out | {"action": "BLOCKED", "blockers": ["EXCHANGE_UNREACHABLE"]}
        qty_s = fmt_qty(float(sig["qty"]), inst["qty_step"])
        if float(qty_s) < inst["min_qty"] - 1e-12:
            blockers.append("BELOW_MIN_QTY")
        if blockers:
            self._j("ENTRY_BLOCKED", {"signal_id": sid, "blockers": blockers})
            self.save()
            return out | {"action": "BLOCKED", "blockers": blockers}
        link = v10_link_id(sid, "EN")
        try:
            if self.client.order(link) is not None:
                self.alert(
                    "DUPLICATE_ON_EXCHANGE", {"signal_id": sid, "link": link}, reconcile=True
                )
                self.save()
                return out | {"action": "SKIPPED_DUPLICATE_ON_EXCHANGE"}
            self.client.set_leverage(float(sig["leverage"]))
        except (DemoApiError, DemoTransportError) as e:
            self._j(
                "ENTRY_BLOCKED",
                {
                    "signal_id": sid,
                    "blockers": ["EXCHANGE_ERROR_BEFORE_ORDER"],
                    "error": str(e)[:200],
                },
            )
            self.save()
            return out | {"action": "BLOCKED", "blockers": ["EXCHANGE_ERROR_BEFORE_ORDER"]}
        p: dict[str, Any] = {
            "signal_id": sid,
            "episode_id": sig.get("episode_id"),
            "family": sig.get("family"),
            "side": side,
            "entry_ref": float(sig["entry_ref"]),
            "stop": float(sig["stop"]),
            "tp1": float(sig["tp1"]),
            "tp2": float(sig["tp2"]),
            "planned_qty": float(qty_s),
            "leverage": float(sig["leverage"]),
            "planned_risk_usdt": float(sig["risk_usdt"]),
            "link_entry": link,
            "status": "SUBMITTING",
            "trigger_ms": int(sig.get("trigger_ms") or sig["decision_ms"]),
            "decision_ms": int(sig["decision_ms"]),
            "order_sent_ms": None,
            "ack_ms": None,
            "fill_ms": None,
            "legs": {},
            "tp1_done": False,
            "time_checked": False,
        }
        self.state["position"] = p
        p["order_sent_ms"] = self.client.now_ms()
        self._j(
            "ENTRY_SUBMIT",
            {
                "signal_id": sid,
                "link": link,
                "side": _bybit_side(side),
                "qty": qty_s,
                "leverage": p["leverage"],
                "order_sent_ts": _iso(p["order_sent_ms"]),
            },
        )
        self.save()  # write-ahead: a crash after this line never leads to a second order
        try:
            r = self.client.create_order(_bybit_side(side), qty_s, "Market", link)
            p["status"], p["order_id"], p["ack_ms"] = (
                "ACKED",
                r.get("orderId"),
                self.client.now_ms(),
            )
        except DemoApiError as e:
            if e.ret_code == RET_DUPLICATE_LINK_ID:
                p["status"], p["ack_ms"] = "ACKED", self.client.now_ms()
            else:
                self._j("ENTRY_REJECTED", {"signal_id": sid, "error": str(e)[:200]})
                self.state["position"] = None
                self.save()
                return out | {"action": "REJECTED", "error": str(e)[:200]}
        except DemoTransportError as e:
            self.alert("ORDER_ACK_MISSING", {"signal_id": sid, "link": link, "error": str(e)[:200]})
            o = None
            with contextlib.suppress(DemoApiError, DemoTransportError):
                o = self.client.order(link)
            if o is None:
                self.alert("ORDER_STATE_UNKNOWN", {"signal_id": sid, "link": link}, reconcile=True)
                self.save()
                return out | {"action": "ACK_UNKNOWN"}
            p["status"], p["order_id"], p["ack_ms"] = (
                "ACKED",
                o.get("orderId"),
                self.client.now_ms(),
            )
        self._j(
            "ENTRY_ACKED",
            {
                "signal_id": sid,
                "ack_ts": _iso(p["ack_ms"]),
                "ack_latency_ms": int(p["ack_ms"]) - int(p["order_sent_ms"]),
            },
        )
        self.state["last_order"] = {"link": link, "at": _iso(p["ack_ms"]), "status": p["status"]}
        self.save()
        self._resolve_fill(p)
        if self.state["position"] is not None and self.state["position"]["status"] == "ACTIVE":
            self._ensure_protection(self.state["position"], None)
        self.save()
        return out | {
            "action": "ENTERED" if self.state["position"] else "NO_FILL",
            "latency": {
                "decision_to_sent_ms": int(p["order_sent_ms"]) - p["decision_ms"],
                "sent_to_ack_ms": (int(p["ack_ms"]) - int(p["order_sent_ms"]))
                if p.get("ack_ms")
                else None,
                "sent_to_fill_ms": (int(p["fill_ms"]) - int(p["order_sent_ms"]))
                if p.get("fill_ms")
                else None,
            },
        }

    def _resolve_fill(self, p: dict[str, Any]) -> None:
        deadline = time.time() + self.dcfg.failsafe.fill_timeout_s
        o: dict[str, Any] | None = None
        while True:
            with contextlib.suppress(DemoApiError, DemoTransportError):
                o = self.client.order(p["link_entry"])
            if o and o.get("orderStatus") in (
                "Filled",
                "PartiallyFilledCanceled",
                "Cancelled",
                "Rejected",
                "Deactivated",
            ):
                break
            if time.time() >= deadline:
                self.alert("ENTRY_FILL_UNRESOLVED", {"signal_id": p["signal_id"]}, reconcile=True)
                return
            time.sleep(0.5)
        qty = _f(o.get("cumExecQty")) if o else math.nan
        if not qty or qty <= 0:
            self._j("ENTRY_NOT_FILLED", {"signal_id": p["signal_id"], "order": o})
            self.state["position"] = None
            return
        oid = str((o or {}).get("orderId") or "") or None
        try:
            agg = confirm_executions(
                lambda: self.client.executions(link_id=p["link_entry"]),
                qty,
                oid,
                p["link_entry"],
                self.dcfg.failsafe.exec_confirm_timeout_s,
            )
            rec = confirmed_record(agg, p["link_entry"], oid, "entry")
            self._j("EXECUTION_CONFIRMED", rec | {"signal_id": p["signal_id"]})
            price, fee, fill_ms, confirmed = (
                float(rec["avg_price"]),
                float(rec["total_fee"]),
                int(rec["last_exec_ms"]),
                True,
            )
        except ExecutionConfirmationTimeoutError as e:
            self.alert(
                "ENTRY_EXECUTION_UNCONFIRMED",
                {"signal_id": p["signal_id"], "last": e.last},
                reconcile=True,
            )
            price, fee, fill_ms, confirmed = (
                _f((o or {}).get("avgPrice")),
                math.nan,
                self.client.now_ms(),
                False,
            )
        p.update(
            {
                "status": "ACTIVE",
                "filled_qty": qty,
                "entry_price": price,
                "entry_fee": fee,
                "execution_confirmed": confirmed,
                "fill_ms": fill_ms,
            }
        )
        self.state["last_fill"] = {
            "link": p["link_entry"],
            "qty": qty,
            "price": price,
            "at": _iso(fill_ms),
        }
        self._j(
            "ENTRY_FILLED",
            {
                "signal_id": p["signal_id"],
                "qty": qty,
                "avg_price": price,
                "fee": fee,
                "fill_ts": _iso(fill_ms),
                "fill_latency_ms": fill_ms - int(p["order_sent_ms"]),
            },
        )
        self.save()

    def _ensure_protection(self, p: dict[str, Any], pos: dict[str, Any] | None) -> None:
        inst = self._instrument()
        try:
            pos = pos or self.client.position()
            want = fmt_price(p["stop"], inst["tick"])
            if pos.get("stop_loss") is None or abs(float(pos["stop_loss"]) - float(want)) > 1e-9:
                self.client.trading_stop(stop_loss=want)
                self._j("STOP_SET", {"signal_id": p["signal_id"], "stop": want})
            ex = self.cfg.exits
            for leg, (px_level, frac) in {
                "T1": (p["tp1"], ex.tp1_frac),
                "T2": (p["tp2"], 1.0 - ex.tp1_frac),
            }.items():
                known = p["legs"].get(leg)
                if known and (known.get("placed") or known.get("reason") == "BELOW_MIN_QTY"):
                    continue
                link = v10_link_id(p["signal_id"], leg)
                if known and not known.get("placed") and self.client.order(link) is not None:
                    known["placed"] = True
                    continue
                q = fmt_qty(p["filled_qty"] * frac, inst["qty_step"])
                px = fmt_price(px_level, inst["tick"])
                if float(q) < inst["min_qty"] - 1e-12:
                    p["legs"][leg] = {
                        "link": link,
                        "price": px,
                        "qty": q,
                        "placed": False,
                        "reason": "BELOW_MIN_QTY",
                    }
                    self._j(
                        "TP_LEG_NOT_PLACED",
                        {
                            "signal_id": p["signal_id"],
                            "leg": leg,
                            "qty": q,
                            "min_qty": inst["min_qty"],
                        },
                    )
                    continue
                p["legs"][leg] = {"link": link, "price": px, "qty": q, "placed": False}
                self.save()
                try:
                    self.client.create_order(
                        _bybit_side(p["side"], closing=True),
                        q,
                        "Limit",
                        link,
                        price=px,
                        reduce_only=True,
                    )
                except DemoApiError as e:
                    if e.ret_code != RET_DUPLICATE_LINK_ID:
                        raise
                p["legs"][leg]["placed"] = True
                self._j(
                    "TP_PLACED",
                    {"signal_id": p["signal_id"], "leg": leg, "link": link, "price": px, "qty": q},
                )
            self.save()
        except (DemoApiError, DemoTransportError) as e:
            self.alert(
                "PROTECTION_NOT_CONFIRMED",
                {"signal_id": p["signal_id"], "error": str(e)[:200]},
                reconcile=True,
            )
            self.save()

    # ------------------------------------------------------------------ management
    def manage(self, now_ms: int) -> dict[str, Any]:
        p = self.state.get("position")
        if p is None:
            return {"action": "none"}
        if p["status"] != "ACTIVE":
            return {"action": "pending_fill"}
        s = float(p["side"])
        ex = self.cfg.exits
        try:
            pos = self.client.position()
            self.state["last_exchange_position"] = pos
            if pos["size"] <= 0:
                return self._finalize(p)
            for leg in ("T1", "T2"):
                lg = p["legs"].get(leg)
                if lg and lg.get("placed") and not lg.get("filled"):
                    o = self.client.order(lg["link"])
                    if o and o.get("orderStatus") == "Filled":
                        lg["filled"], lg["fill_price"] = True, _f(o.get("avgPrice"))
                        p["tp1_done"] = p["tp1_done"] or leg == "T1"
                        self._j(
                            "TP_FILLED",
                            {
                                "signal_id": p["signal_id"],
                                "leg": leg,
                                "price": lg["fill_price"],
                                "qty": lg["qty"],
                            },
                        )
            if pos.get("stop_loss") is None:
                self._ensure_protection(p, pos)
            held = now_ms - int(p["fill_ms"])
            close_reason = None
            if not p["time_checked"] and held >= ex.time_stop_min * 60_000:
                p["time_checked"] = True
                tk = self.client.ticker()
                r = (
                    s
                    * (float(tk["last"]) - float(p["entry_price"]))
                    / abs(float(p["entry_price"]) - float(p["stop"]))
                )
                if r < ex.progress_min_r:
                    close_reason = "TIME"
            if held >= ex.hard_max_min * 60_000:
                close_reason = "HARD"
            if close_reason:
                for leg in ("T1", "T2"):
                    lg = p["legs"].get(leg)
                    if lg and lg.get("placed") and not lg.get("filled"):
                        with contextlib.suppress(DemoApiError):
                            self.client.cancel_order(lg["link"])
                inst = self._instrument()
                link = v10_link_id(p["signal_id"], "TC")
                try:
                    self.client.create_order(
                        _bybit_side(p["side"], closing=True),
                        fmt_qty(pos["size"], inst["qty_step"]),
                        "Market",
                        link,
                        reduce_only=True,
                    )
                except DemoApiError as e:
                    if e.ret_code != RET_DUPLICATE_LINK_ID:
                        raise
                p["close_reason"] = close_reason
                self._j(
                    "TIME_CLOSE",
                    {
                        "signal_id": p["signal_id"],
                        "reason": close_reason,
                        "link": link,
                        "qty": pos["size"],
                    },
                )
                pos = self.client.position()
                if pos["size"] <= 0:
                    return self._finalize(p)
            self.save()
            return {"action": "managed", "size": pos["size"]}
        except (DemoApiError, DemoTransportError) as e:
            self._j("MANAGE_ERROR", {"signal_id": p["signal_id"], "error": str(e)[:200]})
            self.save()
            return {"action": "error", "error": str(e)[:200]}

    def _finalize(self, p: dict[str, Any]) -> dict[str, Any]:
        start = int(p["order_sent_ms"] or p["decision_ms"]) - 1000
        s = float(p["side"])
        bside = _bybit_side(p["side"])
        ex: list[dict[str, Any]] = []
        q_in = q_out = 0.0
        complete = False
        for wait in [*backoff_schedule(self.dcfg.failsafe.exec_confirm_timeout_s), None]:
            ex, _d = dedupe_executions(
                [
                    e
                    for e in self.client.executions(start_ms=start)
                    if not is_smoke_link_id(e.get("orderLinkId"))
                    and e.get("execType", "Trade") not in ("Funding", "Settle", "Delivery")
                ]
            )
            q_in = sum(_f(e["execQty"]) for e in ex if e.get("side") == bside)
            q_out = sum(_f(e["execQty"]) for e in ex if e.get("side") != bside)
            complete = (
                abs(q_in - float(p.get("filled_qty") or 0)) <= QTY_EPS
                and abs(q_out - q_in) <= QTY_EPS
                and all(e.get("execFee") not in (None, "") for e in ex)
            )
            if complete or wait is None:
                break
            time.sleep(wait)
        if not complete:
            if not p.get("finalize_alerted"):
                p["finalize_alerted"] = True
                self.alert(
                    "FLAT_EXECUTIONS_INCOMPLETE",
                    {"signal_id": p["signal_id"], "q_in": q_in, "q_out": q_out},
                )
                self.save()
            return {"action": "finalize_pending"}
        v_in = sum(_f(e["execQty"]) * _f(e["execPrice"]) for e in ex if e.get("side") == bside)
        v_out = sum(_f(e["execQty"]) * _f(e["execPrice"]) for e in ex if e.get("side") != bside)
        fees = sum(_f(e.get("execFee")) for e in ex)
        funding = 0.0
        with contextlib.suppress(DemoApiError, DemoTransportError):
            funding = sum(
                _f(x.get("funding") or x.get("change") or 0) for x in self.client.funding(start)
            )
        gross = s * (v_out - v_in)
        net = gross - fees + funding
        risk = float(p["filled_qty"]) * abs(float(p["entry_price"]) - float(p["stop"]))
        exits = [
            {
                "price": _f(e["execPrice"]),
                "qty": _f(e["execQty"]),
                "link": e.get("orderLinkId") or "exchange_tpsl",
                "time": _iso(int(e["execTime"])),
            }
            for e in ex
            if e.get("side") != bside
        ]
        kinds = {str(x["link"]).rsplit("-", 1)[-1] for x in exits if is_v10_link_id(str(x["link"]))}
        reason = p.get("close_reason") or (
            "TP2" if "T2" in kinds else "TP1_THEN_STOP" if "T1" in kinds else "STOP"
        )
        now = self.client.now_ms()
        trade = {
            "strategy": STRATEGY,
            "signal_id": p["signal_id"],
            "episode_id": p.get("episode_id"),
            "family": p.get("family"),
            "side": "LONG" if s > 0 else "SHORT",
            "entry_ref": p["entry_ref"],
            "entry_price": p["entry_price"],
            "stop": p["stop"],
            "tp1": p["tp1"],
            "tp2": p["tp2"],
            "qty": p["filled_qty"],
            "leverage": p["leverage"],
            "planned_risk_usdt": p["planned_risk_usdt"],
            "actual_risk_usdt": risk,
            "exits": exits,
            "exit_reason": reason,
            "fees": fees,
            "funding": funding,
            "gross_pnl": gross,
            "net_pnl": net,
            "R_net_vs_planned": net / p["planned_risk_usdt"] if p["planned_risk_usdt"] else None,
            "R_net_vs_actual": net / risk if risk else None,
            "execution_mode": "DEMO",
            "decision_ts": _iso(p["decision_ms"]),
            "order_sent_ts": _iso(p.get("order_sent_ms")),
            "ack_ts": _iso(p.get("ack_ms")),
            "fill_ts": _iso(p.get("fill_ms")),
            "closed_ts": _iso(now),
            "closed_ms": now,
        }
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trades_path.open("a") as fh:
            fh.write(json.dumps(trade, sort_keys=True, default=str) + "\n")
        self.state["closed_trades"] = int(self.state["closed_trades"]) + 1
        self.state["cum_net_pnl"] = float(self.state["cum_net_pnl"]) + net
        self.state["wins" if net > 0 else "losses"] = (
            int(self.state["wins" if net > 0 else "losses"]) + 1
        )
        self._j("TRADE_CLOSED", trade)
        self.state["position"] = None
        self.save()
        return {"action": "closed", "trade": trade}
