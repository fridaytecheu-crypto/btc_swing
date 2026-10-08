"""Operations for the forward observation: health check (monitoring), migration export/verify,
integrity snapshots/comparison and the human-readable status. Nothing here touches V5 logic."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.journal import verify_chain
from btc_swing.v5.forward.config import ForwardContextLike
from btc_swing.v5.forward.pipeline import _f, _iso, _read_jsonl
from btc_swing.v5.forward.raw import load_forward_bars
from btc_swing.v5.forward.report import status as status_dict

STATE_DIRS = ("bybit", "seed", "derived", "signals", "paper", "logs", "demo", "host")
MS_5M = 300_000


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_fileobj(fh: Any) -> str:
    h = hashlib.sha256()
    while True:
        chunk = fh.read(1 << 20)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _pid_alive(pid_file: Path) -> tuple[bool, int | None]:
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None


def _latest_raw_mtime(raw: Path) -> float | None:
    days = sorted(p for p in raw.glob("*") if p.is_dir())
    for d in reversed(days):
        files = sorted(d.glob("*.jsonl*"), key=lambda p: p.stat().st_mtime)
        if files:
            return files[-1].stat().st_mtime
    return None


def disk_usage(data_dir: Path, forward_root: Path) -> dict[str, Any]:
    du = shutil.disk_usage(data_dir)
    sizes = {
        d: sum(f.stat().st_size for f in (forward_root / d).rglob("*") if f.is_file())
        for d in STATE_DIRS
        if (forward_root / d).exists()
    }
    return {
        "free_gb": du.free / 1e9,
        "total_gb": du.total / 1e9,
        "used_frac": (du.total - du.free) / du.total,
        "forward_gb": sum(sizes.values()) / 1e9,
        "forward_by_dir_mb": {k: v / 1e6 for k, v in sizes.items()},
    }


# ----------------------------------------------------------------------------- health
def health(
    ctx: ForwardContextLike,
    data_dir: Path,
    stale_seconds: float = 120.0,
    bar_stale_seconds: float = 900.0,
    min_free_gb: float = 5.0,
) -> dict[str, Any]:
    """Process, collector heartbeat, stale data, bar lag and disk. `ok` is False on any problem."""
    paths = ctx.paths
    alive, pid = _pid_alive(paths.run_pid)
    st = (
        json.loads((paths.raw / "state.json").read_text())
        if (paths.raw / "state.json").exists()
        else {}
    )
    hb_age = _age_s(st.get("saved_at"))
    raw_m = _latest_raw_mtime(paths.raw)
    raw_age = (time.time() - raw_m) if raw_m else None
    cycles = _read_jsonl(paths.cycle_log)
    last_bar = cycles[-1].get("last_bar_close") if cycles else None
    bar_age = _age_s(str(last_bar)) if last_bar else None
    cycle_age = _age_s(str(cycles[-1].get("cycle_at"))) if cycles else None
    disk = disk_usage(data_dir, paths.root)
    problems: list[str] = []
    if not alive:
        problems.append("runner process not alive")
    if raw_age is None or raw_age > stale_seconds:
        problems.append(
            f"stale raw data: last write {raw_age if raw_age is None else round(raw_age)} s ago"
        )
    if hb_age is None or hb_age > max(stale_seconds * 3, 600):
        problems.append(
            f"collector heartbeat (state.json) {hb_age if hb_age is None else round(hb_age)} s old"
        )
    if bar_age is None or bar_age > bar_stale_seconds:
        problems.append(
            f"last completed 5m bar {bar_age if bar_age is None else round(bar_age)} s old"
        )
    if cycle_age is None or cycle_age > bar_stale_seconds:
        problems.append(f"last cycle {cycle_age if cycle_age is None else round(cycle_age)} s ago")
    if disk["free_gb"] < min_free_gb:
        problems.append(f"low disk: {disk['free_gb']:.1f} GB free")
    if cycles and cycles[-1].get("paper_open") is None:
        pass
    stats = st.get("stats", {})
    return {
        "ok": not problems,
        "problems": problems,
        "checked_at": datetime.now(UTC).isoformat(),
        "runner_alive": alive,
        "runner_pid": pid,
        "collector_heartbeat_age_s": hb_age,
        "last_raw_write_age_s": raw_age,
        "last_bar_close": last_bar,
        "last_bar_age_s": bar_age,
        "last_cycle_age_s": cycle_age,
        "sequence_gaps": stats.get("sequence_gaps"),
        "duplicates": stats.get("duplicates"),
        "reconnects": stats.get("reconnects"),
        "disk": disk,
    }


# ----------------------------------------------------------------------------- integrity
def _journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "lines": 0, "sha256": None}
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return {
        "exists": True,
        "lines": len(lines),
        "sha256": _sha(path),
        "prefix_sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
    }


def _prefix_sha(path: Path, n: int) -> str | None:
    if not path.exists():
        return None
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()][:n]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def integrity_snapshot(ctx: ForwardContextLike) -> dict[str, Any]:
    """Counts, uniqueness and content hashes of every stateful artefact (host-independent)."""
    paths = ctx.paths
    bars = load_forward_bars(paths.bars_dir)
    sigs = _read_jsonl(paths.signals_file)
    outs = _read_jsonl(paths.outcomes_file)
    trades = _read_jsonl(paths.paper_trades)
    pstate = json.loads(paths.processor_state.read_text()) if paths.processor_state.exists() else {}
    cstate = (
        json.loads((paths.raw / "state.json").read_text())
        if (paths.raw / "state.json").exists()
        else {}
    )
    raw_files = sorted(p for p in paths.raw.glob("*/*.jsonl*") if p.is_file())
    seed_files = (
        sorted((paths.seed / "days").glob("*.parquet")) if (paths.seed / "days").exists() else []
    )
    bar_files = sorted(paths.bars_dir.glob("*.parquet"))
    ids = [s["signal_id"] for s in sigs]
    oids = [o["signal_id"] for o in outs]
    tkeys = [(t["family"], t["side"], int(t["entry_ms"])) for t in trades]
    return {
        "taken_at": datetime.now(UTC).isoformat(),
        "observation_start": _iso(ctx.start_ms),
        "frozen_v5_config_hash": ctx.cfg.config_hash,
        "raw": {
            "files": len(raw_files),
            "bytes": sum(p.stat().st_size for p in raw_files),
            "last_file": raw_files[-1].relative_to(paths.raw).as_posix() if raw_files else None,
            "closed_files_sha256": {
                p.relative_to(paths.raw).as_posix(): _sha(p) for p in raw_files if p.suffix == ".gz"
            },
        },
        "collector_state": {
            "last_orderbook_u": cstate.get("last_orderbook_u"),
            "saved_at": cstate.get("saved_at"),
        },
        "processor": {
            "file": pstate.get("file"),
            "line": pstate.get("line"),
            "last_bar_close": _iso(int(pstate["last_bar_close_ms"]))
            if pstate.get("last_bar_close_ms")
            else None,
            "bars": pstate.get("bars"),
        },
        "bars": {
            "partitions": len(bar_files),
            "rows": bars.height,
            "unique_open_times": int(bars["open_time_ms"].n_unique()) if bars.height else 0,
            "first": _iso(_f(bars["open_time_ms"].min())) if bars.height else None,
            "last_close": _iso(_f(bars["close_time_ms"].max())) if bars.height else None,
            "partition_sha256": {p.name: _sha(p) for p in bar_files},
        },
        "seed": {
            "days": len(seed_files),
            "manifest_sha256": _sha(paths.seed_manifest) if paths.seed_manifest.exists() else None,
            "days_sha256": {p.name: _sha(p) for p in seed_files},
        },
        "signals": {
            **_journal(paths.signals_file),
            "unique_ids": len(set(ids)),
            "duplicates": len(ids) - len(set(ids)),
        },
        "outcomes": {
            **_journal(paths.outcomes_file),
            "unique_ids": len(set(oids)),
            "duplicates": len(oids) - len(set(oids)),
            "without_signal": len(set(oids) - set(ids)),
        },
        "paper_trades": {
            **_journal(paths.paper_trades),
            "unique_keys": len(set(tkeys)),
            "duplicates": len(tkeys) - len(set(tkeys)),
        },
        "paper_state": json.loads(paths.paper_state.read_text())
        if paths.paper_state.exists()
        else {},
        "cycles": _journal(paths.cycle_log),
        "hash_chained": {
            name: {**_journal(path), "chain_ok": verify_chain(path)["ok"]}
            for name, path in _chained_journals(paths.root).items()
        },
        "demo_trades": _demo_trades(paths.root / "demo" / "demo_trades.jsonl"),
    }


def _chained_journals(root: Path) -> dict[str, Path]:
    return {
        "demo_smoke_journal": root / "demo" / "smoke_journal.jsonl",
        "demo_strategy_journal": root / "demo" / "strategy_journal.jsonl",
        "host_authority": root / "host" / "authority.jsonl",
    }


def _demo_trades(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    ids = [str(r.get("signal_id")) for r in rows]
    return {**_journal(path), "unique_ids": len(set(ids)), "duplicates": len(ids) - len(set(ids))}


def integrity_compare(
    before: dict[str, Any], after: dict[str, Any], after_ctx: ForwardContextLike
) -> dict[str, Any]:
    """`after` must be a continuation of `before`: same freeze, every journal's old lines intact
    (prefix hash), counts non-decreasing, no duplicates, processor offset not behind, closed raw
    files byte-identical, seed identical."""
    checks: list[dict[str, Any]] = []
    paths = after_ctx.paths

    def chk(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    chk(
        "frozen V5 config hash unchanged",
        before["frozen_v5_config_hash"] == after["frozen_v5_config_hash"],
        f"{before['frozen_v5_config_hash'][:12]} vs {after['frozen_v5_config_hash'][:12]}",
    )
    chk(
        "observation start unchanged",
        before["observation_start"] == after["observation_start"],
        f"{before['observation_start']} vs {after['observation_start']}",
    )
    for name, path in (
        ("signals", paths.signals_file),
        ("outcomes", paths.outcomes_file),
        ("paper_trades", paths.paper_trades),
        ("cycles", paths.cycle_log),
    ):
        b, a = before[name], after[name]
        chk(
            f"{name}: count non-decreasing",
            a["lines"] >= b["lines"],
            f"{b['lines']} -> {a['lines']}",
        )
        if b["lines"]:
            chk(
                f"{name}: earlier lines intact",
                _prefix_sha(path, b["lines"]) == b.get("prefix_sha256"),
                f"prefix of {b['lines']} lines",
            )
        if "duplicates" in a:
            chk(f"{name}: no duplicates", a["duplicates"] == 0, f"{a['duplicates']} duplicate(s)")
    for name, path in _chained_journals(paths.root).items():
        b = (before.get("hash_chained") or {}).get(name)
        a = (after.get("hash_chained") or {}).get(name) or {}
        if not b or not b.get("lines"):
            continue
        chk(f"{name}: hash chain valid", bool(a.get("chain_ok")), str(a.get("lines")))
        chk(
            f"{name}: earlier records intact (prefix identity)",
            _prefix_sha(path, b["lines"]) == b.get("prefix_sha256"),
            f"prefix of {b['lines']} records",
        )
    bt = before.get("demo_trades") or {}
    at = after.get("demo_trades") or {}
    if bt.get("lines"):
        chk(
            "demo trades: earlier lines intact",
            _prefix_sha(paths.root / "demo" / "demo_trades.jsonl", bt["lines"])
            == bt.get("prefix_sha256"),
            f"prefix of {bt['lines']} lines",
        )
    if at:
        chk("demo trades: no duplicates", at.get("duplicates", 0) == 0, str(at.get("duplicates")))
    chk(
        "outcomes reference known signals",
        after["outcomes"].get("without_signal", 0) == 0,
        str(after["outcomes"].get("without_signal")),
    )
    chk(
        "bars: unique open times",
        after["bars"]["rows"] == after["bars"]["unique_open_times"],
        f"{after['bars']['rows']} rows / {after['bars']['unique_open_times']} unique",
    )
    chk(
        "bars: count non-decreasing",
        after["bars"]["rows"] >= before["bars"]["rows"],
        f"{before['bars']['rows']} -> {after['bars']['rows']}",
    )
    for name, sha in before["bars"]["partition_sha256"].items():
        if (
            name != sorted(before["bars"]["partition_sha256"])[-1]
        ):  # the last partition may legitimately grow
            chk(
                f"bars partition {name} unchanged",
                after["bars"]["partition_sha256"].get(name) == sha,
                name,
            )
    chk(
        "processor offset not behind",
        (after["processor"].get("bars") or 0) >= (before["processor"].get("bars") or 0),
        f"{before['processor']} -> {after['processor']}",
    )
    for name, sha in before["raw"]["closed_files_sha256"].items():
        chk(
            f"raw {name} byte-identical", after["raw"]["closed_files_sha256"].get(name) == sha, name
        )
    chk(
        "seed identical",
        before["seed"]["days_sha256"] == after["seed"]["days_sha256"]
        and before["seed"]["manifest_sha256"] == after["seed"]["manifest_sha256"],
        f"{after['seed']['days']} days",
    )
    chk(
        "collector last_orderbook_u carried",
        before["collector_state"]["last_orderbook_u"]
        == after["collector_state"]["last_orderbook_u"]
        or (after["collector_state"]["saved_at"] or "")
        > (before["collector_state"]["saved_at"] or ""),
        "same state or newer state",
    )
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "before_taken_at": before["taken_at"],
        "after_taken_at": after["taken_at"],
    }


# ----------------------------------------------------------------------------- migration
def manifest_path_for(out: Path) -> Path:
    return out.parent / (out.name.removesuffix(".tar.gz").removesuffix(".tgz") + ".manifest.json")


def export_state(
    ctx: ForwardContextLike,
    out: Path,
    freeze_path: Path,
    config_paths: list[Path],
    include_seed_raw: bool = False,
    exclude_dirs: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Tarball of every stateful forward artefact + freeze + configs, with a sha256 manifest of
    every file. Take it with the runner STOPPED (cold export) so journals and raw files are final."""
    from btc_swing.v5.forward.host import lock_held

    if lock_held(ctx.paths.root / "forward_run.lock"):
        raise RuntimeError("a forward runner holds the runner lock: stop it before a cold export")
    paths = ctx.paths
    alive, _ = _pid_alive(paths.run_pid)
    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "runner_was_alive": alive,
        "frozen_v5_config_hash": ctx.cfg.config_hash,
        "observation_start": _iso(ctx.start_ms),
        "files": {},
    }
    members: list[tuple[Path, str]] = []
    manifest["excluded_dirs"] = list(exclude_dirs)
    for d in STATE_DIRS:
        if d in exclude_dirs:
            continue
        root = paths.root / d
        if not root.exists():
            continue
        for f in sorted(p for p in root.rglob("*") if p.is_file()):
            if f.name.endswith(".pid"):
                continue
            if d == "seed" and f.parent.name == "raw" and not include_seed_raw:
                continue  # archive csv.gz files: re-downloadable, sha256 in seed_manifest.jsonl
            members.append((f, f"forward/{f.relative_to(paths.root).as_posix()}"))
    members.append((freeze_path, f"manifests/{freeze_path.name}"))
    for c in config_paths:
        members.append((c, f"config/{c.name}"))
    manifest["integrity"] = integrity_snapshot(ctx)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz", compresslevel=3) as tar:
        for f, arc in members:
            tar.add(f, arcname=arc)
    # hash what is actually inside the archive (a live hour file may still be growing on the
    # source while a hot export runs; the manifest must describe the bundle, not the source)
    with tarfile.open(out, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            fh = tar.extractfile(m)
            manifest["files"][m.name] = {
                "sha256": _sha_fileobj(fh) if fh is not None else None,
                "bytes": m.size,
            }
    manifest["archive"] = str(out)
    manifest["archive_sha256"] = _sha(out)
    manifest["n_files"] = len(members)
    manifest["seed_raw_included"] = include_seed_raw
    mpath = manifest_path_for(out)
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True, default=str))
    return manifest


