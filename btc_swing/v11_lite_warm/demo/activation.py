"""V11-LITE Demo activation: an immutable, host-bound STRATEGY_DEMO_V11LW_ACTIVATED event in the V11-LITE
Demo journal, written only by `btc-swing v11lw demo activate` after EVERY gate passed. Never automatic."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.journal import HashChainJournal
from btc_swing.v5.forward.host import host_identity
from btc_swing.v11_lite_warm.demo.config import TAG

ACTIVATED = "STRATEGY_DEMO_V11LW_ACTIVATED"
DEACTIVATED = "STRATEGY_DEMO_V11LW_DEACTIVATED"
RECON_ACK = "RECONCILIATION_ACKNOWLEDGED"
RESUMED = "DEMO_RESUMED"
OWNER_EVENTS = (RECON_ACK, RESUMED)
JOURNAL_NAME = "strategy_demo_v11lw"
_THREAD_LOCK = threading.Lock()


def _last_line(path: Path) -> str | None:
    """The last non-empty line, read backwards from the end (journals grow; never read whole)."""
    if not path.exists():
        return None
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        buf = b""
        pos = end
        while pos > 0:
            step = min(65536, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            lines = [x for x in buf.split(b"\n") if x.strip()]
            if len(lines) >= 2 or (pos == 0 and lines):
                return lines[-1].decode("utf-8")
    return None


class LockedChainJournal(HashChainJournal):
    """The hash-chained journal, safe for several writers (runner threads and owner CLI processes):
    each append holds an exclusive file lock and re-reads the chain tail before writing."""

    def append(self, kind: str, data: dict[str, Any], tag: str | None = None) -> dict[str, Any]:
        lock_path = self.path.with_suffix(".lock")
        with _THREAD_LOCK, lock_path.open("a") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                last = _last_line(self.path)
                if last is not None:
                    r = json.loads(last)
                    self._seq, self._prev = int(r["seq"]) + 1, str(r["hash"])
                return super().append(kind, data, tag)
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def demo_dir(lite_root: Path) -> Path:
    return lite_root / "demo"


def demo_journal(lite_root: Path) -> LockedChainJournal:
    return LockedChainJournal(
        demo_dir(lite_root) / "strategy_journal.jsonl",
        JOURNAL_NAME,
        forbid_tags=("EXECUTION_SMOKE", "STRATEGY_DEMO_V10", "STRATEGY_DEMO"),
    )


def record_owner_event(lite_root: Path, kind: str, note: str) -> dict[str, Any]:
    """Owner acknowledgement (reconcile-ack / resume): journaled only; the executor applies it (it
    owns the Demo state file), so a running executor never overwrites the owner's action."""
    if kind not in OWNER_EVENTS:
        raise ValueError(kind)
    rec = {**host_identity(), "at": datetime.now(UTC).isoformat(), "note": note}
    demo_journal(lite_root).append(kind, rec, TAG)
    return rec


def activation_state(lite_root: Path) -> dict[str, Any]:
    p = demo_dir(lite_root) / "strategy_journal.jsonl"
    recs = HashChainJournal(p, JOURNAL_NAME).records() if p.exists() else []
    events = [r for r in recs if r["kind"] in (ACTIVATED, DEACTIVATED)]
    last = events[-1] if events else None
    return {
        "status": "NEVER_ACTIVATED" if last is None else last["kind"],
        "events": len(events),
        "last": dict(last["data"]) if last else None,
    }


def effective_activation(lite_root: Path) -> dict[str, Any] | None:
    st = activation_state(lite_root)
    last = st["last"]
    if st["status"] != ACTIVATED or last is None:
        return None
    if last.get("host_id") != host_identity()["host_id"]:
        return None
    return last


def record_activation(
    lite_root: Path, gates: dict[str, dict[str, Any]], note: str, account_mode: str
) -> dict[str, Any]:
    if not gates or not all(g.get("ok") for g in gates.values()):
        raise RuntimeError("refusing to activate: not every gate passed")
    if effective_activation(lite_root) is not None:
        raise RuntimeError("V11-LITE Demo is already active on this host")
    now = datetime.now(UTC)
    rec = {
        **host_identity(),
        "version": "V11-LITE-WARM",
        "account_mode": account_mode,
        "activated_at": now.isoformat(),
        "activated_at_ms": int(now.timestamp() * 1000),
        "note": note,
        "gates": gates,
        "rule": "only V11-LITE shadow decisions strictly after activated_at may be sent to Bybit Demo",
    }
    demo_journal(lite_root).append(ACTIVATED, rec, TAG)
    return rec


def record_deactivation(lite_root: Path, note: str, position_open: bool) -> dict[str, Any]:
    if effective_activation(lite_root) is None:
        raise RuntimeError("V11-LITE Demo is not active on this host")
    if position_open:
        raise RuntimeError("a V11-LITE Demo position is open: let the frozen exits close it first")
    now = datetime.now(UTC)
    rec = {**host_identity(), "deactivated_at": now.isoformat(), "note": note}
    demo_journal(lite_root).append(DEACTIVATED, rec, TAG)
    return rec


def gate(ok: bool, detail: Any) -> dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}
