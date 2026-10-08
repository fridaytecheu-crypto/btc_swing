"""STRATEGY_DEMO: mirror the FROZEN V5 forward strategy on Bybit DEMO. Execution validation only.

What decides a trade: only the frozen V5 engine. A subclass of the frozen engine records every
trigger the frozen `_size` accepts (plan, finalised stop, trigger close); nothing in V5 is changed.
A demo entry is sent only for a trigger on the bar that has just closed (the frozen fill is the
next 5m open), whose signal id exists in the immutable forward signal journal, when no fail-safe
is active. No V5 signal -> no strategy order.

Sizing: the frozen `size_position` (same leverage ladder, margin cap and liquidation constraints)
on the REFERENCE equity (100 USDT by default), never on the demo wallet. The quantity is floored to
the exchange step; a quantity below the exchange minimum is NOT rounded up (that would raise the
risk above 0.25%): the trade is skipped and journaled as SKIPPED_BELOW_MIN_QTY.

Exits (frozen rules, demo mechanics): exchange stop at the frozen stop (LastPrice trigger), TP1
(+1R, 40%) and TP2 (+2R, 30%) as reduce-only limit orders measured from the actual fill, stop to
breakeven after TP1, 1H swing trail (the frozen engine's own `_update_trail`) after TP1, 24 h time
cap closed at market. A TP leg below the exchange minimum is not placed (journaled).

Safety: write-ahead state before every order, deterministic orderLinkIds from the signal id, Bybit
rejects duplicates, restart recovery queries the exchange and never resubmits, fail-safes block new
entries but keep managing an existing position, every action is journaled hash-chained.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from btc_swing.backtest.ledger import Position
from btc_swing.core.enums import Side
from btc_swing.risk.sizing import size_position
from btc_swing.v5.config import V5Config
from btc_swing.v5.demo.client import (
    RET_DUPLICATE_LINK_ID,
    BybitDemoClient,
    DemoApiError,
    DemoTransportError,
    fmt_price,
    fmt_qty,
)
from btc_swing.v5.demo.config import STRATEGY_TAG, DemoExecConfig, ExecutionMode
from btc_swing.v5.demo.fills import (
    QTY_EPS,
    ExecutionConfirmationTimeoutError,
    backoff_schedule,
    confirm_executions,
    confirmed_record,
    dedupe_executions,
)
from btc_swing.v5.demo.ids import is_smoke_link_id, is_strategy_link_id, strategy_link_id
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.engine import V5Engine

log = logging.getLogger(__name__)
DEMO_STATE_VERSION = 1


# ----------------------------------------------------------------------------- trigger capture
class CapturingEngine(V5Engine):
    """The frozen V5 engine, unchanged, plus a record of every accepted trigger."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.captured: list[dict[str, Any]] = []

    def _size(
        self,
        ep: Any,
        view: Any,
        close: float,
        equity: float,
        bar: int,
        t: int,
        mgr: Any,
        reg: Any,
        closed: list[dict[str, Any]],
    ) -> Any:
        p = super()._size(ep, view, close, equity, bar, t, mgr, reg, closed)
        if p is not None:
            tf = self.cfg.risk.liquidation_atr_tf
            plan = ep.plan
            self.captured.append(
                {
                    "t_ms": int(t),
                    "family": plan.family.value,
                    "side": plan.side.value,
                    "event_ms": int(plan.notes.get("event_ms", -1)),
                    "trigger_close": float(close),
                    "stop": float(plan.stop_price),
                    "atr_setup": float(plan.atr_setup_tf),
                    "atr_liq": float(view.ind(tf, "atr")) if view.warm(tf) else math.nan,
                    "strength": float(plan.notes.get("strength", math.nan)),
                }
            )
        return p


@dataclass(frozen=True)
class Trigger:
    t_ms: int
    family: str
    side: str
    event_ms: int
    trigger_close: float
    stop: float
    atr_setup: float
    atr_liq: float
    strength: float = math.nan

    @property
    def signal_id(self) -> str:
        return f"{self.family}|{self.side}|{self.event_ms}"

    @property
    def sign(self) -> float:
        return 1.0 if self.side == "LONG" else -1.0


