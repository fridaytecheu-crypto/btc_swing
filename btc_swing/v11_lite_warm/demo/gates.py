"""V11-LITE Demo activation gates and the read-only exchange views they use (GET only; demo host only;
credentials from env, never logged). Every gate must pass for `v11lw demo activate` to write the event."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.client import BybitDemoClient, DemoApiError, DemoTransportError
from btc_swing.v5.demo.config import DemoExecConfig, ExecutionMode
from btc_swing.v5.demo.credentials import DemoCredentialsMissingError, credentials_present
from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v11_lite_warm.demo.activation import gate
from btc_swing.v11_lite_warm.demo.config import LiteDemoConfig, exec_config, v10_exec_config
from btc_swing.v11_lite_warm.demo.conflict import conflict_blockers, v10_local_state


def _ro_client(dcfg: DemoExecConfig, scratch: Path, tag: str) -> BybitDemoClient:
    scratch.mkdir(parents=True, exist_ok=True)
    j = HashChainJournal(scratch / f"{tag.lower()}_readonly.jsonl", f"{tag.lower()}_readonly")
    return BybitDemoClient(dcfg, ExecutionMode.DISABLED, j, tag, read_only=True)


def account_snapshot(dcfg: DemoExecConfig, scratch: Path, tag: str) -> dict[str, Any]:
    """Position, open orders and the Bybit user id of the account behind `dcfg`'s credentials."""
    if not credentials_present(dcfg):
        return {"reachable": False, "credentials": False, "error": "credentials missing (env)"}
    try:
        c = _ro_client(dcfg, scratch, tag)
        pos, oo = c.position(), c.open_orders()
        uid = (c._request("GET", "/v5/user/query-api").get("result") or {}).get("userID")
        c.close()
        return {"reachable": True, "credentials": True, "position": pos, "position_size": pos["size"],
                "open_orders": oo, "open_order_links": [o.get("orderLinkId") for o in oo], "user_id": uid}  # fmt: skip
    except (DemoApiError, DemoTransportError, DemoCredentialsMissingError) as e:
        return {"reachable": False, "credentials": True, "error": str(e)[:200]}