def verify_state(
    data_dir: Path, manifest_path: Path, freeze_path: Path, config_dir: Path
) -> dict[str, Any]:
    """Re-hash every migrated file on the target host against the migration manifest."""
    man = json.loads(manifest_path.read_text())
    root = data_dir / "btc" / "forward"
    missing: list[str] = []
    mismatched: list[str] = []
    for arc, info in man["files"].items():
        if arc.startswith("forward/"):
            p = root / arc[len("forward/") :]
        elif arc.startswith("manifests/"):
            p = freeze_path.parent / arc[len("manifests/") :]
        else:
            p = config_dir / arc[len("config/") :]
        if not p.exists():
            missing.append(arc)
        elif _sha(p) != info["sha256"]:
            mismatched.append(arc)
    freeze = json.loads(freeze_path.read_text()) if freeze_path.exists() else {}
    ok_freeze = freeze.get("v5_config_hash") == man["frozen_v5_config_hash"]
    return {
        "ok": not missing and not mismatched and ok_freeze,
        "files_checked": len(man["files"]),
        "missing": missing,
        "mismatched": mismatched,
        "freeze_hash_matches": ok_freeze,
        "manifest_created_at": man["created_at"],
        "observation_start": man["observation_start"],
    }


# ----------------------------------------------------------------------------- status text
def status_text(ctx: ForwardContextLike, data_dir: Path) -> str:
    rows = status_rows(ctx, data_dir)
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(w)}  {v}" for k, v in rows)


