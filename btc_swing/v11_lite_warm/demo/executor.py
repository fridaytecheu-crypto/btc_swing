"""V11-LITE Bybit DEMO executor (paper money; demo host enforced by the shared client). Consumes the
frozen V11-LITE shadow decision (it never decides anything itself); sizes 0.10 % of 5000 USDT on the
shadow's stop distance; places a market entry, the exchange stop and TP1 / TP2 reduce-only limits;
mirrors the frozen V11 management (break-even after TP1, runner trail after TP2 on completed 5m bars,
20-min stall review, 60-min hard stop). Only orders whose orderLinkId starts with V11LWD- are ever
created or cancelled; before any exchange-modifying action the exchange position must equal the own
expected size, otherwise reconciliation is required and nothing is touched. Own journal / state /
trades under <LITE root>/demo/. Never imports V10 code; V10 files are only read (conflict gate)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from btc_swing.v5.demo.client import (
    RET_DUPLICATE_LINK_ID,
    DemoApiError,
    DemoTransportError,
    fmt_price,
    fmt_qty,
)
from btc_swing.v5.demo.fills import (
    ExecutionConfirmationTimeoutError,
    backoff_schedule,
    confirm_executions,
    confirmed_record,
    dedupe_executions,
)
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v11.config import V11Config
from btc_swing.v11_lite_warm.demo.activation import RECON_ACK, RESUMED
from btc_swing.v11_lite_warm.demo.config import TAG, LiteDemoConfig
from btc_swing.v11_lite_warm.demo.conflict import conflict_blockers

ROLES = ("EN", "T1", "T2", "TC")
QTY_EPS = 1e-9
MS_1M = 60_000
DAY_MS = 86_400_000


class DemoClientLike(Protocol):
    def now_ms(self) -> int: ...
    def instrument(self) -> dict[str, Any]: ...
    def ticker(self) -> dict[str, Any]: ...
    def position(self) -> dict[str, Any]: ...
    def open_orders(self) -> list[dict[str, Any]]: ...
    def order(self, link_id: str) -> dict[str, Any] | None: ...
    def set_leverage(self, lev: float) -> dict[str, Any]: ...
    def create_order(self, side: str, qty: str, order_type: str, link_id: str, price: str | None = None, reduce_only: bool = False, tif: str | None = None) -> dict[str, Any]: ...  # fmt: skip
    def cancel_order(self, link_id: str) -> dict[str, Any]: ...
    def executions(self, link_id: str | None = None, start_ms: int | None = None) -> list[dict[str, Any]]: ...  # fmt: skip
    def trading_stop(self, stop_loss: str | None = None, take_profit: str | None = None) -> dict[str, Any]: ...  # fmt: skip
    def funding(self, start_ms: int) -> list[dict[str, Any]]: ...


@dataclass
class BarCtx:
    """A COPY of the completed 1m bar the shadow just processed: `i` is the shadow frame index (5m
    alignment of the frozen runner trail), `j` the same row inside the copied column tails `x`."""

    i: int
    j: int
    t_close: int
    x: dict[str, Any]  # copied column tails (numpy arrays): high, low, close, atr5
    score_long: float
    score_short: float


def lite_link_id(prefix: str, signal_id: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(role)
    return f"{prefix}{hashlib.sha256(signal_id.encode()).hexdigest()[:20]}-{role}"


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


class LiteDemoExecutor:
    def __init__(
        self,
        lc: LiteDemoConfig,
        v11: V11Config,
        client: DemoClientLike,
        journal: HashChainJournal,
        demo_dir: Path,
        v10_root: Path,
        activated_at_ms: int,
        sleep: Any = time.sleep,
    ) -> None:
        self.lc, self.v11, self.client, self.journal = lc, v11, client, journal
        self.state_path = demo_dir / "strategy_state.json"
        self.trades_path = demo_dir / "demo_trades.jsonl"
        self.v10_root = v10_root
        self.activated_at_ms = activated_at_ms
        self.sleep = sleep
        self.state: dict[str, Any] = self._load()
        self._inst: dict[str, Any] | None = None

    # ------------------------------------------------------------------ state
    def _default(self) -> dict[str, Any]:
        return {
            "strategy": "BTC_V11_LITE_WARM_DEMO",
            "account_mode": self.lc.account_mode,
            "position": None,
            "processed_signals": [],
            "reconcile_required": False,
            "reconcile_reason": None,
            "halted": False,
            "halted_reason": None,
            "executed_ms": [],
            "day": None,
            "day_realised_usd": 0.0,
            "consecutive_full_losses": 0,
            "closed_trades": 0,
            "cum_net_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "last_exchange_position": None,
            "owner_offset": 0,
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
        self.journal.append(kind, data, TAG)

    def alert(self, kind: str, data: dict[str, Any], reconcile: bool = False) -> None:
        self._j("ALERT_" + kind, {**data, "reconcile": reconcile})
        if reconcile:
            self.state["reconcile_required"] = True
            self.state["reconcile_reason"] = kind

    def apply_owner_events(self) -> list[str]:
        """Replay the journal tail in order: a reconcile ALERT sets the flag, the owner's later
        RECONCILIATION_ACKNOWLEDGED clears it; DEMO_HALTED / DEMO_RESUMED likewise. Order-exact, so an
        acknowledgement never clears a mismatch detected after it."""
        path = self.journal.path
        if not path.exists():
            return []
        off = int(self.state.get("owner_offset") or 0)
        if path.stat().st_size < off:
            off = 0
        with path.open("rb") as fh:
            fh.seek(off)
            chunk = fh.read()
        end = chunk.rfind(b"\n") + 1
        applied: list[str] = []
        st = self.state
        for line in chunk[:end].splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            k, d = str(r.get("kind")), r.get("data") or {}
            if k.startswith("ALERT_") and d.get("reconcile"):
                st["reconcile_required"], st["reconcile_reason"] = True, k.removeprefix("ALERT_")
            elif k == RECON_ACK and st["reconcile_required"]:
                st["reconcile_required"], st["reconcile_reason"] = False, None
                applied.append(k)
            elif k == "DEMO_HALTED":
                st["halted"], st["halted_reason"] = True, d.get("reason")
            elif k == RESUMED and st["halted"]:
                st["halted"], st["halted_reason"], st["consecutive_full_losses"] = False, None, 0
                applied.append(k)
        st["owner_offset"] = off + end
        if applied:
            self.save()
        return applied

    def has_position(self) -> bool:
        return self.state.get("position") is not None

    def _instrument(self) -> dict[str, Any]:
        if self._inst is None:
            self._inst = self.client.instrument()
        return self._inst

    def _link(self, sid: str, role: str) -> str:
        return lite_link_id(self.lc.order_prefix, sid, role)

    def _own(self, link: Any) -> bool:
        return str(link or "").startswith(self.lc.order_prefix)

    # ------------------------------------------------------------------ safety
    def _roll(self, now_ms: int) -> None:
        self.state["executed_ms"] = [
            t for t in self.state["executed_ms"] if now_ms - int(t) < DAY_MS
        ]
        day = datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        if self.state.get("day") != day:
            self.state["day"], self.state["day_realised_usd"] = day, 0.0

    def safety_blockers(self, now_ms: int) -> list[str]:
        self.apply_owner_events()
        self._roll(now_ms)
        s = self.lc.safety
        b: list[str] = []
        if self.state["reconcile_required"]:
            b.append("RECONCILIATION_REQUIRED")
        if self.state["halted"]:
            b.append(f"HALTED:{self.state['halted_reason']}")
        if self.state["position"] is not None:
            b.append("DEMO_POSITION_OPEN")
        if len(self.state["executed_ms"]) >= s.max_trades_rolling_24h:
            b.append("DEMO_CAP_24H")
        if float(self.state["day_realised_usd"]) <= -s.daily_stop_usdt:
            b.append("DAILY_STOP")
        return b

    # ------------------------------------------------------------------ recovery
    def recover(self) -> dict[str, Any]:
        self.apply_owner_events()
        p = self.state.get("position")
        try:
            pos = self.client.position()
        except (DemoApiError, DemoTransportError) as e:
            self.alert("RECOVERY_EXCHANGE_UNREACHABLE", {"error": str(e)[:200]}, reconcile=True)
            self.save()
            return {"action": "exchange_unreachable"}
        self.state["last_exchange_position"] = pos
        if p is None and pos["size"] > 0 and not self.lc.shares_v10_account:
            self.alert("UNEXPECTED_OPEN_POSITION_AT_START", {"position": pos}, reconcile=True)
        elif p is not None and pos["size"] <= 0 and p.get("status") == "ACTIVE":
            out = self._finalize(p)
            self.save()
            return {"action": "finalized_at_start", "result": out.get("action")}
        elif p is not None and abs(pos["size"] - self._expected_size(p)) > QTY_EPS:
            self.alert("SIZE_MISMATCH_AT_START", {"exchange": pos["size"], "expected": self._expected_size(p)}, reconcile=True)  # fmt: skip
        self.save()
        return {"action": "ok", "position_open": p is not None}

    # ------------------------------------------------------------------ entry (shadow decision -> Demo)
    def on_shadow_decision(self, rec: dict[str, Any], bar: BarCtx | None) -> dict[str, Any]:
        """`rec` is the frozen V11-LITE EXECUTED shadow decision (read-only). Returns the Demo outcome;
        every outcome is journaled; nothing here can change the shadow."""
        sid = str(rec["signal_id"])
        s = int(rec["side_sign"])
        now = self.client.now_ms()
        base = {
            "signal_id": sid,
            "side": rec.get("side"),
            "selected_template": rec.get("family"),
            "shadow_score": rec.get("score"),
            "shadow_entry_ref": rec.get("entry_ref"),
            "shadow_stop": rec.get("stop"),
            "decision_ts": _iso(rec.get("decision_ms")),
        }
        if sid in self.state["processed_signals"]:
            self.alert("DUPLICATE_SIGNAL", {"signal_id": sid})
            return {"signal_id": sid, "action": "SKIPPED_DUPLICATE"}
        self.state["processed_signals"] = [*self.state["processed_signals"], sid][-2000:]
        if int(rec["decision_ms"]) <= self.activated_at_ms:
            self._j("PRE_ACTIVATION_SIGNAL_REFUSED", base)
            self.save()
            return {"signal_id": sid, "action": "REFUSED_BEFORE_ACTIVATION"}
        blockers = self.safety_blockers(now)
        if now - int(rec["decision_ms"]) > self.lc.safety.entry_max_delay_s * 1000:
            blockers.append("ENTRY_TOO_LATE")
        entry_ref, stop = float(rec["entry_ref"]), float(rec["stop"])
        dist = abs(entry_ref - stop)
        try:
            pos = self.client.position()
            oo = self.client.open_orders()
            inst = self._instrument()
        except (DemoApiError, DemoTransportError) as e:
            return self._blocked(base, ["EXCHANGE_UNREACHABLE"], {"error": str(e)[:200]})
        cb, cdet = conflict_blockers(
            shared=self.lc.shares_v10_account, v10_root=self.v10_root, exchange_position=pos,
            open_orders=oo, own_prefix=self.lc.order_prefix, v10_prefix=self.lc.v10_order_prefix,
            own_position_open=self.has_position(),
        )  # fmt: skip
        blockers += cb
        qty_s = fmt_qty(self.lc.risk.risk_usdt / dist, inst["qty_step"]) if dist > 0 else "0"
        if float(qty_s) < inst["min_qty"] - 1e-12:
            blockers.append("BELOW_MIN_QTY")
        if blockers:
            return self._blocked(base, sorted(set(blockers)), {"conflict": cdet})
        link = self._link(sid, "EN")
        try:
            if self.client.order(link) is not None:
                self.alert(
                    "DUPLICATE_ON_EXCHANGE", {"signal_id": sid, "link": link}, reconcile=True
                )
                self.save()
                return {"signal_id": sid, "action": "SKIPPED_DUPLICATE_ON_EXCHANGE"}
            # race guard: re-read the shared state immediately before the order
            pos2, oo2 = self.client.position(), self.client.open_orders()
            cb2, _ = conflict_blockers(
                shared=self.lc.shares_v10_account, v10_root=self.v10_root, exchange_position=pos2,
                open_orders=oo2, own_prefix=self.lc.order_prefix, v10_prefix=self.lc.v10_order_prefix,
                own_position_open=False,
            )  # fmt: skip
            if cb2:
                return self._blocked(base, cb2, {"stage": "pre-order recheck"})
            self.client.set_leverage(float(self.lc.risk.leverage))
        except (DemoApiError, DemoTransportError) as e:
            return self._blocked(base, ["EXCHANGE_ERROR_BEFORE_ORDER"], {"error": str(e)[:200]})
        x = bar.x if bar is not None else {}
        i_dec = bar.j if bar is not None else -1
        trig = (
            float(x["low"][i_dec])
            if (bar and s > 0)
            else float(x["high"][i_dec])
            if bar
            else math.nan
        )
        p: dict[str, Any] = {
            "signal_id": sid, "selected_template": rec.get("family"), "side": s,
            "entry_ref": entry_ref, "stop0": stop, "stop": stop, "tp1": float(rec["tp1"]), "tp2": float(rec["tp2"]),
            "planned_qty": float(qty_s), "planned_risk_usdt": self.lc.risk.risk_usdt,
            "leverage": float(self.lc.risk.leverage), "score_entry": float(rec["score"]),
            "trigger_low": trig, "link_entry": link, "status": "SUBMITTING",
            "decision_ms": int(rec["decision_ms"]), "order_sent_ms": now, "ack_ms": None, "fill_ms": None,
            "legs": {}, "tp1_done": False, "tp2_done": False, "stall_checked": False, "mfe_r": 0.0, "mae_r": 0.0,
            "close_reason": None,
        }  # fmt: skip
        self.state["position"] = p
        self.state["executed_ms"] = [*self.state["executed_ms"], now]
        self._j("ENTRY_SUBMIT", {**base, "link": link, "side_bybit": _bybit_side(s), "qty": qty_s,
                                 "leverage": p["leverage"], "risk_usdt": p["planned_risk_usdt"],
                                 "order_sent_ts": _iso(now)})  # fmt: skip
        self.save()  # write-ahead: a crash after this line never leads to a second order
        try:
            r = self.client.create_order(_bybit_side(s), qty_s, "Market", link)
            p["status"], p["order_id"], p["ack_ms"] = (
                "ACKED",
                r.get("orderId"),
                self.client.now_ms(),
            )
        except DemoApiError as e:
            if e.ret_code != RET_DUPLICATE_LINK_ID:
                self._j("ENTRY_REJECTED", {"signal_id": sid, "error": str(e)[:200]})
                self.state["position"] = None
                self.save()
                return {"signal_id": sid, "action": "REJECTED"}
            p["status"], p["ack_ms"] = "ACKED", self.client.now_ms()
        except DemoTransportError as e:
            self.alert("ORDER_ACK_MISSING", {"signal_id": sid, "error": str(e)[:200]})
            o = None
            with contextlib.suppress(DemoApiError, DemoTransportError):
                o = self.client.order(link)
            if o is None:
                self.alert("ORDER_STATE_UNKNOWN", {"signal_id": sid, "link": link}, reconcile=True)
                self.save()
                return {"signal_id": sid, "action": "ACK_UNKNOWN"}
            p["status"], p["order_id"], p["ack_ms"] = (
                "ACKED",
                o.get("orderId"),
                self.client.now_ms(),
            )
        self._j("ENTRY_ACKED", {"signal_id": sid, "ack_latency_ms": int(p["ack_ms"]) - now})
        self._resolve_fill(p)
        if self.state["position"] is not None and p["status"] == "ACTIVE":
            self._ensure_protection(p)
        self.save()
        return {"signal_id": sid, "action": "ENTERED" if self.state["position"] else "NO_FILL"}

    def _blocked(
        self, base: dict[str, Any], blockers: list[str], extra: dict[str, Any]
    ) -> dict[str, Any]:
        self._j("DEMO_ENTRY_BLOCKED", {**base, "blockers": blockers, **extra})
        self.save()
        return {"signal_id": base["signal_id"], "action": "BLOCKED", "blockers": blockers}

    def _resolve_fill(self, p: dict[str, Any]) -> None:
        deadline = time.time() + self.lc.failsafe.fill_timeout_s
        o: dict[str, Any] | None = None
        while True:
            with contextlib.suppress(DemoApiError, DemoTransportError):
                o = self.client.order(p["link_entry"])
            if o and o.get("orderStatus") in ("Filled", "PartiallyFilledCanceled", "Cancelled", "Rejected", "Deactivated"):  # fmt: skip
                break
            if time.time() >= deadline:
                self.alert("ENTRY_FILL_UNRESOLVED", {"signal_id": p["signal_id"]}, reconcile=True)
                return
            self.sleep(0.5)
        qty = _f(o.get("cumExecQty")) if o else math.nan
        if not qty or qty <= 0 or not math.isfinite(qty):
            self._j(
                "ENTRY_NOT_FILLED",
                {"signal_id": p["signal_id"], "order_status": (o or {}).get("orderStatus")},
            )
            self.state["position"] = None
            return
        if qty < p["planned_qty"] - QTY_EPS:
            self._j(
                "ENTRY_PARTIAL_FILL",
                {"signal_id": p["signal_id"], "filled": qty, "planned": p["planned_qty"]},
            )
        oid = str((o or {}).get("orderId") or "") or None
        try:
            agg = confirm_executions(
                lambda: self.client.executions(link_id=p["link_entry"]), qty, oid, p["link_entry"],
                self.lc.failsafe.exec_confirm_timeout_s, sleep=self.sleep,
            )  # fmt: skip
            rec = confirmed_record(agg, p["link_entry"], oid, "entry")
            self._j("EXECUTION_CONFIRMED", rec | {"signal_id": p["signal_id"]})
            price, fee, fill_ms = (
                float(rec["avg_price"]),
                float(rec["total_fee"]),
                int(rec["last_exec_ms"]),
            )
        except ExecutionConfirmationTimeoutError as e:
            self.alert("ENTRY_EXECUTION_UNCONFIRMED", {"signal_id": p["signal_id"], "last": str(e.last)[:200]}, reconcile=True)  # fmt: skip
            price, fee, fill_ms = _f((o or {}).get("avgPrice")), math.nan, self.client.now_ms()
        p.update(
            {
                "status": "ACTIVE",
                "filled_qty": qty,
                "entry_price": price,
                "entry_fee": fee,
                "fill_ms": fill_ms,
            }
        )
        self._j("ENTRY_FILLED", {"signal_id": p["signal_id"], "qty": qty, "avg_price": price, "fee": fee,
                                 "fill_ts": _iso(fill_ms), "fill_latency_ms": fill_ms - int(p["order_sent_ms"]),
                                 "slippage_bps_vs_shadow_ref": p["side"] * (price / p["entry_ref"] - 1) * 1e4})  # fmt: skip
        with contextlib.suppress(DemoApiError, DemoTransportError):
            pos = self.client.position()
            if abs(pos["size"] - qty) > QTY_EPS:  # netted with something that is not ours
                self.alert("EXCHANGE_SIZE_MISMATCH_AFTER_ENTRY", {"exchange": pos["size"], "own": qty}, reconcile=True)  # fmt: skip
        self.save()

    def _expected_size(self, p: dict[str, Any]) -> float:
        q = float(p.get("filled_qty") or 0.0)
        for leg in ("T1", "T2"):
            lg = p["legs"].get(leg) or {}
            if lg.get("filled"):
                q -= float(lg.get("qty") or 0.0)
        return max(q, 0.0)

    def _ensure_protection(self, p: dict[str, Any]) -> None:
        if self.state["reconcile_required"]:
            return  # ownership unproven: touch nothing
        inst = self._instrument()
        ex = self.v11.exits
        try:
            pos = self.client.position()
            if abs(pos["size"] - self._expected_size(p)) > QTY_EPS:
                self.alert("SIZE_MISMATCH_BEFORE_PROTECTION", {"exchange": pos["size"], "expected": self._expected_size(p)}, reconcile=True)  # fmt: skip
                return
            want = fmt_price(p["stop"], inst["tick"])
            if pos.get("stop_loss") is None or abs(float(pos["stop_loss"]) - float(want)) > 1e-9:
                self.client.trading_stop(stop_loss=want)
                self._j("STOP_SET", {"signal_id": p["signal_id"], "stop": want})
            for leg, (lvl, frac) in {
                "T1": (p["tp1"], ex.tp1_frac),
                "T2": (p["tp2"], ex.tp2_frac),
            }.items():
                known = p["legs"].get(leg)
                if known and (known.get("placed") or known.get("reason") == "BELOW_MIN_QTY"):
                    continue
                link = self._link(p["signal_id"], leg)
                q = fmt_qty(p["filled_qty"] * frac, inst["qty_step"])
                px = fmt_price(lvl, inst["tick"])
                if float(q) < inst["min_qty"] - 1e-12:
                    p["legs"][leg] = {
                        "link": link,
                        "price": px,
                        "qty": q,
                        "placed": False,
                        "reason": "BELOW_MIN_QTY",
                    }
                    self._j(
                        "TP_LEG_NOT_PLACED", {"signal_id": p["signal_id"], "leg": leg, "qty": q}
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
    def manage(self, now_ms: int, bar: BarCtx | None = None) -> dict[str, Any]:
        p = self.state.get("position")
        if p is None or p.get("status") != "ACTIVE":
            return {"action": "none" if p is None else "pending_fill"}
        s = int(p["side"])
        ex = self.v11.exits
        self.apply_owner_events()
        try:
            pos = self.client.position()
            self.state["last_exchange_position"] = pos
            if pos["size"] <= 0:
                return self._finalize(p)
            if self.state["reconcile_required"]:
                return {"action": "reconcile_required"}  # never touch an unproven position
            for leg in ("T1", "T2"):
                lg = p["legs"].get(leg)
                if lg and lg.get("placed") and not lg.get("filled"):
                    o = self.client.order(lg["link"])
                    if o and o.get("orderStatus") == "Filled":
                        lg["filled"], lg["fill_price"] = True, _f(o.get("avgPrice"))
                        p["tp1_done"] = p["tp1_done"] or leg == "T1"
                        p["tp2_done"] = p["tp2_done"] or leg == "T2"
                        self._j("TP_FILLED", {"signal_id": p["signal_id"], "leg": leg, "price": lg["fill_price"], "qty": lg["qty"]})  # fmt: skip
                    elif o and o.get("orderStatus") == "PartiallyFilled":
                        self._j(
                            "TP_PARTIAL_FILL",
                            {"signal_id": p["signal_id"], "leg": leg, "cum": o.get("cumExecQty")},
                        )
            if abs(pos["size"] - self._expected_size(p)) > 1e-6 and not any(
                (p["legs"].get(k) or {}).get("placed") and not (p["legs"].get(k) or {}).get("filled")
                and self._partially_filled(p["legs"][k]) for k in ("T1", "T2")
            ):  # fmt: skip
                self.alert(
                    "SIZE_MISMATCH",
                    {"exchange": pos["size"], "expected": self._expected_size(p)},
                    reconcile=True,
                )
                self.save()
                return {"action": "reconcile_required"}
            new_stop, why = float(p["stop"]), ""
            if p["tp1_done"]:  # frozen V11: break-even (+bps) after TP1 on the shadow's entry_ref
                be = float(p["entry_ref"]) * (1 + s * ex.breakeven_after_tp1_bps / 1e4)
                if s * (be - new_stop) > 0:
                    new_stop, why = be, "BE"
            if bar is not None:
                self._track_excursion(p, bar)
                if p["tp2_done"] and (bar.i + 1) % 5 == 0:
                    tr = runner_trail(
                        bar.x, bar.j, s, ex.runner_trail_bars_5m, ex.runner_trail_buffer_atr
                    )
                    if tr is not None and s * (tr - new_stop) > 0:
                        new_stop, why = tr, "TRAIL"
            if s * (new_stop - p["stop"]) > 0:
                self._move_stop(p, new_stop, why)
            if bar is not None:
                # the frozen clock: the shadow position's entry_ms is the decision bar close
                held = (bar.t_close - int(p["decision_ms"])) / MS_1M
                if not p["stall_checked"] and held >= ex.stall_review_min:
                    p["stall_checked"] = True
                    sc_now = bar.score_long if s > 0 else bar.score_short
                    c = float(bar.x["close"][bar.j])
                    invalid = (
                        s * (c - float(p["trigger_low"])) < 0
                        if math.isfinite(float(p["trigger_low"]))
                        else False
                    )
                    deteriorated = (
                        math.isfinite(sc_now)
                        and sc_now <= float(p["score_entry"]) - ex.stall_score_drop
                    )
                    if p["mfe_r"] < ex.stall_min_mfe_r and (deteriorated or invalid):
                        return self._market_close(p, pos, "STALL")
                if held >= ex.hard_max_min:
                    return self._market_close(p, pos, "TIME")
            self.save()
            return {"action": "managed", "size": pos["size"]}
        except (DemoApiError, DemoTransportError) as e:
            self._j("MANAGE_ERROR", {"signal_id": p["signal_id"], "error": str(e)[:200]})
            self.save()
            return {"action": "error"}

    def _partially_filled(self, lg: dict[str, Any]) -> bool:
        with contextlib.suppress(DemoApiError, DemoTransportError):
            o = self.client.order(lg["link"])
            return bool(o and o.get("orderStatus") == "PartiallyFilled")
        return False

    def _track_excursion(self, p: dict[str, Any], bar: BarCtx) -> None:
        if bar.t_close <= int(p["decision_ms"]):
            return
        s = int(p["side"])
        ref = float(
            p["entry_ref"]
        )  # frozen V11: excursions on the plan geometry (entry_ref, stop0)
        d = abs(ref - float(p["stop0"]))
        h, lo = float(bar.x["high"][bar.j]), float(bar.x["low"][bar.j])
        fav, adv = (h, lo) if s > 0 else (lo, h)
        if d > 0 and math.isfinite(fav) and math.isfinite(adv):
            p["mfe_r"] = max(float(p["mfe_r"]), s * (fav - ref) / d)
            p["mae_r"] = min(float(p["mae_r"]), s * (adv - ref) / d)

    def _move_stop(self, p: dict[str, Any], level: float, why: str) -> None:
        inst = self._instrument()
        want = fmt_price(level, inst["tick"])
        try:
            self.client.trading_stop(stop_loss=want)
            p["stop"] = level
            self._j("STOP_MOVED", {"signal_id": p["signal_id"], "reason": why, "stop": want})
        except DemoApiError as e:  # e.g. the new level is already through the market: close instead
            self._j(
                "STOP_MOVE_REJECTED",
                {"signal_id": p["signal_id"], "reason": why, "error": str(e)[:200]},
            )
            last = _f(self.client.ticker().get("last"))
            if int(p["side"]) * (last - level) <= 0:
                self._market_close(p, self.client.position(), f"{why}_LEVEL_CROSSED")

    def _market_close(self, p: dict[str, Any], pos: dict[str, Any], reason: str) -> dict[str, Any]:
        if abs(pos["size"] - self._expected_size(p)) > QTY_EPS:
            self.alert("SIZE_MISMATCH_BEFORE_CLOSE", {"exchange": pos["size"], "expected": self._expected_size(p)}, reconcile=True)  # fmt: skip
            self.save()
            return {"action": "reconcile_required"}
        for leg in ("T1", "T2"):
            lg = p["legs"].get(leg)
            if lg and lg.get("placed") and not lg.get("filled"):
                with contextlib.suppress(DemoApiError):
                    self.client.cancel_order(lg["link"])  # own V11LWD- link only
        inst = self._instrument()
        link = self._link(p["signal_id"], "TC")
        try:
            self.client.create_order(_bybit_side(int(p["side"]), closing=True), fmt_qty(pos["size"], inst["qty_step"]), "Market", link, reduce_only=True)  # fmt: skip
        except DemoApiError as e:
            if e.ret_code != RET_DUPLICATE_LINK_ID:
                raise
        p["close_reason"] = reason
        self._j(
            "TIME_CLOSE",
            {"signal_id": p["signal_id"], "reason": reason, "link": link, "qty": pos["size"]},
        )
        self.save()
        if self.client.position()["size"] <= 0:
            return self._finalize(p)
        return {"action": "closing"}

    # ------------------------------------------------------------------ close accounting
    def _finalize(self, p: dict[str, Any]) -> dict[str, Any]:
        """Own executions only: every V11LWD- link of this signal plus the exchange-side stop fills
        (no orderLinkId) on the closing side inside the trade window, which can only be ours because
        no new entry is ever sent while any other BTCUSDT position or order exists."""
        start = int(p["order_sent_ms"]) - 1000
        s = int(p["side"])
        bside = _bybit_side(s)
        own_links = {self._link(p["signal_id"], r) for r in ROLES}
        ex_rows: list[dict[str, Any]] = []
        complete = False
        q_in = q_out = 0.0
        for wait in [*backoff_schedule(self.lc.failsafe.exec_confirm_timeout_s), None]:
            rows, _d = dedupe_executions([
                e for e in self.client.executions(start_ms=start)
                if e.get("execType", "Trade") not in ("Funding", "Settle", "Delivery")
            ])  # fmt: skip
            mine = [e for e in rows if str(e.get("orderLinkId") or "") in own_links]
            linkless_close = sorted(
                (e for e in rows if not e.get("orderLinkId") and e.get("side") != bside), key=lambda e: int(e.get("execTime") or 0)
            )  # fmt: skip
            q_in = sum(_f(e["execQty"]) for e in mine if e.get("side") == bside)
            q_out = sum(_f(e["execQty"]) for e in mine if e.get("side") != bside)
            need = q_in - q_out
            take: list[dict[str, Any]] = []
            for e in linkless_close:
                if need <= QTY_EPS:
                    break
                take.append(e)
                need -= _f(e["execQty"])
            ex_rows = mine + take
            q_out = sum(_f(e["execQty"]) for e in ex_rows if e.get("side") != bside)
            complete = (
                abs(q_in - float(p.get("filled_qty") or 0)) <= QTY_EPS
                and abs(q_out - q_in) <= QTY_EPS
                and all(e.get("execFee") not in (None, "") for e in ex_rows)
            )
            if complete or wait is None:
                break
            self.sleep(wait)
        if not complete:
            if not p.get("finalize_alerted"):
                p["finalize_alerted"] = True
                self.alert("FLAT_EXECUTIONS_INCOMPLETE", {"signal_id": p["signal_id"], "q_in": q_in, "q_out": q_out}, reconcile=True)  # fmt: skip
                self.save()
            return {"action": "finalize_pending"}
        v_in = sum(_f(e["execQty"]) * _f(e["execPrice"]) for e in ex_rows if e.get("side") == bside)
        v_out = sum(
            _f(e["execQty"]) * _f(e["execPrice"]) for e in ex_rows if e.get("side") != bside
        )
        fees = sum(_f(e.get("execFee")) for e in ex_rows)
        funding = 0.0
        if (
            not self.lc.shares_v10_account
        ):  # funding is account-wide: attributable only when isolated
            with contextlib.suppress(DemoApiError, DemoTransportError):
                funding = sum(
                    _f(x.get("funding") or x.get("change") or 0) for x in self.client.funding(start)
                )
        gross = s * (v_out - v_in)
        net = gross - fees + funding
        risk_actual = float(p["filled_qty"]) * abs(float(p["entry_price"]) - float(p["stop0"]))
        exits: list[dict[str, Any]] = []
        for e in ex_rows:
            if e.get("side") == bside:
                continue
            link = str(e.get("orderLinkId") or "")
            kind = link.rsplit("-", 1)[-1] if self._own(link) else "EXCHANGE_STOP"
            exits.append(
                {
                    "price": _f(e["execPrice"]),
                    "qty": _f(e["execQty"]),
                    "kind": kind,
                    "time": _iso(int(e["execTime"])),
                }
            )
        kinds = {x["kind"] for x in exits}
        reason = p.get("close_reason") or ("TP2_THEN_STOP" if "T2" in kinds and "EXCHANGE_STOP" in kinds else
                                           "TP2" if "T2" in kinds and "EXCHANGE_STOP" not in kinds else
                                           "TP1_THEN_STOP" if "T1" in kinds else "STOP")  # fmt: skip
        stop_px: list[float] = [float(x["price"]) for x in exits if x["kind"] == "EXCHANGE_STOP"]
        now = self.client.now_ms()
        trade = {
            "strategy": "BTC_V11_LITE_WARM_DEMO", "execution_mode": "DEMO", "account_mode": self.lc.account_mode,
            "signal_id": p["signal_id"], "selected_template": p.get("selected_template"),
            "side": "LONG" if s > 0 else "SHORT", "shadow_entry_ref": p["entry_ref"], "entry_price": p["entry_price"],
            "entry_slippage_bps": s * (float(p["entry_price"]) / float(p["entry_ref"]) - 1) * 1e4,
            "stop0": p["stop0"], "stop_final": p["stop"], "tp1": p["tp1"], "tp2": p["tp2"], "qty": p["filled_qty"],
            "leverage": p["leverage"], "planned_risk_usdt": p["planned_risk_usdt"], "actual_risk_usdt": risk_actual,
            "exits": exits, "exit_reason": reason,
            "stop_exit_slippage_bps": (sum(s * (float(p["stop"]) - x) / float(p["stop"]) * 1e4 for x in stop_px) / len(stop_px)) if stop_px else None,
            "fees": fees, "funding": funding, "gross_pnl": gross, "net_pnl": net,
            "R_net_vs_planned": net / p["planned_risk_usdt"], "R_net_vs_actual": net / risk_actual if risk_actual else None,
            "mfe_r": p.get("mfe_r"), "mae_r": p.get("mae_r"),
            "decision_ts": _iso(p["decision_ms"]), "fill_ts": _iso(p["fill_ms"]), "closed_ts": _iso(now), "closed_ms": now,
            "hold_min": (now - int(p["fill_ms"])) / MS_1M,
        }  # fmt: skip
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trades_path.open("a") as fh:
            fh.write(json.dumps(trade, sort_keys=True, default=str) + "\n")
        self._roll(now)
        st = self.state
        st["closed_trades"] = int(st["closed_trades"]) + 1
        st["cum_net_pnl"] = float(st["cum_net_pnl"]) + net
        st["day_realised_usd"] = float(st["day_realised_usd"]) + net
        st["wins" if net > 0 else "losses"] = int(st["wins" if net > 0 else "losses"]) + 1
        if trade["R_net_vs_planned"] <= self.lc.safety.full_loss_r:
            st["consecutive_full_losses"] = int(st["consecutive_full_losses"]) + 1
        else:
            st["consecutive_full_losses"] = 0
        if st["consecutive_full_losses"] >= self.lc.safety.max_consecutive_full_losses:
            st["halted"], st["halted_reason"] = (
                True,
                f"{st['consecutive_full_losses']}_CONSECUTIVE_FULL_LOSSES",
            )
            self._j("DEMO_HALTED", {"reason": st["halted_reason"]})
        self._j("TRADE_CLOSED", trade)
        st["position"] = None
        self.save()
        return {"action": "closed", "trade": trade}


def runner_trail(x: dict[str, Any], i: int, s: int, nb: int, buf: float) -> float | None:
    """The frozen V11 runner trail (V11Engine._manage) on completed 5m bars ending at row i."""
    lo5 = [
        float(np.nanmin(x["low"][j - 4 : j + 1]))
        for j in range(i - 5 * (nb - 1), i + 1, 5)
        if j - 4 >= 0
    ]
    hi5 = [
        float(np.nanmax(x["high"][j - 4 : j + 1]))
        for j in range(i - 5 * (nb - 1), i + 1, 5)
        if j - 4 >= 0
    ]
    a5 = float(x["atr5"][i])
    if not (lo5 and hi5 and np.isfinite(a5)):
        return None
    return (min(lo5) - buf * a5) if s > 0 else (max(hi5) + buf * a5)
