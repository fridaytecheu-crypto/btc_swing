"""Markdown rendering of a full-history report dict (pure; writes nothing)."""

from __future__ import annotations

import json
from typing import Any

NA = "N/A"


def _v(x: Any, nd: int = 3) -> str:
    if x is None:
        return NA
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x != x:
            return NA
        if x == float("inf"):
            return "inf"
        return f"{x:.{nd}f}"
    return json.dumps(x, default=str)


def _kv_table(title: str, d: Any, nd: int = 3) -> list[str]:
    out = [f"**{title}**", ""]
    if not isinstance(d, dict):
        out.append(f"{_v(d)}")
        out.append("")
        return out
    out += ["| key | value |", "|---|---|"]
    for k, v in d.items():
        out.append(f"| {k} | {_v(v, nd)} |")
    out.append("")
    return out


def _fs_table(title: str, d: Any) -> list[str]:
    if not isinstance(d, dict):
        return [f"**{title}**: {_v(d)}", ""]
    out = [f"**{title}**", "", "| family | LONG | SHORT | total |", "|---|---|---|---|"]
    for f in ("A", "B", "C"):
        out.append(f"| {f} | {d.get(f'{f}-LONG', 0)} | {d.get(f'{f}-SHORT', 0)} | {d.get(f, 0)} |")
    out.append(
        f"| all | {d.get('LONG', 0)} | {d.get('SHORT', 0)} | {d.get('LONG', 0) + d.get('SHORT', 0)} |"
    )
    out.append("")
    return out


def render_full_history(r: dict[str, Any]) -> str:
    out = [f"# {r['label']} — forward full history (read-only)\n"]
    out.append(
        f"Generated {r['generated_utc']} · generation `{r['generation']}` · root `{r['root']}`"
        f"{' (VERIFICATION root)' if r.get('verification_root') else ''} · execution modes {r['execution_modes']} · "
        f"freeze strategy `{str(r['freeze']['strategy_hash'])[:12]}` (config `{str(r['config_strategy_hash'])[:12]}`) · "
        f"observation start {r['observation_start']} · last bar/tick {r['last_bar_or_tick']} · duration {_v(r['duration_days'], 2)} days.\n"
    )
    out.append(
        "Source of truth: the journals listed at the end. This report reads them and writes nothing else; "
        "N/A marks fields the generation does not journal.\n"
    )
    out.append("## 1. Funnel\n")
    out += _kv_table("candidate -> armed -> trigger -> executed", r["funnel"], 0)
    out += _kv_table("rates per observed day", r["rates_per_day"], 2)
    out += _kv_table("state counters (as persisted by the runner)", r["state_counters"], 0)
    out.append("## 2. Families and sides\n")
    for k in ("armed", "triggered", "executed", "blocked", "near_misses", "shadow_closed"):
        out += _fs_table(k, r["by_family_side"].get(k))
    out.append("## 3. Blockers and failed conditions\n")
    out += _kv_table("decision blockers (BLOCKED signals)", r["blockers"] or "none", 0)
    out += _kv_table("failed / invalidated episode conditions", r["failed_conditions"], 0)
    out.append("## 4. Score distributions\n")
    sc = r["scores"]
    for k in ("candidates", "armed", "executed", "near_misses"):
        out += _kv_table(k, sc.get(k), 2)
    if isinstance(sc.get("candidates_by_side"), dict):
        for s, d in sc["candidates_by_side"].items():
            out += _kv_table(f"candidates {s}", d, 2)
    out.append("## 5. Shadow trades (PnL / R)\n")
    t = r["shadow_trades"]
    if t.get("n", 0) == 0:
        out.append("No closed shadow trade yet.\n")
    else:
        out += _kv_table(
            "closed shadow trades",
            {
                k: v
                for k, v in t.items()
                if k not in ("by_family_side", "net_r_by_family", "net_r_by_side", "exit_reasons")
            },
        )
        out += _kv_table("exit reasons", t["exit_reasons"], 0)
        out += _kv_table("net R by family", t["net_r_by_family"])
        out += _kv_table("net R by side", t["net_r_by_side"])
        out += _fs_table("closed trades by family / side", t["by_family_side"])
    out.append("## 6. Outcomes (forward returns after candidates / episodes / signals)\n")
    o = r["outcomes"]
    out.append(f"records: {o.get('n', 0)}\n")
    if isinstance(o.get("mean_return_bps_by_kind_horizon"), dict):
        out += ["| kind@horizon | n | mean return bps |", "|---|---|---|"]
        for k, d in o["mean_return_bps_by_kind_horizon"].items():
            out.append(f"| {k} | {d['n']} | {_v(d['mean_bps'], 2)} |")
        out.append("")
    out.append("## 7. Demo execution\n")
    d = r["demo"]
    if not isinstance(d, dict):
        out.append(f"{d} — this generation is SHADOW ONLY (no exchange client).\n")
    else:
        out += _kv_table("demo journal", {"records": d["journal_records"], **d["journal_kinds"]}, 0)
        out.append(
            "activation events: "
            + (
                json.dumps(d["activation_events"], default=str)
                if d["activation_events"]
                else "none"
            )
            + "\n"
        )
        dt = d["trades"]
        if dt.get("n", 0) == 0:
            out.append("No closed demo trade yet.\n")
        else:
            out += _kv_table(
                "closed demo trades",
                {
                    k: v
                    for k, v in dt.items()
                    if k
                    not in ("by_family_side", "net_r_by_family", "net_r_by_side", "exit_reasons")
                },
            )
            out += _kv_table("demo exit reasons", dt["exit_reasons"], 0)
    out.append("## 8. Daily\n")
    if r["daily"]:
        cols = sorted({c for v in r["daily"].values() for c in v})
        out.append("| day | " + " | ".join(cols) + " |")
        out.append("|---|" + "---|" * len(cols))
        for day, v in r["daily"].items():
            out.append(f"| {day} | " + " | ".join(str(v.get(c, 0)) for c in cols) + " |")
        out.append("")
    else:
        out.append("no dated records yet\n")
    out.append("## 9. Journal inventory\n")
    out += _kv_table("files", r["journal_files"])
    out += _kv_table("episode kinds", r["episode_kinds"], 0)
    out += _kv_table("other signal-journal kinds", r["other_signal_journal_kinds"] or "none", 0)
    out.append(f"malformed lines skipped: {r['malformed_lines']}\n")
    if r.get("notes"):
        out += _kv_table("notes", r["notes"])
    return "\n".join(out) + "\n"