def status_rows(ctx: ForwardContextLike, data_dir: Path) -> list[tuple[str, str]]:
    s = status_dict(ctx)
    h = health(ctx, data_dir)
    c = s["collector"]
    st, lat = c.get("stats", {}), c.get("latency_ms", {})
    connected = (
        h["last_raw_write_age_s"] is not None
        and h["last_raw_write_age_s"] <= 120
        and h["runner_alive"]
    )
    op = s.get("open_paper_position")
    cp = s["cumulative_paper"]
    d = h["disk"]
    rows = [
        ("runner", f"ALIVE (pid {h['runner_pid']})" if h["runner_alive"] else "DEAD"),
        ("collector", "CONNECTED" if connected else "DISCONNECTED"),
        (
            "last exchange message (raw write)",
            f"{c.get('last_raw_write') or 'n/a'} ({'n/a' if h['last_raw_write_age_s'] is None else str(round(h['last_raw_write_age_s'])) + ' s ago'})",
        ),
        (
            "last completed 5m bar",
            f"{h['last_bar_close'] or 'n/a'} ({'n/a' if h['last_bar_age_s'] is None else str(round(h['last_bar_age_s'])) + ' s ago'})",
        ),
        (
            "latency p50 / p95 (ms)",
            f"{lat.get('p50', 'n/a')} / {lat.get('p95', lat.get('p90', 'n/a'))}",
        ),
        ("sequence gaps", str(st.get("sequence_gaps", "n/a"))),
        ("duplicates", str(st.get("duplicates", "n/a"))),
        ("messages (current process)", str(st.get("messages", "n/a"))),
        ("signals today / total", f"{s['signals_today']} / {s['signals_total']}"),
        (
            "open paper trade",
            f"{op['family']} {op['side']} entry {op['entry_price']:.1f} stop {op['final_stop']:.1f} unrealised {op['POSITION_PNL']:.2f} USDT"
            if op
            else "none",
        ),
        ("closed paper trades", str(cp.get("closed_trades") or 0)),
        (
            "cumulative hypothetical P&L (USDT)",
            f"{cp.get('cumulative_net_pnl') or 0:.2f} (equity mtm {cp.get('equity_mtm') or 0:.2f})",
        ),
        (
            "disk",
            f"{d['free_gb']:.1f} GB free of {d['total_gb']:.0f} GB; forward data {d['forward_gb']:.2f} GB",
        ),
        ("observation start", s["observation_start"]),
        ("frozen V5 config hash", s["frozen_v5_config_hash"]),
        ("health", "OK" if h["ok"] else "PROBLEMS: " + "; ".join(h["problems"])),
    ]
    try:
        from btc_swing.v5.demo.config import DEFAULT_DEMO_CONFIG_PATH, load_demo_config
        from btc_swing.v5.demo.runtime import demo_status, demo_status_lines

        if DEFAULT_DEMO_CONFIG_PATH.exists():
            rows += demo_status_lines(demo_status(ctx, load_demo_config(DEFAULT_DEMO_CONFIG_PATH)))
    except Exception as e:
        rows.append(("demo execution", f"status unavailable: {type(e).__name__}: {e}"[:120]))
    return rows


