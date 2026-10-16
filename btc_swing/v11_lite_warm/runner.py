"""V11-LITE-WARM shadow runner: own feed (V10 collector class as a library, V11-LITE-WARM raw root), one
engine thread on the LITE scorer, pid / lock under the V11-LITE-WARM root. Nothing here can place an
order; the V10, V11 and V11-WARM roots are never written. On the observation root only, a separate Demo
supervisor thread (package `demo`) attaches the optional Bybit DEMO observer when, and only while, the
owner's host-bound activation event exists; the shadow engine never waits on it."""

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
from btc_swing.v11.research import _clean
from btc_swing.v11_lite_warm.config import STRATEGY_DELTA, V11LiteWarmConfig
from btc_swing.v11_lite_warm.freeze import check_freeze
from btc_swing.v11_lite_warm.live import V11LiteWarmLive
from btc_swing.v11_lite_warm.paths import V11LiteWarmPaths

log = logging.getLogger(__name__)
MS_1M = 60_000


class LiteEngineThread(threading.Thread):
    def __init__(
        self, lc: V11LiteWarmConfig, v11: V11Config, paths: V11LiteWarmPaths, start_ms: int
    ) -> None:
        super().__init__(name="v11lw-engine", daemon=True)
        self.paths = paths
        self.queue: deque[dict[str, Any]] = deque()
        self.stop_ev = threading.Event()
        self.live = V11LiteWarmLive(v11, paths, start_ms, lc.warm, lc.lite)
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
                "warm_start": self.live.state.get("warm_start"),
                "bootstrap_bars_in_window": self.live.n_bootstrap,
                "strategy_delta": STRATEGY_DELTA,
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
    lc: V11LiteWarmConfig,
    v11: V11Config,
    paths: V11LiteWarmPaths,
    duration_s: float | None,
    start_ms: int,
) -> dict[str, Any]:
    from btc_swing.v10.config import load_v10_config

    eng = LiteEngineThread(lc, v11, paths, start_ms)
    col = V10Collector(load_v10_config().collector, paths.raw, on_event=eng.on_event)
    sup: Any = None
    if not paths.verification:
        from btc_swing.v11_lite_warm.demo.worker import make_supervisor

        def set_sink(f: Any) -> None:
            eng.live.demo_sink = f

        sup = make_supervisor(lc, v11, paths.root, set_sink)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    if duration_s:
        loop.call_later(duration_s, stop.set)
    paths.run_pid.write_text(str(os.getpid()))
    eng.start()
    if sup is not None:
        sup.start()
    task = asyncio.create_task(col.run(duration_seconds=duration_s))
    await stop.wait()
    col.stop()
    stats = await task
    if sup is not None:
        sup.stop_ev.set()
        sup.join(timeout=60)
    eng.stop_ev.set()
    eng.join(timeout=30)
    paths.run_pid.unlink(missing_ok=True)
    return stats.as_dict() | {"engine_errors": eng.errors}


def seed_required(paths: V11LiteWarmPaths) -> None:
    if not paths.seed_record.exists():
        raise RuntimeError(
            "V11-LITE-WARM root is not seeded: run `btc-swing v11lw seed` (bootstrap bars) first"
        )


def run_forward(
    lc: V11LiteWarmConfig,
    v11: V11Config,
    paths: V11LiteWarmPaths,
    duration_s: float | None,
    verification: bool,
) -> dict[str, Any]:
    paths.ensure()
    seed_required(paths)
    fh = logging.FileHandler(paths.logs / "forward_run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    if verification:
        start_ms = int(time.time() * 1000) + lc.warm.live_warmup_min * MS_1M
    else:
        start_ms = int(check_freeze(lc, v11)["observation_start_ms"])
    lock = RunnerLock(paths.lock)
    lock.acquire()
    log.info(
        "V11-LITE-WARM shadow runner started (pid %s, %s, V11 strategy %s, in-window from %s)",
        os.getpid(),
        "VERIFICATION" if verification else "OBSERVATION",
        v11.strategy_hash[:12],
        datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat(),
    )
    try:
        out = asyncio.run(_main(lc, v11, paths, duration_s, start_ms))
    finally:
        lock.release()
    (
        paths.logs / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    ).write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def runner_alive(paths: V11LiteWarmPaths) -> tuple[bool, int | None]:
    if not paths.run_pid.exists():
        return False, None
    try:
        pid = int(paths.run_pid.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (ValueError, ProcessLookupError, PermissionError):
        return False, None
