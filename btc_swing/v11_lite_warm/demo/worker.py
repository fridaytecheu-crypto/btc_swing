"""Demo threads, decoupled from the shadow engine thread.

`snapshot_item` is the ONLY thing the shadow calls (through `V11LiteWarmLive.demo_sink`): it copies the
completed bar's column tails and the shadow decision record and enqueues them; it never blocks, never
calls an exchange and never returns anything to the shadow. `DemoWorker` drains the queue on its own
thread (Demo HTTP latency cannot delay a shadow tick). `DemoSupervisor` attaches a worker only while the
owner's host-bound activation is effective, and detaches it after deactivation once flat."""

from __future__ import annotations

import contextlib
import copy
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from btc_swing.v11_lite_warm.demo.executor import BarCtx

log = logging.getLogger(__name__)


def v10_root_of(lite_root: Path) -> Path:
    """V10's forward root next to the LITE root (read-only conflict gate input)."""
    return lite_root.parent / "forward_v10"


def snapshot_item(
    cols: dict[str, Any],
    i: int,
    j: int,
    t_close: int,
    score_long: float,
    score_short: float,
    rec: dict[str, Any] | None,
) -> dict[str, Any]:
    """`cols` are the column tails the shadow copied (row j = frame row i); copied once more here."""
    return {
        "bar": BarCtx(
            i=i,
            j=j,
            t_close=t_close,
            x={k: np.array(v, dtype=float, copy=True) for k, v in cols.items()},
            score_long=float(score_long),
            score_short=float(score_short),
        ),
        "rec": copy.deepcopy(rec) if rec is not None else None,
        "enqueued_s": time.time(),
    }


class DemoWorker(threading.Thread):
    def __init__(self, executor: Any, poll_s: float = 10.0) -> None:
        super().__init__(name="v11lw-demo", daemon=True)
        self.ex = executor
        self.q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10_000)
        self.poll_s = poll_s
        self.stop_ev = threading.Event()
        self.errors = 0

    # the shadow-side sink: copy + enqueue only
    def sink(
        self,
        cols: dict[str, Any],
        i: int,
        j: int,
        t_close: int,
        score_long: float,
        score_short: float,
        rec: dict[str, Any] | None,
    ) -> None:
        try:
            self.q.put_nowait(snapshot_item(cols, i, j, t_close, score_long, score_short, rec))
        except queue.Full:
            self.errors += 1

    def _error(self, stage: str, e: Exception) -> None:
        self.errors += 1
        log.exception("V11-LITE Demo %s error", stage)
        try:
            self.ex.alert(
                "DEMO_" + stage.upper() + "_EXCEPTION",
                {"error": f"{type(e).__name__}: {e}"[:200]},
                reconcile=bool(self.ex.has_position()),
            )
            self.ex.save()
        except Exception:
            log.exception("V11-LITE Demo error journaling failed")

    def process(self, item: dict[str, Any]) -> None:
        """One shadow bar: manage the open Demo position on it, then send the shadow decision (if the
        shadow EXECUTED one on this bar). Synchronous; also used by tests."""
        bar: BarCtx = item["bar"]
        try:
            self.ex.manage(bar.t_close, bar)
        except Exception as e:
            self._error("manage", e)
        rec = item.get("rec")
        if rec is not None and rec.get("action") == "EXECUTED":
            try:
                self.ex.on_shadow_decision(rec, bar)
            except Exception as e:
                self._error("entry", e)

    def poll(self) -> None:
        if self.ex.has_position():
            try:
                self.ex.manage(int(time.time() * 1000), None)
            except Exception as e:
                self._error("poll", e)

    def run(self) -> None:
        last_poll = time.monotonic()
        while not self.stop_ev.is_set():
            with contextlib.suppress(queue.Empty):
                self.process(self.q.get(timeout=0.5))
            if time.monotonic() - last_poll >= self.poll_s:
                last_poll = time.monotonic()
                self.poll()


class DemoSupervisor(threading.Thread):
    """Attach / detach the Demo worker from the owner's activation journal. Any failure is journaled
    (once per distinct message) and leaves the shadow untouched."""

    def __init__(
        self,
        lite_root: Path,
        set_sink: Callable[[Callable[..., None] | None], None],
        build: Callable[[dict[str, Any]], Any],
        check_s: float = 30.0,
    ) -> None:
        super().__init__(name="v11lw-demo-supervisor", daemon=True)
        self.lite_root = lite_root
        self.set_sink = set_sink
        self.build = build
        self.check_s = check_s
        self.worker: DemoWorker | None = None
        self.stop_ev = threading.Event()
        self._last_error: str | None = None

    def _journal(self, kind: str, data: dict[str, Any]) -> None:
        from btc_swing.v11_lite_warm.demo.activation import demo_journal
        from btc_swing.v11_lite_warm.demo.config import TAG

        demo_journal(self.lite_root).append(kind, data, TAG)

    def check(self) -> str:
        from btc_swing.v11_lite_warm.demo.activation import effective_activation

        act = effective_activation(self.lite_root)
        w = self.worker
        if act is not None and w is None:
            try:
                ex = self.build(act)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"[:200]
                if msg != self._last_error:
                    self._last_error = msg
                    self._journal("DEMO_ATTACH_FAILED", {"error": msg})
                return "attach_failed"
            self._last_error = None
            w = DemoWorker(ex)
            w.start()
            self.worker = w
            self.set_sink(w.sink)
            self._journal(
                "DEMO_EXECUTOR_ATTACHED",
                {"activated_at": act.get("activated_at"), "account_mode": act.get("account_mode")},
            )
            return "attached"
        if act is None and w is not None:
            if w.ex.has_position():
                return "deactivated_position_open"  # the frozen exits close it first
            self.set_sink(None)
            w.stop_ev.set()
            w.join(timeout=30)
            self.worker = None
            self._journal("DEMO_EXECUTOR_DETACHED", {"reason": "deactivated"})
            return "detached"
        return "unchanged"

    def run(self) -> None:
        while not self.stop_ev.is_set():
            try:
                self.check()
            except Exception:
                log.exception("V11-LITE Demo supervisor error")
            self.stop_ev.wait(self.check_s)
        if self.worker is not None:
            self.set_sink(None)
            self.worker.stop_ev.set()
            self.worker.join(timeout=30)


def make_supervisor(
    lc: Any, v11: Any, lite_root: Path, set_sink: Callable[[Callable[..., None] | None], None]
) -> DemoSupervisor:
    """The runner's Demo supervisor (observation root only). Each attach re-checks the Demo config
    against the Demo execution manifest and the V11-LITE strategy hashes."""

    def build(act: dict[str, Any]) -> Any:
        from btc_swing.v11_lite_warm.demo.config import load_lite_demo_config
        from btc_swing.v11_lite_warm.demo.freeze import check_demo_freeze
        from btc_swing.v11_lite_warm.demo.gates import build_executor

        dc = load_lite_demo_config()
        check_demo_freeze(lc, v11, dc)
        return build_executor(lite_root, v10_root_of(lite_root), v11, dc, act)

    return DemoSupervisor(lite_root, set_sink, build)
