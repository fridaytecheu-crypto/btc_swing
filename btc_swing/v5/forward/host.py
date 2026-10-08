"""Authoritative forward host: platform service manager, single-runner lock, authority lease.

- Service manager: LINUX requires an active systemd unit `btc-v5-forward`; MACOS requires a running
  launchd job `com.btcswing.v5-forward` (LaunchAgent in the owner's GUI domain). Any other platform
  is not a supported authoritative host.
- Single-runner lock: `v5 forward run` takes an exclusive, non-blocking `flock` on
  `<forward>/forward_run.lock`; a second runner on the same data directory exits immediately.
- Authority lease: an append-only, hash-chained journal `<forward>/host/authority.jsonl` with
  `AUTHORITY_CLAIMED` / `AUTHORITY_RELEASED` events bound to a host id. It migrates with the state.
  A host may claim only when no lease exists or the last event is a release; a runner refuses to
  start while the lease is held by another host or has been released by this one (a copied data
  directory can therefore never silently become a second authoritative runner).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import platform
import re
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from btc_swing.v5.demo.journal import HashChainJournal

SYSTEMD_SERVICE = "btc-v5-forward"
LAUNCHD_LABEL = "com.btcswing.v5-forward"
LAUNCHD_HEALTH_LABEL = "com.btcswing.v5-forward-health"


def host_os() -> str:
    s = platform.system()
    return {"Linux": "LINUX", "Darwin": "MACOS"}.get(s, s.upper() or "UNKNOWN")


def _machine_id() -> str:
    if host_os() == "MACOS":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            ).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                return m.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        with contextlib.suppress(OSError):
            v = Path(p).read_text().strip()
            if v:
                return v
    return socket.gethostname()


def host_identity() -> dict[str, str]:
    """Stable, non-secret host id (sha256 of the platform machine id) plus readable labels."""
    return {
        "host_id": hashlib.sha256(_machine_id().encode()).hexdigest()[:16],
        "hostname": socket.gethostname(),
        "os": host_os(),
    }


# ----------------------------------------------------------------------------- service managers
def systemd_status(service: str = SYSTEMD_SERVICE) -> dict[str, Any]:
    if not Path("/run/systemd/system").exists():
        return {"ok": False, "detail": "systemd not running on this machine", "pid": None}
    try:
        r = subprocess.run(
            ["systemctl", "show", service, "-p", "ActiveState", "-p", "MainPID"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "detail": f"systemctl unavailable: {e}"[:200], "pid": None}
    kv = dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)
    pid = int(kv.get("MainPID", "0") or 0) or None
    state = kv.get("ActiveState", "unknown")
    return {
        "ok": state == "active" and pid is not None,
        "detail": f"{service}: {state}",
        "pid": pid,
    }


def parse_launchctl_print(text: str) -> dict[str, Any]:
    state = re.search(r"^\s*state = (\S+)", text, re.M)
    pid = re.search(r"^\s*pid = (\d+)", text, re.M)
    runs = re.search(r"^\s*runs = (\d+)", text, re.M)
    last_exit = re.search(r"^\s*last exit code = (.+)$", text, re.M)
    return {
        "state": state.group(1) if state else None,
        "pid": int(pid.group(1)) if pid else None,
        "runs": int(runs.group(1)) if runs else None,
        "last_exit": last_exit.group(1).strip() if last_exit else None,
    }


def launchd_status(label: str = LAUNCHD_LABEL) -> dict[str, Any]:
    domain = f"gui/{os.getuid()}"
    try:
        r = subprocess.run(
            ["launchctl", "print", f"{domain}/{label}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "detail": f"launchctl unavailable: {e}"[:200], "pid": None}
    if r.returncode != 0:
        return {"ok": False, "detail": f"{domain}/{label} not loaded", "pid": None}
    p = parse_launchctl_print(r.stdout)
    ok = p["state"] == "running" and p["pid"] is not None
    return {
        "ok": ok,
        "detail": f"{domain}/{label}: state {p['state']}, pid {p['pid']}, runs {p['runs']}, last exit {p['last_exit']}",
        **p,
    }


def service_manager_status() -> dict[str, Any]:
    """LINUX + healthy systemd deployment, or MACOS + healthy launchd deployment."""
    osn = host_os()
    if osn == "LINUX":
        return {"os": osn, "manager": "systemd", **systemd_status()}
    if osn == "MACOS":
        return {"os": osn, "manager": "launchd", **launchd_status()}
    return {"os": osn, "manager": None, "ok": False, "detail": f"unsupported OS {osn}", "pid": None}


# ----------------------------------------------------------------------------- runner lock
class RunnerLockedError(RuntimeError):
    pass


class RunnerLock:
    """Exclusive non-blocking flock; released by the OS when the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise RunnerLockedError(
                f"another forward runner holds {self.path}: refusing to start a second runner"
            ) from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

    def __enter__(self) -> RunnerLock:
        self.acquire()
        return self

    def __exit__(self, *a: object) -> None:
        self.release()


