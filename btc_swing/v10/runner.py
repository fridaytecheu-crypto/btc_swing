"""V10 forward runner: the public feed (asyncio) hands events to ONE engine thread (events, 1-second
ticks, decisions, blocking Demo calls), so the feed is never stalled by execution. Pid file, single-
runner lock, diagnostic snapshot every 5 s, state save every 30 s, daily report at 00:05 UTC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from btc_swing.v5.demo.preflight import is_cloud_session_container
from btc_swing.v5.forward.host import RunnerLock, authority_state, host_identity
from btc_swing.v10.activation import effective_activation
from btc_swing.v10.config import V10Config
from btc_swing.v10.engine import V10Engine, _clean
from btc_swing.v10.feed import V10Collector
from btc_swing.v10.freeze import check_freeze
from btc_swing.v10.paths import REPORTS_DIR, V10Paths
from btc_swing.v10.report import daily_report

log = logging.getLogger(__name__)


def build_demo_executor(cfg: V10Config, paths: V10Paths) -> Any:
    """Only with an effective V10 activation for this host, off the cloud, with the lease held."""
    from btc_swing.v5.demo.client import BybitDemoClient
    from btc_swing.v5.demo.config import DEFAULT_DEMO_CONFIG_PATH, ExecutionMode, load_demo_config
    from btc_swing.v5.demo.journal import HashChainJournal
    from btc_swing.v10.execution import V10DemoExecutor

    act = effective_activation(paths)
    if act is None:
        raise RuntimeError("no STRATEGY_DEMO_V10_ACTIVATED event for this host")
    if is_cloud_session_container():
        raise RuntimeError("cloud-session container: Demo execution refused")
    auth = authority_state(paths.v5_root)
    if auth["status"] != "CLAIMED" or auth["host_id"] != host_identity()["host_id"]:
        raise RuntimeError("authority lease not held by this host")
    check_freeze(cfg)
    dcfg = load_demo_config(DEFAULT_DEMO_CONFIG_PATH, ExecutionMode.STRATEGY_DEMO)
    journal = HashChainJournal(
        paths.demo_journal, "strategy_demo_v10", forbid_tags=("EXECUTION_SMOKE",)
    )
    client = BybitDemoClient(dcfg, dcfg.mode, journal, "STRATEGY_DEMO_V10")
    ex = V10DemoExecutor(
        cfg, dcfg, client, journal, paths.demo_state, paths.demo_trades, int(act["activated_at_ms"])
    )
    log.info("V10 Demo executor built: recovery %s", ex.recover())
    return ex


class EngineThread(threading.Thread):
    def __init__(
        self, cfg: V10Config, paths: V10Paths, observation_start_ms: int, allow_demo: bool
    ) -> None:
        super().__init__(name="v10-engine", daemon=True)
        self.cfg, self.paths = cfg, paths
        self.queue: deque[dict[str, Any]] = deque()
        self.stop_ev = threading.Event()
        self.allow_demo = allow_demo
        self.engine = V10Engine(cfg, paths, observation_start_ms, mode="SHADOW")
        self.errors = 0
        self.refused: str | None = None
        self._last_report_day = ""

    def on_event(self, ev: dict[str, Any]) -> None:
        self.queue.append(ev)

    def _check_activation(self) -> None:
        if not self.allow_demo:
            return
        act = effective_activation(self.paths)
        if act is None:
            if self.engine.executor is not None and not self.engine.executor.has_position():
                log.warning("V10 Demo no longer active on this host: executor released")
                self.engine.executor, self.engine.mode = None, "SHADOW"
            return
        if self.engine.executor is None:
            try:
                self.engine.executor = build_demo_executor(self.cfg, self.paths)
                self.engine.mode = "DEMO"
                self.refused = None
                self.engine.j_sig.append(
                    "DEMO_EXECUTOR_ATTACHED", {"activated_at": act["activated_at"]}
                )
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"[:300]
                if msg != self.refused:
                    log.error("V10 Demo refused (fails closed, shadow continues): %s", msg)
                self.refused = msg

    def run(self) -> None:
        tick_s = self.cfg.features.tick_seconds
        next_tick = time.time()
        last_save = last_diag = last_act = time.time()
        self.engine.j_ep.append(
            "RUNNER_STARTED",
            {
                **host_identity(),
                "pid": os.getpid(),
                "mode": self.engine.mode,
                "observation_start_ms": self.engine.observation_start_ms,
            },
        )
        self._check_activation()
        while not self.stop_ev.is_set():
            try:
                while self.queue:
                    self.engine.on_event(self.queue.popleft())
                now = time.time()
                if now >= next_tick:
                    self.engine.tick(int(now * 1000))
                    next_tick = now + tick_s
                if now - last_diag >= 5:
                    last_diag = now
                    self._write_diag()
                if now - last_save >= 30:
                    last_save = now
                    self.engine.save()
                if now - last_act >= 30:
                    last_act = now
                    self._check_activation()
                    self._daily_report()
                time.sleep(0.05)
            except Exception as e:
                self.errors += 1
                log.exception("engine loop error: %s", e)
                time.sleep(1.0)
        self.engine.save()
        self.engine.j_ep.append("RUNNER_STOPPED", {"pid": os.getpid()})

    def _write_diag(self) -> None:
        d = self.engine.diagnostic() | {
            "written_at": datetime.now(UTC).isoformat(),
            "queue": len(self.queue),
            "mode": self.engine.mode,
            "demo_refused": self.refused,
        }
        tmp = self.paths.derived / "diagnostic.tmp"
        tmp.write_text(json.dumps(_clean(d), sort_keys=True))
        tmp.replace(self.paths.derived / "diagnostic.json")

    def _daily_report(self) -> None:
        now = datetime.now(UTC)
        if now.hour == 0 and now.minute >= self.cfg.schedule.daily_report_utc_minute:
            prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            if prev != self._last_report_day and not self.paths.verification:
                p = REPORTS_DIR / f"v10_day_{prev}.md"
                if not p.exists():
                    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                    p.write_text(daily_report(self.cfg, self.paths, prev))
                self._last_report_day = prev


async def _main(
    cfg: V10Config, paths: V10Paths, duration_s: float | None, start_ms: int, allow_demo: bool
) -> dict[str, Any]:
    eng = EngineThread(cfg, paths, start_ms, allow_demo)
    col = V10Collector(cfg.collector, paths.raw, on_event=eng.on_event)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    if duration_s:
        loop.call_later(duration_s, stop.set)
    paths.run_pid.write_text(str(os.getpid()))
    eng.start()
    col_task = asyncio.create_task(col.run(duration_seconds=duration_s))
    await stop.wait()
    col.stop()
    stats = await col_task
    eng.stop_ev.set()
    eng.join(timeout=30)
    paths.run_pid.unlink(missing_ok=True)
    return stats.as_dict() | {"engine_errors": eng.errors}


def run_forward(
    cfg: V10Config, paths: V10Paths, duration_s: float | None, verification: bool
) -> dict[str, Any]:
    paths.ensure()
    fh = logging.FileHandler(paths.logs / "forward_run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    if verification:
        start_ms = int(time.time() * 1000)
        allow_demo = False
    else:
        rec = check_freeze(cfg)
        start_ms = int(rec["observation_start_ms"])
        auth = (
            authority_state(paths.v5_root)
            if paths.v5_root.exists()
            else {"status": "NONE", "host_id": None, "hostname": None}
        )
        me = host_identity()
        if auth["status"] == "CLAIMED" and auth["host_id"] != me["host_id"]:
            raise RuntimeError(
                f"authority lease held by another host ({auth['hostname']}): V10 observation refused here"
            )
        allow_demo = not is_cloud_session_container()
    lock = RunnerLock(paths.lock)
    lock.acquire()
    log.info(
        "V10 runner started (pid %s, %s, config %s, observation_start %s)",
        os.getpid(),
        "VERIFICATION" if verification else "OBSERVATION",
        cfg.config_hash[:12],
        start_ms,
    )
    try:
        out = asyncio.run(_main(cfg, paths, duration_s, start_ms, allow_demo))
    finally:
        lock.release()
    (
        paths.logs / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    ).write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def runner_alive(paths: V10Paths) -> tuple[bool, int | None]:
    if not paths.run_pid.exists():
        return False, None
    try:
        pid = int(paths.run_pid.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None


def diagnostic_snapshot(paths: V10Paths) -> dict[str, Any] | None:
    p = paths.derived / "diagnostic.json"
    return json.loads(p.read_text()) if p.exists() else None


def latest_raw_age_s(paths: V10Paths) -> float | None:
    files = (
        sorted(paths.raw.glob("*/*.jsonl*"), key=lambda p: p.stat().st_mtime)
        if paths.raw.exists()
        else []
    )
    return time.time() - files[-1].stat().st_mtime if files else None


def logs_dir(paths: V10Paths) -> Path:
    return paths.logs