def _bybit_side(side: str, closing: bool = False) -> str:
    long = side == "LONG"
    return ("Sell" if long else "Buy") if closing else ("Buy" if long else "Sell")


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _iso(ms: float | int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(float(ms) / 1000, tz=UTC).isoformat()


# ----------------------------------------------------------------------------- executor
class StrategyDemoExecutor:
    def __init__(
        self,
        cfg: V5Config,
        dcfg: DemoExecConfig,
        client: BybitDemoClient,
        journal: HashChainJournal,
        state_path: Path,
        trades_path: Path,
        signals_path: Path,
        paper_trades_path: Path,
        smoke_report: dict[str, Any] | None,
        clock: Any = time.time,
        sleep: Any = time.sleep,
        activated_at_ms: int | None = None,
    ) -> None:
        """`activated_at_ms`: the STRATEGY_DEMO_ACTIVATED timestamp. Only triggers whose bar closed
        strictly after it can be traded; without it nothing is ever entered (fails closed)."""
        self.activated_at_ms = activated_at_ms
        if dcfg.mode is not ExecutionMode.STRATEGY_DEMO:
            raise RuntimeError("StrategyDemoExecutor requires mode STRATEGY_DEMO")
        if not smoke_report or smoke_report.get("status") != "PASSED":
            raise RuntimeError("STRATEGY_DEMO requires a PASSED EXECUTION_SMOKE report first")
        if abs(dcfg.risk_per_trade - cfg.risk.risk_per_trade) > 1e-12:
            raise RuntimeError("demo risk_per_trade differs from the frozen V5 risk_per_trade")
        self.cfg, self.dcfg, self.client, self.journal = cfg, dcfg, client, journal
        self.state_path, self.trades_path = state_path, trades_path
        self.signals_path, self.paper_trades_path = signals_path, paper_trades_path
        self._clock, self._sleep = clock, sleep
        self.state: dict[str, Any] = self._load_state()

    # ------------------------------------------------------------------ state
    def _default_state(self) -> dict[str, Any]:
        return {
            "version": DEMO_STATE_VERSION,
            "position": None,
            "processed_signals": [],
            "reconcile_required": False,
            "reconcile_reasons": [],
            "alerts": [],
            "last_seq_gaps": None,
            "instrument": None,
            "last_exchange_position": None,
            "last_order": None,
            "last_fill": None,
            "errors": [],
            "demo_closed_trades": 0,
            "demo_cum_net_pnl": 0.0,
            "paper_cum_pnl_ref": 0.0,
        }

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return {**self._default_state(), **json.loads(self.state_path.read_text())}
        return self._default_state()

    def save(self) -> None:
        if self.client.last_ok_ms is not None:
            self.state["last_api_ok_ms"] = self.client.last_ok_ms
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True, default=str))
        tmp.replace(self.state_path)

    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _j(self, kind: str, data: dict[str, Any]) -> None:
        self.journal.append(kind, data, STRATEGY_TAG)

    def alert(
        self, reason: str, detail: dict[str, Any] | None = None, reconcile: bool = False
    ) -> None:
        rec = {"at": _iso(self.now_ms()), "reason": reason, "detail": detail or {}}
        self.state["alerts"] = ([*self.state["alerts"], rec])[-50:]
        if reconcile:
            self.state["reconcile_required"] = True
            self.state["reconcile_reasons"] = [*self.state["reconcile_reasons"], rec][-20:]
        self._j("ALERT", rec | {"reconcile_required": reconcile})
        log.warning("demo alert: %s %s", reason, detail or "")

    def error(self, where: str, e: Exception) -> None:
        rec = {"at": _iso(self.now_ms()), "where": where, "error": f"{type(e).__name__}: {e}"[:300]}
        self.state["errors"] = [*self.state["errors"], rec][-50:]
        self._j("ERROR", rec)

    def acknowledge_reconciliation(self, note: str) -> None:
        self._j(
            "RECONCILIATION_ACKNOWLEDGED",
            {"note": note, "reasons": self.state["reconcile_reasons"]},
        )
        self.state["reconcile_required"] = False
        self.state["reconcile_reasons"] = []
        self.save()

    def _instrument(self) -> dict[str, Any]:
        if not self.state.get("instrument"):
            self.state["instrument"] = self.client.instrument()
        return dict(self.state["instrument"])

    # ------------------------------------------------------------------ recovery
    def recover(self) -> dict[str, Any]:
        """Reconcile the local journal/state with Bybit after a (re)start. Never resubmits."""
        out: dict[str, Any] = {"at": _iso(self.now_ms())}
        try:
            pos = self.client.position()
            opens = self.client.open_orders()
        except (DemoApiError, DemoTransportError) as e:
            self.error("recover", e)
            self.alert("RECOVERY_EXCHANGE_UNREACHABLE", {"error": str(e)[:200]}, reconcile=True)
            self.save()
            return out | {"ok": False}
        self.state["last_exchange_position"] = pos
        strat_open = [o for o in opens if is_strategy_link_id(o.get("orderLinkId"))]
        p = self.state["position"]
        out.update(
            {
                "exchange_position": pos,
                "strategy_open_orders": [o.get("orderLinkId") for o in strat_open],
                "local_status": p and p["status"],
            }
        )
        if p is None:
            if pos["size"] > 0:
                self.alert("UNEXPECTED_OPEN_POSITION", {"position": pos}, reconcile=True)
            if strat_open:
                self.alert(
                    "UNEXPECTED_STRATEGY_ORDERS",
                    {"orders": [o.get("orderLinkId") for o in strat_open]},
                    reconcile=True,
                )
        else:
            st = p["status"]
            if st in ("SUBMITTING", "ACK_UNKNOWN"):
                o = self.client.order(p["link_entry"])
                if o is None:
                    if pos["size"] > 0:
                        self.alert(
                            "POSITION_WITHOUT_KNOWN_ENTRY",
                            {"position": pos, "link": p["link_entry"]},
                            reconcile=True,
                        )
                    else:
                        p["status"] = "ABANDONED_NO_ACK"
                        self._j(
                            "ENTRY_NOT_ON_EXCHANGE",
                            {
                                "signal_id": p["signal_id"],
                                "link": p["link_entry"],
                                "action": "abandoned; never resubmitted",
                            },
                        )
                        self._close_local(p, reason="ABANDONED_NO_ACK")
                else:
                    p["status"] = "ACKED"
                    p["order_id"] = o.get("orderId")
                    self._j("ENTRY_FOUND_ON_EXCHANGE", {"signal_id": p["signal_id"], "order": o})
            if self.state["position"] is not None and self.state["position"]["status"] == "ACKED":
                self._resolve_fill(self.state["position"])
            if self.state["position"] is not None and self.state["position"]["status"] == "ACTIVE":
                self._ensure_protection(self.state["position"], pos)
                self._check_size(self.state["position"], self.client.position())
        self._j("RECOVERY", out)
        self.save()
        return out | {"ok": True, "reconcile_required": self.state["reconcile_required"]}

    # ------------------------------------------------------------------ entry
    def enter(self, trig: Trigger, blockers: list[str]) -> dict[str, Any]:
        sid = trig.signal_id
        decision: dict[str, Any] = {
            "signal_id": sid,
            "trigger": asdict(trig),
            "trigger_at": _iso(trig.t_ms),
        }
        if sid in self.state["processed_signals"]:
            self.alert("DUPLICATE_SIGNAL", {"signal_id": sid})
            return decision | {"action": "SKIPPED_DUPLICATE_SIGNAL"}
        if self.activated_at_ms is None or int(trig.t_ms) <= int(self.activated_at_ms):
            # only genuine prospective signals produced after STRATEGY_DEMO_ACTIVATED
            self.state["processed_signals"] = [*self.state["processed_signals"], sid][-2000:]
            self._j(
                "PRE_ACTIVATION_SIGNAL_REFUSED",
                decision | {"activated_at": _iso(self.activated_at_ms)},
            )
            self.save()
            return decision | {"action": "REFUSED_BEFORE_ACTIVATION"}
        self.state["processed_signals"] = [*self.state["processed_signals"], sid][-2000:]
        if blockers:
            self._j("ENTRY_BLOCKED", decision | {"blockers": blockers})
            self.alert("ENTRY_BLOCKED_BY_FAILSAFE", {"signal_id": sid, "blockers": blockers})
            self.save()
            return decision | {"action": "BLOCKED", "blockers": blockers}
        link = strategy_link_id(sid, "EN")
        try:
            if self.client.order(link) is not None:
                self.alert(
                    "DUPLICATE_SIGNAL",
                    {
                        "signal_id": sid,
                        "link": link,
                        "note": "entry orderLinkId already on the exchange",
                    },
                    reconcile=True,
                )
                self.save()
                return decision | {"action": "SKIPPED_DUPLICATE_ON_EXCHANGE"}
            inst = self._instrument()
        except (DemoApiError, DemoTransportError) as e:
            self.error("enter.precheck", e)
            self.save()
            return decision | {"action": "BLOCKED", "blockers": ["EXCHANGE_UNREACHABLE"]}
        side = Side(trig.side)
        sz = size_position(
            self.dcfg.reference_equity_usdt,
            trig.trigger_close,
            trig.stop,
            side,
            trig.atr_liq,
            self.cfg.risk,
        )
        sizing = {
            "reference_equity": self.dcfg.reference_equity_usdt,
            "risk_amount": sz.risk_amount,
            "qty_raw": sz.qty,
            "leverage": sz.leverage,
            "notional": sz.notional,
            "accepted": sz.accepted,
            "reason": sz.reason,
            "min_qty": inst["min_qty"],
            "qty_step": inst["qty_step"],
        }
        if not sz.accepted:
            self._j("SKIPPED_RISK_REJECTED", decision | {"sizing": sizing})
            self.save()
            return decision | {"action": "SKIPPED_RISK_REJECTED", "sizing": sizing}
        qty_s = fmt_qty(sz.qty, inst["qty_step"])
        sizing["qty"] = qty_s
        if float(qty_s) < inst["min_qty"] - 1e-12:
            sizing["reference_equity_needed_for_min_qty"] = (
                inst["min_qty"] * abs(trig.trigger_close - trig.stop) / self.dcfg.risk_per_trade
            )
            self._j(
                "SKIPPED_BELOW_MIN_QTY",
                decision
                | {
                    "sizing": sizing,
                    "note": "not rounded up: rounding up would exceed the frozen 0.25% risk",
                },
            )
            self.save()
            return decision | {"action": "SKIPPED_BELOW_MIN_QTY", "sizing": sizing}
        try:
            self.client.set_leverage(sz.leverage)
            tk = self.client.ticker()
        except (DemoApiError, DemoTransportError) as e:
            self.error("enter.leverage", e)
            self.save()
            return decision | {"action": "BLOCKED", "blockers": ["EXCHANGE_ERROR_BEFORE_ORDER"]}
        p: dict[str, Any] = {
            "signal_id": sid,
            "family": trig.family,
            "side": trig.side,
            "trigger_ms": trig.t_ms,
            "trigger_close": trig.trigger_close,
            "stop0": trig.stop,
            "stop": trig.stop,
            "atr_setup": trig.atr_setup,
            "link_entry": link,
            "status": "SUBMITTING",
            "planned_qty": float(qty_s),
            "leverage": sz.leverage,
            "planned_risk_usdt": sz.risk_amount,
            "submitted_price": tk["last"],
            "submitted_ms": self.client.now_ms(),
            "filled_qty": 0.0,
            "entry_price": None,
            "legs": {},
            "tp1_done": False,
            "tp2_done": False,
            "breakeven_done": False,
            "pending_trail": None,
        }
        self.state["position"] = p
        self._j(
            "ENTRY_SUBMIT",
            {
                "signal_id": sid,
                "link": link,
                "side": _bybit_side(trig.side),
                "qty": qty_s,
                "sizing": sizing,
                "submitted_price": tk["last"],
            },
        )
        self.save()  # write-ahead: a crash after this line never leads to a second order
        try:
            r = self.client.create_order(_bybit_side(trig.side), qty_s, "Market", link)
            p["status"], p["order_id"] = "ACKED", r.get("orderId")
        except DemoApiError as e:
            if e.ret_code == RET_DUPLICATE_LINK_ID:
                p["status"] = "ACKED"
            else:
                self.error("enter.create", e)
                self._close_local(p, reason=f"REJECTED_{e.ret_code}")
                self.save()
                return decision | {"action": "REJECTED", "error": str(e)}
        except DemoTransportError as e:
            self.error("enter.create", e)
            p["status"] = "ACK_UNKNOWN"
            self.save()
            self.alert("ORDER_ACK_MISSING", {"signal_id": sid, "link": link})
            o = None
            try:
                o = self.client.order(link)
            except (DemoApiError, DemoTransportError) as e2:
                self.error("enter.lookup", e2)
            if o is None:
                self.alert("ORDER_STATE_UNKNOWN", {"signal_id": sid, "link": link}, reconcile=True)
                self.save()
                return decision | {"action": "ACK_UNKNOWN"}
            p["status"], p["order_id"] = "ACKED", o.get("orderId")
        self.state["last_order"] = {
            "link": link,
            "at": _iso(self.client.now_ms()),
            "status": p["status"],
        }
        self.save()
        self._resolve_fill(p)
        if self.state["position"] is not None and self.state["position"]["status"] == "ACTIVE":
            self._ensure_protection(self.state["position"], None)
        self.save()
        return decision | {
            "action": "ENTERED" if self.state["position"] else "NO_FILL",
            "sizing": sizing,
        }

    def _resolve_fill(self, p: dict[str, Any]) -> None:
        deadline = time.time() + self.dcfg.failsafe.fill_timeout_s
        o: dict[str, Any] | None = None
        while True:
            try:
                o = self.client.order(p["link_entry"])
            except (DemoApiError, DemoTransportError) as e:
                self.error("resolve_fill", e)
                o = None
            if o and o.get("orderStatus") in (
                "Filled",
                "PartiallyFilledCanceled",
                "Cancelled",
                "Rejected",
                "Deactivated",
            ):
                break
            if time.time() >= deadline:
                self.alert(
                    "ENTRY_FILL_UNRESOLVED",
                    {"signal_id": p["signal_id"], "last_status": o and o.get("orderStatus")},
                    reconcile=True,
                )
                return
            self._sleep(0.5)
        qty = _f(o.get("cumExecQty"))
        if not qty or qty <= 0:
            self._j("ENTRY_NOT_FILLED", {"signal_id": p["signal_id"], "order": o})
            self._close_local(p, reason=f"NO_FILL_{o.get('orderStatus')}")
            return
        # Filled is provisional until the executions are visible (Bybit creates orders
        # asynchronously): journal it, then wait (bounded) for definitive execution accounting.
        oid = str(o.get("orderId") or "") or None
        prov = {
            "signal_id": p["signal_id"],
            "leg": "entry",
            "orderLinkId": p["link_entry"],
            "orderId": oid,
            "orderStatus": o.get("orderStatus"),
            "cumExecQty": qty,
            "avgPrice": _f(o.get("avgPrice")),
            "cumExecFee": o.get("cumExecFee"),
        }
        self._j("ORDER_FILLED_PROVISIONAL", prov)
        try:
            agg = confirm_executions(
                lambda: self.client.executions(link_id=p["link_entry"]),
                qty,
                oid,
                p["link_entry"],
                self.dcfg.failsafe.exec_confirm_timeout_s,
                sleep=self._sleep,
                clock=self._clock,
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
            # the position exists on the exchange and must still be protected: keep the provisional
            # order values, flag them, and require an operator reconciliation (no new entries)
            self._j("EXECUTION_CONFIRMATION_TIMEOUT", prov | {"last": e.last})
            self.alert("ENTRY_EXECUTION_UNCONFIRMED", prov, reconcile=True)
            price, fee, fill_ms, confirmed = (
                _f(o.get("avgPrice")),
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
                "partial": o.get("orderStatus") == "PartiallyFilledCanceled",
            }
        )
        p["fill_latency_ms"] = p["fill_ms"] - int(p["submitted_ms"])
        self.state["last_fill"] = {
            "link": p["link_entry"],
            "qty": qty,
            "price": p["entry_price"],
            "at": _iso(p["fill_ms"]),
        }
        self._j(
            "ENTRY_FILLED",
            {
                "signal_id": p["signal_id"],
                "qty": qty,
                "avg_price": p["entry_price"],
                "fee": p["entry_fee"],
                "execution_confirmed": p["execution_confirmed"],
                "partial": p["partial"],
                "fill_latency_ms": p["fill_latency_ms"],
            },
        )
        if p["partial"]:
            self.alert(
                "PARTIAL_ENTRY_FILL",
                {"signal_id": p["signal_id"], "planned": p["planned_qty"], "filled": qty},
            )
        self.save()

    def _ensure_protection(self, p: dict[str, Any], pos: dict[str, Any] | None) -> None:
        """Idempotent: exchange stop at the current frozen stop; TP legs placed once (by link id)."""
        inst = self._instrument()
        s = 1.0 if p["side"] == "LONG" else -1.0
        try:
            pos = pos or self.client.position()
            want = fmt_price(p["stop"], inst["tick"])
            if pos.get("stop_loss") is None or abs(float(pos["stop_loss"]) - float(want)) > 1e-9:
                self.client.trading_stop(stop_loss=want)
                self._j(
                    "STOP_SET",
                    {
                        "signal_id": p["signal_id"],
                        "stop": want,
                        "reason": "attach" if pos.get("stop_loss") is None else "sync",
                    },
                )
            r = s * (p["entry_price"] - p["stop0"])
            fr = {
                "T1": (self.cfg.exits.tp1_r, self.cfg.exits.tp1_frac),
                "T2": (self.cfg.exits.tp2_r, self.cfg.exits.tp2_frac),
            }
            for leg, (rr, frac) in fr.items():
                known = p["legs"].get(leg)
                if known and (known.get("placed") or known.get("reason") == "BELOW_MIN_QTY"):
                    continue
                # write-ahead record without confirmation (crash in between): ask the exchange
                if (
                    known
                    and not known.get("placed")
                    and self.client.order(known["link"]) is not None
                ):
                    known["placed"] = True
                    self._j(
                        "TP_FOUND_ON_EXCHANGE",
                        {"signal_id": p["signal_id"], "leg": leg, "link": known["link"]},
                    )
                    continue
                q = fmt_qty(p["filled_qty"] * frac, inst["qty_step"])
                px = fmt_price(p["entry_price"] + s * rr * r, inst["tick"])
                link = strategy_link_id(p["signal_id"], leg)
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
                            "note": "remaining quantity stays under stop / trail / time cap",
                        },
                    )
                    continue
                p["legs"][leg] = {"link": link, "price": px, "qty": q, "placed": False}
                self.save()  # write-ahead
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
                    {
                        "signal_id": p["signal_id"],
                        "leg": leg,
                        "link": link,
                        "price": px,
                        "qty": q,
                        "r_multiple": rr,
                    },
                )
            self.save()
        except (DemoApiError, DemoTransportError) as e:
            self.error("ensure_protection", e)
            self.alert(
                "PROTECTION_NOT_CONFIRMED",
                {"signal_id": p["signal_id"], "error": str(e)[:200]},
                reconcile=True,
            )
            self.save()

    def _check_size(self, p: dict[str, Any], pos: dict[str, Any]) -> None:
        expected = p["filled_qty"] - sum(
            float(lg["qty"])
            for k, lg in p["legs"].items()
            if lg.get("filled") and k in ("T1", "T2")
        )
        bside = _bybit_side(p["side"])
        if pos["size"] > 0 and (
            pos["side"] != bside
            or pos["size"] > p["filled_qty"] + 1e-9
            or abs(pos["size"] - expected) > 1e-9
        ):
            self.alert("POSITION_MISMATCH", {"expected": expected, "exchange": pos}, reconcile=True)

    # ------------------------------------------------------------------ management
    def manage(self, trail_candidate: float | None, now_ms: int) -> dict[str, Any]:
        """One management step for an ACTIVE position with the frozen exit rules."""
        p = self.state["position"]
        if p is None or p["status"] != "ACTIVE":
            return {"action": "none"}
        s = 1.0 if p["side"] == "LONG" else -1.0
        out: dict[str, Any] = {"signal_id": p["signal_id"]}
        try:
            pos = self.client.position()
            self.state["last_exchange_position"] = pos
            if pos["size"] <= 0:
                return out | self._finalize(p)
            for leg in ("T1", "T2"):
                lg = p["legs"].get(leg)
                if lg and lg.get("placed") and not lg.get("filled"):
                    o = self.client.order(lg["link"])
                    if o and o.get("orderStatus") == "Filled":
                        lg["filled"] = True
                        lg["fill_price"] = _f(o.get("avgPrice"))
                        p[f"tp{leg[1]}_done"] = True
                        self._j(
                            "TP_FILLED",
                            {
                                "signal_id": p["signal_id"],
                                "leg": leg,
                                "price": lg["fill_price"],
                                "qty": lg["qty"],
                            },
                        )
                        self.state["last_fill"] = {
                            "link": lg["link"],
                            "qty": lg["qty"],
                            "price": lg["fill_price"],
                            "at": _iso(now_ms),
                        }
            # frozen engine: TP1 is evaluated on the bar's favourable extreme; a TP1 leg too small to
            # place is "done" when price trades through the TP1 level (same bar logic, last price)
            t1 = p["legs"].get("T1")
            if t1 and not t1.get("placed") and not p["tp1_done"]:
                tk = self.client.ticker()
                if s * (tk["last"] - float(t1["price"])) >= 0:
                    p["tp1_done"] = True
                    self._j(
                        "TP1_LEVEL_REACHED",
                        {
                            "signal_id": p["signal_id"],
                            "last": tk["last"],
                            "note": "leg not placed (below min qty); breakeven and trail activate",
                        },
                    )
            new_stop = p["stop"]
            if (
                p["tp1_done"]
                and self.cfg.exits.breakeven_after_tp1
                and not p["breakeven_done"]
                and s * (p["entry_price"] - new_stop) > 0
            ):
                new_stop, p["breakeven_done"] = p["entry_price"], True
            if (
                p["tp1_done"]
                and p.get("pending_trail") is not None
                and s * (float(p["pending_trail"]) - new_stop) > 0
            ):
                new_stop = float(p["pending_trail"])
            # the frozen engine applies a trail candidate from the NEXT bar: store it now, apply next cycle
            p["pending_trail"] = (
                trail_candidate
                if p["tp1_done"]
                and trail_candidate is not None
                and s * (trail_candidate - new_stop) > 0
                else None
            )
            if s * (new_stop - p["stop"]) > 1e-9:
                inst = self._instrument()
                self.client.trading_stop(stop_loss=fmt_price(new_stop, inst["tick"]))
                self._j(
                    "STOP_MOVED",
                    {
                        "signal_id": p["signal_id"],
                        "from": p["stop"],
                        "to": new_stop,
                        "reason": "BREAKEVEN" if new_stop == p["entry_price"] else "TRAIL",
                    },
                )
                p["stop"] = new_stop
            elif pos.get("stop_loss") is None:
                self._ensure_protection(p, pos)
            if now_ms - int(p["fill_ms"]) >= self.cfg.exits.max_hold_hours * 3_600_000:
                for leg in ("T1", "T2"):
                    lg = p["legs"].get(leg)
                    if lg and lg.get("placed") and not lg.get("filled"):
                        with contextlib.suppress(DemoApiError):
                            self.client.cancel_order(lg["link"])
                inst = self._instrument()
                link = strategy_link_id(p["signal_id"], "TC")
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
                self._j(
                    "TIME_CAP_CLOSE",
                    {"signal_id": p["signal_id"], "link": link, "qty": pos["size"]},
                )
                pos = self.client.position()
                if pos["size"] <= 0:
                    return out | self._finalize(p)
            self._check_size(p, pos)
            self.save()
            return out | {
                "action": "managed",
                "stop": p["stop"],
                "pending_trail": p["pending_trail"],
                "size": pos["size"],
            }
        except (DemoApiError, DemoTransportError) as e:
            self.error("manage", e)
            self.save()
            return out | {"action": "error", "error": str(e)[:200]}

    def _close_local(self, p: dict[str, Any], reason: str) -> None:
        self._j(
            "POSITION_RECORD_CLOSED", {"signal_id": p["signal_id"], "reason": reason, "record": p}
        )
        self.state["position"] = None
        self.save()

    def _finalize(self, p: dict[str, Any]) -> dict[str, Any]:
        """Exchange position is flat: collect fills, fees, funding and PnL; compare with paper."""
        start = int(p["submitted_ms"]) - 1000
        s = 1.0 if p["side"] == "LONG" else -1.0
        bside = _bybit_side(p["side"])
        # flat is not enough: wait (bounded) until every entry and exit execution, with its fee,
        # is visible; otherwise do not finalise in this cycle (retried next cycle)
        ex: list[dict[str, Any]] = []
        q_in = q_out = 0.0
        dups = 0
        complete = False
        for wait in [*backoff_schedule(self.dcfg.failsafe.exec_confirm_timeout_s), None]:
            ex, dups = dedupe_executions(
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
                abs(q_in - float(p["filled_qty"])) <= QTY_EPS
                and abs(q_out - q_in) <= QTY_EPS
                and all(e.get("execFee") not in (None, "") for e in ex)
            )
            if complete or wait is None:
                break
            self._sleep(wait)
        if not complete:
            if not p.get("finalize_pending_alerted"):
                p["finalize_pending_alerted"] = True
                self.alert(
                    "FLAT_EXECUTIONS_INCOMPLETE",
                    {
                        "signal_id": p["signal_id"],
                        "filled_qty": p["filled_qty"],
                        "exec_qty_in": q_in,
                        "exec_qty_out": q_out,
                    },
                )
                self.save()
            return {"action": "finalize_pending", "exec_qty_in": q_in, "exec_qty_out": q_out}
        buys = [e for e in ex if e.get("side") == bside]
        sells = [e for e in ex if e.get("side") != bside]
        v_in = sum(_f(e["execQty"]) * _f(e["execPrice"]) for e in buys)
        v_out = sum(_f(e["execQty"]) * _f(e["execPrice"]) for e in sells)
        fees = sum(_f(e.get("execFee")) for e in ex)
        try:
            funding = sum(
                _f(x.get("funding") or x.get("change") or 0) for x in self.client.funding(start)
            )
        except (DemoApiError, DemoTransportError) as e:
            self.error("finalize.funding", e)
            funding = 0.0
        gross = s * (v_out - v_in * (q_out / q_in if q_in else 0))
        net = gross - fees + funding
        risk_actual = p["filled_qty"] * abs(p["entry_price"] - p["stop0"])
        closed = self.client.closed_pnl(start)
        exits = [
            {
                "price": _f(e["execPrice"]),
                "qty": _f(e["execQty"]),
                "link": e.get("orderLinkId") or "exchange_tpsl",
                "time": _iso(int(e["execTime"])),
            }
            for e in sells
        ]
        trade = {
            "signal_id": p["signal_id"],
            "family": p["family"],
            "side": p["side"],
            "trigger_at": _iso(p["trigger_ms"]),
            "expected_entry": p["trigger_close"],
            "stop0": p["stop0"],
            "final_stop": p["stop"],
            "tp_levels": {k: v["price"] for k, v in p["legs"].items()},
            "submitted_price": p["submitted_price"],
            "entry_price": p["entry_price"],
            "filled_qty": p["filled_qty"],
            "fill_latency_ms": p.get("fill_latency_ms"),
            "exit_price_avg": v_out / q_out if q_out else None,
            "exits": exits,
            "fees": fees,
            "execution_ids": sorted(str(e.get("execId")) for e in ex if e.get("execId")),
            "duplicate_executions_dropped": dups,
            "funding": funding,
            "gross_pnl": gross,
            "net_pnl": net,
            "bybit_closed_pnl_sum": sum(_f(c.get("closedPnl")) for c in closed),
            "planned_risk_usdt": p["planned_risk_usdt"],
            "actual_risk_usdt": risk_actual,
            "R_net_vs_planned": net / p["planned_risk_usdt"] if p["planned_risk_usdt"] else None,
            "R_net_vs_actual": net / risk_actual if risk_actual else None,
            "R_gross_vs_actual": gross / risk_actual if risk_actual else None,
            "closed_at": _iso(self.client.now_ms()),
        }
        trade["paper_comparison"] = compare_with_paper(
            trade,
            p,
            self.paper_trades_path,
            self.dcfg.reference_equity_usdt,
            self.cfg.risk.initial_equity,
        )
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trades_path.open("a") as f:
            f.write(json.dumps(trade, sort_keys=True, default=str) + "\n")
        self.state["demo_closed_trades"] = int(self.state["demo_closed_trades"]) + 1
        self.state["demo_cum_net_pnl"] = float(self.state["demo_cum_net_pnl"]) + net
        self._j("TRADE_CLOSED", trade)
        self._close_local(p, reason="EXCHANGE_FLAT")
        return {"action": "closed", "trade": trade}

    # ------------------------------------------------------------------ cycle
    def step(
        self,
        triggers_now: list[Trigger],
        blockers: list[str],
        trail_candidate: float | None,
        now_ms: int,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"at": _iso(now_ms), "blockers": blockers}
        out["manage"] = self.manage(trail_candidate, now_ms)
        if self.state["reconcile_required"]:
            blockers = [*blockers, "RECONCILIATION_REQUIRED"]
        if self.state["position"] is not None:
            blockers = [*blockers, "POSITION_OPEN"]
        else:
            try:
                lp = self.client.position()
                if lp["size"] > 0:
                    self.alert("UNEXPECTED_OPEN_POSITION", {"position": lp}, reconcile=True)
                    blockers = [*blockers, "UNEXPECTED_OPEN_POSITION"]
            except (DemoApiError, DemoTransportError) as e:
                self.error("step.position", e)
                blockers = [*blockers, "EXCHANGE_UNREACHABLE"]
        out["entries"] = []
        for i, trig in enumerate(triggers_now):
            b = blockers if i == 0 else [*blockers, "ONE_POSITION_ONLY"]
            out["entries"].append(self.enter(trig, b))
            if self.state["position"] is not None:
                blockers = [*blockers, "POSITION_OPEN"]
        self.save()
        return out


