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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from btc_swing.v5.collector import BybitPublicCollector
from btc_swing.v5.forward.host import RunnerLock, runner_may_start
from btc_swing.v5.forward.pipeline import ForwardContext, run_cycle
from btc_swing.v5.forward.report import write_day_report

log = logging.getLogger(__name__)


def _next_cycle_at(now: float, cycle_s: int, grace_s: int) -> float:
    return (int(now) // cycle_s + 1) * cycle_s + grace_s


def _demo_hook(
    ctx: Any,
    build: Callable[[Any, Any], Any] | None = None,
    cycle: Callable[..., Any] | None = None,
    label: str = "STRATEGY_DEMO",
) -> Any:
    """Per-cycle demo hook. STRATEGY_DEMO is in effect only while this host holds an active
    STRATEGY_DEMO_ACTIVATED event in the strategy's own journal (checked every cycle, so activation
    needs no restart); the executor is built lazily and every refusal fails closed while the
    observation continues. `build`/`cycle` default to the V5 executor; V5.1 passes its own."""
    from btc_swing.v5.demo.activation import effective_activation
    from btc_swing.v5.demo.config import DEFAULT_DEMO_CONFIG_PATH, ExecutionMode, load_demo_config
    from btc_swing.v5.demo.runtime import build_executor, demo_cycle

    build_fn = build or build_executor
    cycle_fn = cycle or demo_cycle
    held: dict[str, Any] = {"ex": None, "at": None, "refused": None}

    def hook(a: Any, b: Any, res: Any) -> Any:
        act = effective_activation(ctx.paths.root)
        if act is None:
            if held["ex"] is not None:
                log.warning("%s no longer active on this host: executor released", label)
            held.update(ex=None, at=None)
            return {"mode": "DISABLED", "note": f"no active {label}_ACTIVATED for this host"}
        if held["ex"] is None or held["at"] != act["activated_at_ms"]:
            dcfg = load_demo_config(DEFAULT_DEMO_CONFIG_PATH, ExecutionMode.STRATEGY_DEMO)
            try:
                ex = build_fn(ctx, dcfg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"[:300]
                if msg != held["refused"]:
                    log.error("%s refused (fails closed, observation continues): %s", label, msg)
                held["refused"] = msg
                return {"mode": label, "refused": msg}
            log.info("%s active since %s: recovery %s", label, act["activated_at"], ex.recover())
            held.update(ex=ex, at=act["activated_at_ms"], refused=None)
        return cycle_fn(held["ex"], ctx, a, b, res)

    if not DEFAULT_DEMO_CONFIG_PATH.exists():
        return None
    return hook


@dataclass
class V51Runtime:
    """The V5.1 pipeline evaluated after V5 in the same runner (shared collector and bars)."""

    ctx: Any  # btc_swing.v51.forward.V51Context
    reports_dir: Path
    build: Callable[[Any, Any], Any]
    cycle: Callable[..., Any]
    run_cycle: Callable[..., Any]


async def _cycle_loop(
    ctx: ForwardContext, reports_dir: Path, stop: asyncio.Event, v51: V51Runtime | None = None
) -> None:
    sc = ctx.fcfg.schedule
    last_report_day = ""
    hook = _demo_hook(ctx)
    hook51 = _demo_hook(v51.ctx, v51.build, v51.cycle, "STRATEGY_DEMO (V5.1)") if v51 else None
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
            out = await asyncio.to_thread(run_cycle, ctx, None, True, hook)
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
        if v51 is not None:
            try:
                o51 = await asyncio.to_thread(v51.run_cycle, v51.ctx, None, hook51)
                log.info(
                    "V5.1 cycle %s: signals %s, paper closed %s, open %s, valid 1h %s, %.1fs",
                    o51["cycle_at"][11:19],
                    len(o51["new_signals"]),
                    o51["paper"]["closed_trades"],
                    o51["paper"]["open_position"] is not None,
                    o51["validity"]["current_1h_window_clean"],
                    o51["elapsed_s"],
                )
            except Exception as e:
                log.exception("V5.1 cycle failed: %s", e)
        now = datetime.now(UTC)
        if now.minute >= sc.daily_report_utc_minute and now.hour == 0:
            prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            if prev != last_report_day:
                try:
                    p = await asyncio.to_thread(write_day_report, ctx, prev, reports_dir)
                    log.info("daily report %s -> %s", prev, p)
                except Exception as e:
                    log.exception("daily report failed: %s", e)
                if v51 is not None:
                    try:
                        p = await asyncio.to_thread(
                            write_day_report, v51.ctx, prev, v51.reports_dir
                        )
                        log.info("V5.1 daily report %s -> %s", prev, p)
                    except Exception as e:
                        log.exception("V5.1 daily report failed: %s", e)
                last_report_day = prev


async def _main(
    ctx: ForwardContext,
    reports_dir: Path,
    duration_s: float | None,
    v51: V51Runtime | None = None,
) -> dict[str, Any]:
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
    cyc_task = asyncio.create_task(_cycle_loop(ctx, reports_dir, stop, v51))
    await stop.wait() if duration_s is None else asyncio.sleep(duration_s)
    col.stop()
    stop.set()
    stats = await col_task
    await cyc_task
    ctx.paths.run_pid.unlink(missing_ok=True)
    return stats.as_dict()


def run_forward(
    ctx: ForwardContext,
    reports_dir: Path,
    duration_s: float | None = None,
    v51: V51Runtime | None = None,
) -> dict[str, Any]:
    ctx.paths.logs.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(ctx.paths.logs / "forward_run.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)
    ok, why = runner_may_start(ctx.paths.root)
    if not ok:
        log.error("forward runner refused: %s", why)
        raise RuntimeError(f"forward runner refused: {why}")
    lock = RunnerLock(ctx.paths.root / "forward_run.lock")
    lock.acquire()  # a second runner on this data directory exits here (RunnerLockedError)
    log.info(
        "forward observation runner started (pid %s, frozen V5 %s; %s)",
        os.getpid(),
        ctx.cfg.config_hash[:12],
        why,
    )
    try:
        out = asyncio.run(_main(ctx, reports_dir, duration_s, v51))
    finally:
        lock.release()
    (
        ctx.paths.logs / f"collector_stats_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    ).write_text(json.dumps(out, indent=1, sort_keys=True))
    return out