# ----------------------------------------------------------------------------- coverage
def _intervals(slots: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for t in slots:
        if out and t == out[-1][1]:
            out[-1] = (out[-1][0], t + MS_5M)
        else:
            out.append((t, t + MS_5M))
    return out


def coverage(ctx: ForwardContextLike, now_ms: int | None = None) -> dict[str, Any]:
    """What forward data exists since the observation start, and every period WITHOUT live
    collector data. A 5-minute slot is live only if the public collector recorded trades in it;
    slots without trades (collector down: the processor writes them as zero-trade rows, which the
    pipeline flags `gap_filled`) and slots with no row at all are MISSING. Nothing is backfilled."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    bars = load_forward_bars(ctx.paths.bars_dir)
    first_slot = -(-ctx.start_ms // MS_5M) * MS_5M  # first complete 5m slot after the start
    live: set[int] = set()
    present: set[int] = set()
    if bars.height:
        for t, n in zip(bars["open_time_ms"].to_list(), bars["trades"].to_list(), strict=True):
            present.add(int(t))
            if n and int(n) > 0:
                live.add(int(t))
    last_close = (max(present) + MS_5M) if present else first_slot
    horizon = max(last_close, (now // MS_5M) * MS_5M)
    slots = range(first_slot, horizon, MS_5M)
    missing = [t for t in slots if t not in live]
    gaps = []
    for a, b in _intervals(missing):
        kind = (
            "since_last_bar (no runner)"
            if a >= last_close
            else "before_first_forward_bar"
            if not present or b <= min(present)
            else "collector_down (zero-trade rows, flagged gap_filled)"
        )
        gaps.append({"from": _iso(a), "to": _iso(b), "bars": (b - a) // MS_5M, "kind": kind})
    ids = [s["signal_id"] for s in _read_jsonl(ctx.paths.signals_file)]
    trades = _read_jsonl(ctx.paths.paper_trades)
    tkeys = [(t["family"], t["side"], int(t["entry_ms"])) for t in trades]
    return {
        "observation_start": _iso(ctx.start_ms),
        "checked_at": _iso(now),
        "forward_rows": bars.height,
        "live_bars": len(live),
        "expected_bars_to_now": len(slots),
        "missing_bars_to_now": len(missing),
        "first_forward_bar": _iso(min(present)) if present else None,
        "last_completed_bar_close": _iso(last_close) if present else None,
        "missing_periods": gaps,
        "duplicates": {
            "bars": bars.height - (int(bars["open_time_ms"].n_unique()) if bars.height else 0),
            "signals": len(ids) - len(set(ids)),
            "paper_trades": len(tkeys) - len(set(tkeys)),
        },
        "signals": len(ids),
        "paper_trades": len(trades),
    }


def host_status_text(
    ctx: ForwardContextLike, data_dir: Path, extra_rows: list[tuple[str, str]] | None = None
) -> str:
    """Authoritative-host status: host and service manager rows, the forward/demo status and
    every period without live collector data since the observation start."""
    from btc_swing.v5.forward.host import (
        authority_state,
        host_identity,
        lock_held,
        service_manager_status,
    )

    me = host_identity()
    sm = service_manager_status()
    lease = authority_state(ctx.paths.root)
    cov = coverage(ctx)
    head = [
        ("host", f"{me['os']} {me['hostname']} (host id {me['host_id']})"),
        (
            f"{sm.get('manager') or 'service manager'} status",
            ("HEALTHY " if sm.get("ok") else "NOT HEALTHY ") + str(sm.get("detail")),
        ),
        (
            "authority lease",
            f"{lease['status']} by {lease['hostname'] or 'n/a'} at {lease['at'] or 'n/a'}"
            + (" (THIS host)" if lease["host_id"] == me["host_id"] else ""),
        ),
        (
            "single-runner lock",
            "held" if lock_held(ctx.paths.root / "forward_run.lock") else "not held",
        ),
    ]
    tail = [
        (
            "forward coverage",
            f"{cov['live_bars']} live bars of {cov['expected_bars_to_now']} since the start; "
            f"{cov['missing_bars_to_now']} missing (never backfilled)",
        ),
        *[
            (f"  missing {g['kind']}", f"{g['from']} -> {g['to']} ({g['bars']} bars)")
            for g in cov["missing_periods"]
        ],
        ("duplicates (bars / signals / paper trades)", json.dumps(cov["duplicates"])),
        *(extra_rows or []),
    ]
    rows = head + status_rows(ctx, data_dir) + tail
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(w)}  {v}" for k, v in rows)
