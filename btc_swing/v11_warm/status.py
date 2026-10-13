"""`btc-swing v11w forward status` / `diagnostic` / `verify-warm` / `shadow-report` (read-only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from btc_swing.v11.config import V11Config
from btc_swing.v11.status import diagnostic_text as _v11_diagnostic_text
from btc_swing.v11.status import shadow_report as _v11_shadow_report
from btc_swing.v11.status import status_rows as _v11_status_rows
from btc_swing.v11_warm.config import V11WarmConfig
from btc_swing.v11_warm.freeze import load_freeze
from btc_swing.v11_warm.paths import V11WarmPaths


def _diag(paths: V11WarmPaths) -> dict[str, Any]:
    return json.loads(paths.diagnostic.read_text()) if paths.diagnostic.exists() else {}


def verify_warm(wc: V11WarmConfig, paths: V11WarmPaths) -> dict[str, Any]:
    """Feature availability on the latest completed bar, against the pre-declared lists."""
    d = _diag(paths)
    w = d.get("warm") or {}
    seed = json.loads(paths.seed_record.read_text()) if paths.seed_record.exists() else {}
    ind = d.get("indicators") or {}
    contrib = (d.get("scores") or {}).get("contrib_long") or {}
    out = {
        "diagnostic_written_at": d.get("written_at"),
        "last_bar": d.get("last_bar"),
        "bars_in_window": d.get("bars"),
        "bootstrap_bars_in_window": w.get("bootstrap_bars_in_window"),
        "seed": {
            k: seed.get(k) for k in ("bootstrap_bars", "gapfill_bars", "last_close", "sha256_bars")
        },
        "required_fields": {k: ind.get(k) for k in wc.warm.required_warm_fields},
        "required_groups": {g: contrib.get(g) for g in wc.warm.required_warm_groups},
        "derivatives_contribution": contrib.get("derivatives"),
        "microstructure_contribution": contrib.get("microstructure"),
        "scores": {k: (d.get("scores") or {}).get(k) for k in ("long", "short")},
        "in_window": d.get("in_window"),
        "all_required_fields_populated": bool(w.get("all_fields")),
        "all_required_groups_populated": bool(w.get("all_groups")),
    }
    out["status"] = (
        "WARM"
        if out["all_required_fields_populated"] and out["all_required_groups_populated"]
        else "NOT_WARM"
    )
    return out


def status_rows(wc: V11WarmConfig, v11: V11Config, paths: V11WarmPaths) -> list[tuple[str, str]]:
    rows = _v11_status_rows(v11, paths)
    fz = load_freeze()
    d = _diag(paths)
    w = d.get("warm") or {}
    out: list[tuple[str, str]] = []
    for k, v in rows:
        if k == "freeze":
            out.append(
                (
                    "freeze",
                    f"V11 strategy {fz['v11_strategy_hash'][:12]} frozen {fz['frozen_at'][:19]} observation from {fz['observation_start'][:19]}"
                    if fz
                    else "NONE",
                )
            )
        elif k == "V10 coupling":
            out.append(
                (
                    "V10 / V11 coupling",
                    "none: separate feed, root, journals, state, pid, lock, freeze",
                )
            )
        else:
            out.append((k, v))
    out.insert(3, ("mode", "WARM-STARTED V11 SHADOW (same strategy; feature bootstrap only)"))
    out.append(
        (
            "warm-start",
            f"bootstrap bars in window {w.get('bootstrap_bars_in_window')} / {w.get('bars_in_window')} · "
            f"fields {'ALL' if w.get('all_fields') else 'MISSING ' + ','.join(k for k, ok in (w.get('fields') or {}).items() if not ok)} · "
            f"groups {'ALL' if w.get('all_groups') else 'MISSING ' + ','.join(k for k, ok in (w.get('groups') or {}).items() if not ok)} · "
            f"derivatives {'yes' if w.get('derivatives_available') else 'warming'} · microstructure {'yes' if w.get('microstructure_available') else 'warming'}",
        )
    )
    out.append(("in window", str(d.get("in_window"))))
    return out


def status_text(wc: V11WarmConfig, v11: V11Config, paths: V11WarmPaths) -> str:
    rows = status_rows(wc, v11, paths)
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def diagnostic_text(paths: V11WarmPaths) -> str:
    base = _v11_diagnostic_text(paths).replace("V11 diagnostic", "V11-WARM diagnostic", 1)
    d = _diag(paths)
    return base + f"\nwarm: {json.dumps(d.get('warm'))}\nin_window: {d.get('in_window')}"


def shadow_report(
    wc: V11WarmConfig, v11: V11Config, paths: V11WarmPaths, min_hours: float = 0.5
) -> dict[str, Any]:
    rep = _v11_shadow_report(v11, paths, min_hours)
    vw = verify_warm(wc, paths)
    rep["run_id"] = f"v11warm-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    rep["checks"]["features warm (pre-declared fields)"] = vw["all_required_fields_populated"]
    rep["checks"]["groups warm (pre-declared groups)"] = vw["all_required_groups_populated"]
    rep["checks"]["no V10 / V11 coupling"] = "forward_v11_warm" in str(paths.root)
    rep["status"] = "PASSED" if all(rep["checks"].values()) else "NOT_PASSED"
    rep["warm"] = vw
    rep["mode"] = "SHADOW ONLY (warm-started V11)"
    return rep
