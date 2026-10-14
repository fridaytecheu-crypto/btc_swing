"""Replay the f885f85 V11-LITE engine and the cleaned engine (bookkeeping v11lw-book-2) on identical
historical frames (consumed TRAIN / VALIDATION windows, loads capped at 2025-07-01) and compare
every decision and trade field. Equality only; no performance is reported.

Usage: BTC_DATA_DIR=./data uv run python scripts/v11lw_equivalence_replay.py <out.json>
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from btc_swing.v11.config import load_v11_config
from btc_swing.v11_2.study import build_window
from btc_swing.v11_lite_warm.engine import V11LiteEngine
from tests.v11_lite_warm.legacy_engine_f885f85 import V11LiteEngine as Legacy

DEC = (
    "signal_id", "decision_ms", "family", "side", "side_sign", "action", "blockers", "entry_ref",
    "structural_stop", "stop", "stop_pct", "stop_note", "tp1", "tp2", "qty", "leverage", "notional",
    "margin", "risk_usdt", "score", "score_armed", "armed_ms", "score_long", "score_short",
)  # fmt: skip


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def main(out_path: str) -> int:
    cfg = load_v11_config("config/btc_swing_v11.yaml")
    out: dict[str, Any] = {}
    windows = (("train", "2022-01-01", "2024-07-01"), ("validation", "2024-07-01", "2025-07-01"))
    for name, a, b in windows:
        t0 = time.time()
        w = build_window(cfg, Path("data"), name, a, b)
        old = Legacy(cfg, w.fr, w.sc, funding_t=w.fund_t, funding_r=w.fund_r)
        new = V11LiteEngine(cfg, w.fr, w.sc, funding_t=w.fund_t, funding_r=w.fund_r)
        old.run(w.start_ms, w.end_ms)
        new.run(w.start_ms, w.end_ms)
        pairs_d = list(zip(old.decisions, new.decisions, strict=False))
        pairs_t = [(x.as_dict(), y.as_dict()) for x, y in zip(old.trades, new.trades, strict=False)]
        dmis = sum(1 for x, y in pairs_d for k in DEC if not _same(x.get(k), y.get(k)))
        tmis = sum(1 for x, y in pairs_t for k in x if k != "decision" and not _same(x[k], y[k]))
        out[name] = {
            "decisions_old": len(old.decisions),
            "decisions_new": len(new.decisions),
            "executed_old": old.counters["executed"],
            "executed_new": new.counters["executed"],
            "blocked_old": old.counters["blocked"],
            "blocked_new": new.counters["blocked"],
            "trades_old": len(old.trades),
            "trades_new": len(new.trades),
            "decision_field_mismatches": dmis,
            "trade_field_mismatches": tmis,
            "selected_template_equals_old_family": all(
                y["selected_template"] == x["family"] for x, y in pairs_d
            ),
            "candidates_equal": old.counters["candidates"] == new.counters["candidates"]
            and old._cand_last == new._cand_last,
            "candidates": new.counters["candidates"],
            "old_per_family_arm_starts": old.counters["armed"],
            "new_side_arm_episodes": new.counters["arm_episodes"],
            "seconds": round(time.time() - t0),
        }
        print(name, json.dumps(out[name]), flush=True)
    ok = all(
        v["decisions_old"] == v["decisions_new"]
        and v["trades_old"] == v["trades_new"]
        and v["decision_field_mismatches"] == 0
        and v["trade_field_mismatches"] == 0
        and v["selected_template_equals_old_family"]
        and v["candidates_equal"]
        for v in out.values()
    )
    result = "EQUIVALENT" if ok else "MISMATCH"
    print(result)
    Path(out_path).write_text(json.dumps({"result": result, "windows": out}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
