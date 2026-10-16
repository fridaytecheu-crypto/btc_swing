"""READ-ONLY conflict gate between V11-LITE Demo and V10 Demo on BTCUSDT. V10's files are only read
(never written, no V10 module imported). Blockers affect Demo execution only, never the shadow."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

V10_ACTIVE = "V10_POSITION_OR_ORDERS_ACTIVE"
V10_RECON = "V10_RECONCILIATION_PENDING"
UNPROVEN = "OWNERSHIP_UNPROVEN"
FOREIGN_ORDERS = "FOREIGN_OPEN_ORDERS"
EXCHANGE_NOT_FLAT = "EXCHANGE_POSITION_NOT_FLAT"


def read_json_retry(p: Path, tries: int = 3) -> dict[str, Any] | None:
    """V10 rewrites its state atomically (tmp + replace); a read can race a replace: retry briefly."""
    for k in range(tries):
        try:
            d = json.loads(p.read_text())
            return d if isinstance(d, dict) else None
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05 * (k + 1))
    raise ValueError(f"unreadable: {p}")


def v10_local_state(v10_root: Path) -> dict[str, Any]:
    """V10 Demo local view: position record, reconciliation flag, activation journal presence."""
    st_path = v10_root / "demo" / "strategy_state.json"
    journal = v10_root / "demo" / "strategy_journal.jsonl"
    try:
        st = read_json_retry(st_path)
    except ValueError:
        return {
            "readable": False,
            "position": None,
            "reconcile_required": None,
            "journal": journal.exists(),
        }
    return {
        "readable": True,
        "state_exists": st is not None,
        "position": (st or {}).get("position"),
        "reconcile_required": bool((st or {}).get("reconcile_required")),
        "journal": journal.exists(),
    }


def conflict_blockers(
    *,
    shared: bool,
    v10_root: Path,
    exchange_position: dict[str, Any],
    open_orders: list[dict[str, Any]],
    own_prefix: str,
    v10_prefix: str,
    own_position_open: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Blockers for a NEW V11-LITE Demo entry. Shared account: V10's local state and V10-attributed
    exchange orders / position block; any unattributable position or foreign order blocks
    (ownership cannot be proven). Isolated account: the own account must be flat with no foreign order."""
    b: list[str] = []
    detail: dict[str, Any] = {"shared_account": shared}
    links = [str(o.get("orderLinkId") or "") for o in open_orders]
    v10_orders = [x for x in links if x.startswith(v10_prefix)]
    foreign = [x for x in links if not x.startswith(own_prefix) and not x.startswith(v10_prefix)]
    size = float(exchange_position.get("size") or 0.0)
    detail.update(
        {"exchange_size": size, "v10_orders": len(v10_orders), "foreign_orders": len(foreign)}
    )
    if shared:
        v10 = v10_local_state(v10_root)
        detail["v10"] = {k: (v is not None if k == "position" else v) for k, v in v10.items()}
        if not v10["readable"] or (v10["journal"] and not v10.get("state_exists", False)):
            b.append(UNPROVEN)
        if v10.get("position") is not None or v10_orders:
            b.append(V10_ACTIVE)
        if v10.get("reconcile_required"):
            b.append(V10_RECON)
        if size > 0 and not own_position_open:
            b.append(V10_ACTIVE if (v10.get("position") is not None or v10_orders) else UNPROVEN)
    else:
        if v10_orders:
            b.append(UNPROVEN)  # V10 orders on an account declared isolated: isolation is false
        if size > 0 and not own_position_open:
            b.append(EXCHANGE_NOT_FLAT)
    if foreign:
        b.append(FOREIGN_ORDERS)
    return sorted(set(b)), detail