def demo_gates(
    *,
    dc: LiteDemoConfig,
    lite_snapshot: dict[str, Any],
    v10_snapshot: dict[str, Any] | None,
    v10_root: Path,
    shared_flag: bool,
    runner_alive: bool,
    feed_age_s: float | None,
    warm: dict[str, Any],
    in_window: bool,
    freeze_ok: tuple[bool, str],
    demo_freeze_ok: tuple[bool, str],
    chains: dict[str, dict[str, Any]],
    demo_state: dict[str, Any],
    lease_ok: tuple[bool, str],
    cloud: bool,
    base_mode: str,
) -> dict[str, dict[str, Any]]:
    g: dict[str, dict[str, Any]] = {}
    g["not a cloud-session container"] = gate(not cloud, "authoritative host only")
    g["authority lease held by this host"] = gate(lease_ok[0], lease_ok[1])
    g["V11-LITE runner alive"] = gate(runner_alive, {"alive": runner_alive})
    g["feed fresh (raw store <= 120 s)"] = gate(
        feed_age_s is not None and feed_age_s <= 120, {"age_s": feed_age_s}
    )
    g["scored inputs warm"] = gate(bool(warm.get("ready")), warm)
    g["observation window active"] = gate(in_window, {"in_window": in_window})
    g["V11-LITE freeze / strategy hashes valid"] = gate(freeze_ok[0], freeze_ok[1])
    g["Demo execution manifest matches config"] = gate(demo_freeze_ok[0], demo_freeze_ok[1])
    g["hash-chained journals valid"] = gate(
        all(c.get("ok") for c in chains.values()), {k: v.get("ok") for k, v in chains.items()}
    )
    g["no V11-LITE Demo reconciliation / position / halt"] = gate(
        not demo_state.get("reconcile_required") and demo_state.get("position") is None and not demo_state.get("halted"),
        {k: demo_state.get(k) for k in ("reconcile_required", "halted")} | {"position": demo_state.get("position") is not None},
    )  # fmt: skip
    g["Demo credentials present (env, never printed)"] = gate(
        bool(lite_snapshot.get("credentials")), dc.account_mode
    )
    g["Bybit Demo reachable (demo host, read-only)"] = gate(
        bool(lite_snapshot.get("reachable")), lite_snapshot.get("error")
    )
    g["Demo config file stays DISABLED"] = gate(base_mode == "DISABLED", base_mode)
    g["risk 0.10 % of 5000 USDT, leverage <= 2"] = gate(
        abs(dc.risk.risk_usdt - 5.0) < 1e-9 and dc.risk.leverage <= 2, {"risk_usdt": dc.risk.risk_usdt, "leverage": dc.risk.leverage}
    )  # fmt: skip
    pos = lite_snapshot.get("position") or {"size": 1.0}
    oo = lite_snapshot.get("open_orders") or []
    if dc.account_mode == "isolated":
        v10 = v10_snapshot or {}
        uid_l, uid_v = lite_snapshot.get("user_id"), v10.get("user_id")
        g["isolated account proven (different Bybit user id than V10)"] = gate(
            uid_l is not None and uid_v is not None and str(uid_l) != str(uid_v),
            {"lite_user": "present" if uid_l else None, "v10_user": "present" if uid_v else None, "different": uid_l != uid_v},
        )  # fmt: skip
        g["own Demo account BTCUSDT flat, no open orders"] = gate(pos.get("size", 1.0) == 0.0 and not oo, {"size": pos.get("size"), "open_orders": len(oo)})  # fmt: skip
    else:
        g["shared account explicitly acknowledged (--shared-account)"] = gate(
            shared_flag, shared_flag
        )
        blockers, det = conflict_blockers(
            shared=True, v10_root=v10_root, exchange_position=pos, open_orders=oo,
            own_prefix=dc.order_prefix, v10_prefix=dc.v10_order_prefix, own_position_open=False,
        )  # fmt: skip
        g["V10 conflict gate clear (no V10 position / orders / reconciliation)"] = gate(
            not blockers, {"blockers": blockers, **det}
        )
    return g


def warm_ready(verify: dict[str, Any]) -> dict[str, Any]:
    """Scored inputs warm: every pre-declared field except the 1h ATR (sizing label only), every group,
    and the live microstructure contribution."""
    mf = [k for k, v in (verify.get("required_fields") or {}).items() if v is None]
    mg = [k for k, v in (verify.get("required_groups") or {}).items() if v is None]
    micro = verify.get("microstructure_contribution")
    return {
        "ready": set(mf) <= {"atr1h"} and not mg and micro is not None,
        "missing_fields": mf,
        "missing_groups": mg,
        "micro": micro,
    }


def build_executor(
    lite_root: Path, v10_root: Path, v11: Any, dc: LiteDemoConfig, act: dict[str, Any]
) -> Any:
    """Only with an effective activation for this host, off the cloud, with the manifest valid."""
    from btc_swing.v5.demo.preflight import is_cloud_session_container
    from btc_swing.v11_lite_warm.demo.activation import demo_dir, demo_journal
    from btc_swing.v11_lite_warm.demo.executor import LiteDemoExecutor

    if is_cloud_session_container():
        raise RuntimeError("cloud-session container: Demo execution refused")
    if str(act.get("account_mode")) != dc.account_mode:
        raise RuntimeError("Demo account mode differs from the activation record")
    dcfg = exec_config(dc, ExecutionMode.STRATEGY_DEMO)
    journal = demo_journal(lite_root)
    client = BybitDemoClient(dcfg, dcfg.mode, journal, "STRATEGY_DEMO_V11LW")
    ex = LiteDemoExecutor(
        dc, v11, client, journal, demo_dir(lite_root), v10_root, int(act["activated_at_ms"])
    )
    ex.recover()
    return ex


def v10_view(dc: LiteDemoConfig, v10_root: Path, scratch: Path) -> dict[str, Any]:
    return {
        "local": v10_local_state(v10_root),
        "account": account_snapshot(v10_exec_config(dc), scratch, "V10_VIEW"),
    }


def now_s() -> float:
    return time.time()
