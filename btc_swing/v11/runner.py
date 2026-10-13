"""V11 shadow runner: own feed (V10 collector class as a library, V11 raw root), one engine thread,
pid / lock under the V11 root, diagnostic every 5 s, state every 30 s. Nothing here can place an order."""

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
from datetime import UTC, datetime
from typing import Any

from btc_swing.v5.forward.host import RunnerLock, host_identity
from btc_swing.v10.feed import V10Collector
from btc_swing.v11.config import V11Config
from btc_swing.v11.freeze import check_freeze
from btc_swing.v11.live import V11Live
from btc_swing.v11.paths import V11Paths
from btc_swing.v11.research import _clean

log = logging.getLogger(__name__)


class EngineThread(threading.Thread):
    def __init__(self, cfg: V11Config, paths: V11Paths, start_ms: int) -> None:
        super().__init__(name="v11-engine", daemon=True)
        self.cfg, self.paths = cfg, paths
        self.queue: deque[dict[str, Any]] = deque()
        self.stop_ev = threading.Event()
        self.live = V11Live(cfg, paths, start_ms)
        self.errors = 0

    def on_event(self, ev: dict[str, Any]) -> None:
        self.queue.append(ev)

    def run(self) -> None:
        last_diag = last_save = time.time()
        self.live.j_ep.append(
            "RUNNER_STARTED",
            {
                **host_identity(),
                "pid": os.getpid(),
                "observation_start_ms": self.live.observation_start_ms,
                "verification": self.paths.verification,
            },
        )
        while not self.stop_ev.is_set():
            try:
                while self.queue:
                    self.live.on_event(self.queue.popleft())
                self.live.tick(int(time.time() * 1000))
                now = time.time()
                if now - last_diag >= 5:
                    last_diag = now
                    try:
                        d = self.live.diagnostic() | {
                            "queue": len(self.queue),
                            "errors": self.errors,
                        }
                        tmp = self.paths.diagnostic.with_suffix(".tmp")
                        tmp.write_text(json.dumps(_clean(d), sort_keys=True, default=str))
                        tmp.replace(self.paths.diagnostic)
                    except Exception as e:  # reporting only
                        log.warning("diagnostic failed: %s", e)
                if now - last_save >= 30:
                    last_save = now
                    self.live.save()
                time.sleep(0.2)
            except Exception as e:
                self.errors += 1
                log.exception("engine loop error: %s", e)
                time.sleep(1.0)
        self.live.save()
        self.live.j_ep.append("RUNNER_STOPPED", {"pid": os.getpid()})


async def _main(
    cfg: V11Config, paths: V11Paths, duration_s: float | None, start_ms: int
) -> dict[str, Any]:
    from btc_swing.v10.config import load_v10_config

    eng = EngineThread(cfg, paths, start_ms)
    col = V10Collector(
        load_v10_config().collector, paths.raw, on_event=eng.on_event
    )  # same public topics
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    if duration_s:
        loop.call_later(duration_s, stop.set)
    paths.run_pid.write_text(str(os.getpid()))
    eng.start()
    task = asyncio.create_task(col.run(duration_seconds=duration_s))
    await stop.wait()
    col.stop()
    stats = await task
    eng.stop_ev.set()
    eng.join(timeout=30)
    paths.run_pid.unlink(missing_ok=True)
    return stats.as_dict() | {"engine_errors": eng.errors}


def run_forward(
    cfg: V11Config, paths: V11Paths, duration_s: float | None, verification: bool
) -> dict[str, Any]:
    paths.ensure()
    fh = logging.FileHandler(paths.logs / "forward_run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    start_ms = (
        int(time.time() * 1000) if verification else int(check_freeze(cfg)["observation_start_ms"])
    )
    lock = RunnerLock(paths.lock)
    lock.acquire()
    log.info(
        "V11 shadow runner started (pid %s, %s, strategy %s)",
        os.getpid(),
        "VERIFICATION" if verification else "OBSERVATION",
        cfg.strategy_hash[:12],
    )
    try:
        out = asyncio.run(_main(cfg, paths, duration_s, start_ms))
    finally:
        lock.release()
    (
        paths.logs / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    ).write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def runner_alive(paths: V11Paths) -> tuple[bool, int | None]:
    if not paths.run_pid.exists():
        return False, None
    try:
        pid = int(paths.run_pid.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (OSError, ValueError):
        return False, None
