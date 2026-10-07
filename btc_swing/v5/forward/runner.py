"""Forward observation runner: the Bybit public collector and the 5-minute evaluation cycle in one
asyncio process, with pid file, restart recovery (collector state, processor offsets, append-only
signal/trade logs) and the immutable daily report at 00:05 UTC. No orders, no credentials."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from btc_swing.v5.collector import BybitPublicCollector
from btc_swing.v5.forward.pipeline import ForwardContext, run_cycle
from btc_swing.v5.forward.report import write_day_report

log = logging.getLogger(__name__)


def _next_cycle_at(now: float, cycle_s: int, grace_s: int) -> float:
    return (int(now) // cycle_s + 1) * cycle_s + grace_s


async def _cycle_loop(ctx: ForwardContext, reports_dir: Path, stop: asyncio.Event) -> None:
    sc = ctx.fcfg.schedule
    last_report_day = ""
    while not stop.is_set():
        wait = max(
            1.0, _next_cycle_at(time.time(), sc.cycle_seconds, sc.cycle_grace_seconds) - time.time()
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
            break
        except TimeoutError:
            pass
        try:
            out = await asyncio.to_thread(run_cycle, ctx)
            log.info(
                "cycle %s: bars %s (+%s), signals %s, paper closed %s, open %s, %.1fs",
                out["cycle_at"][11:19],
                out["bars_forward"],
                out["new_bars"],
                len(out["new_signals"]),
                out["paper"]["closed_trades"],
                out["paper"]["open_position"] is not None,
                out["elapsed_s"],
            )
        except Exception as e:
            log.exception("cycle failed: %s", e)
        now = datetime.now(UTC)
        if now.minute >= sc.daily_report_utc_minute and now.hour == 0:
            prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            if prev != last_report_day:
                try:
                    p = await asyncio.to_thread(write_day_report, ctx, prev, reports_dir)
                    log.info("daily report %s -> %s", prev, p)
                except Exception as e:
                    log.exception("daily report failed: %s", e)
                last_report_day = prev


async def _main(ctx: ForwardContext, reports_dir: Path, duration_s: float | None) -> dict[str, Any]:
    col = BybitPublicCollector(ctx.fcfg.collector, ctx.paths.root.parent.parent)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    if duration_s:
        loop.call_later(duration_s, stop.set)
    ctx.paths.run_pid.write_text(str(os.getpid()))
    col_task = asyncio.create_task(col.run(duration_seconds=duration_s))
    cyc_task = asyncio.create_task(_cycle_loop(ctx, reports_dir, stop))
    await stop.wait() if duration_s is None else asyncio.sleep(duration_s)
    col.stop()
    stop.set()
    stats = await col_task
    await cyc_task
    ctx.paths.run_pid.unlink(missing_ok=True)
    return stats.as_dict()


def run_forward(
    ctx: ForwardContext, reports_dir: Path, duration_s: float | None = None
) -> dict[str, Any]:
    ctx.paths.logs.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(ctx.paths.logs / "forward_run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    log.info(
        "forward observation runner started (pid %s, frozen V5 %s)",
        os.getpid(),
        ctx.cfg.config_hash[:12],
    )
    out = asyncio.run(_main(ctx, reports_dir, duration_s))
    (
        ctx.paths.logs / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    ).write_text(json.dumps(out, indent=1, sort_keys=True))
    return out