# ----------------------------------------------------------------------------- reconciliation
def compare_with_paper(
    trade: dict[str, Any],
    p: dict[str, Any],
    paper_trades_path: Path,
    reference_equity: float,
    paper_equity: float,
) -> dict[str, Any]:
    """SIGNAL vs PAPER vs DEMO for one trade. Paper P&L is scaled from the paper equity to the
    demo reference equity; R values are compared directly."""
    paper = None
    if paper_trades_path.exists():
        for line in paper_trades_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (
                r.get("family") == p["family"]
                and r.get("side") == p["side"]
                and int(r.get("entry_ms", -1)) == int(p["trigger_ms"])
            ):
                paper = r
    if paper is None:
        return {"paper_found": False}
    s = 1.0 if p["side"] == "LONG" else -1.0
    scale = reference_equity / paper_equity
    risk_p = _f(paper.get("risk_amount")) or math.nan
    paper_fee_r = _f(paper.get("fees")) / risk_p if risk_p else math.nan
    demo_fee_r = (
        trade["fees"] / trade["actual_risk_usdt"] if trade["actual_risk_usdt"] else math.nan
    )
    return {
        "paper_found": True,
        "signal": {
            "trigger_at": trade["trigger_at"],
            "expected_entry": p["trigger_close"],
            "stop": p["stop0"],
            "targets": trade["tp_levels"],
        },
        "paper": {
            "fill": _f(paper.get("entry_price")),
            "exit_avg": _f(paper.get("avg_exit_price")),
            "exit_reason": paper.get("exit_reason"),
            "fees_scaled": _f(paper.get("fees")) * scale,
            "slippage_scaled": _f(paper.get("slippage")) * scale,
            "funding_scaled": _f(paper.get("funding")) * scale,
            "pnl_scaled": _f(paper.get("POSITION_PNL")) * scale,
            "R": _f(paper.get("R_MULTIPLE")),
            "R_gross": _f(paper.get("R_MULTIPLE_GROSS")),
        },
        "demo": {
            "submitted": trade["submitted_price"],
            "fill": trade["entry_price"],
            "fill_latency_ms": trade["fill_latency_ms"],
            "fees": trade["fees"],
            "funding": trade["funding"],
            "exits": trade["exits"],
            "pnl": trade["net_pnl"],
            "R": trade["R_net_vs_actual"],
        },
        "diff": {
            "entry_fill_diff_bps": s
            * (trade["entry_price"] / _f(paper.get("entry_price")) - 1.0)
            * 1e4,
            "fee_diff_R": demo_fee_r - paper_fee_r,
            "R_diff": (trade["R_net_vs_actual"] or math.nan) - _f(paper.get("R_MULTIPLE")),
            "pnl_diff_scaled_usdt": trade["net_pnl"] - _f(paper.get("POSITION_PNL")) * scale,
        },
    }


def trail_candidate(eng: V5Engine, view: Any, close: float, side: str, stop: float) -> float | None:
    """The frozen engine's own `_update_trail` evaluated on a minimal position shim."""
    shim = SimpleNamespace(
        trail_active=True, side=Side(side), stop=stop, pending_stop=None, pending_stop_source=None
    )
    eng._update_trail(cast(Position, shim), view, close)
    return float(shim.pending_stop) if shim.pending_stop is not None else None