def lock_held(path: Path) -> bool:
    """True if some process currently holds the runner lock."""
    if not path.exists():
        return False
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False


# ----------------------------------------------------------------------------- authority lease
def authority_path(forward_root: Path) -> Path:
    return forward_root / "host" / "authority.jsonl"


def authority_state(forward_root: Path) -> dict[str, Any]:
    p = authority_path(forward_root)
    recs = HashChainJournal(p, "authority").records() if p.exists() else []
    events = [r for r in recs if r["kind"] in ("AUTHORITY_CLAIMED", "AUTHORITY_RELEASED")]
    last = events[-1] if events else None
    return {
        "events": len(events),
        "status": "NONE" if last is None else last["kind"].removeprefix("AUTHORITY_"),
        "host_id": last["data"].get("host_id") if last else None,
        "hostname": last["data"].get("hostname") if last else None,
        "at": last["data"].get("at") if last else None,
        "last": last["data"] if last else None,
    }


def claim_authority(forward_root: Path, note: str, extra: dict[str, Any]) -> dict[str, Any]:
    me = host_identity()
    st = authority_state(forward_root)
    if st["status"] == "CLAIMED":
        if st["host_id"] == me["host_id"]:
            return st["last"] or {}
        raise RuntimeError(
            f"authority is held by host {st['hostname']} ({st['host_id']}) since {st['at']}: "
            "release it there first (cold migration)"
        )
    rec = {**me, "at": datetime.now(UTC).isoformat(), "note": note, **extra}
    HashChainJournal(authority_path(forward_root), "authority").append("AUTHORITY_CLAIMED", rec)
    return rec


def release_authority(
    forward_root: Path, note: str, extra: dict[str, Any], legacy: bool = False
) -> dict[str, Any]:
    """Release by the holder. `legacy=True` records the release of a pre-lease host (no claim)."""
    me = host_identity()
    st = authority_state(forward_root)
    if st["status"] == "CLAIMED" and st["host_id"] != me["host_id"]:
        raise RuntimeError(f"authority is held by another host ({st['hostname']}); not released")
    if st["status"] != "CLAIMED" and not legacy:
        raise RuntimeError(f"no claim held by this host (lease status {st['status']})")
    rec = {**me, "at": datetime.now(UTC).isoformat(), "note": note, "legacy": legacy, **extra}
    HashChainJournal(authority_path(forward_root), "authority").append("AUTHORITY_RELEASED", rec)
    return rec


def runner_may_start(forward_root: Path) -> tuple[bool, str]:
    """A runner may start with no lease (pre-lease state) or with a lease CLAIMED by this host."""
    st = authority_state(forward_root)
    me = host_identity()
    if st["status"] == "NONE":
        return True, "no authority lease (pre-lease state)"
    if st["status"] == "CLAIMED" and st["host_id"] == me["host_id"]:
        return True, f"authority held by this host since {st['at']}"
    if st["status"] == "CLAIMED":
        return False, f"authority held by another host: {st['hostname']} ({st['host_id']})"
    return False, f"authority RELEASED by {st['hostname']} at {st['at']}: claim it first"
