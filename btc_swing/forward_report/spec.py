"""Generation descriptors and the read-only readers. A spec names the files; it carries no behaviour
that could write. Journal files are hash-chained JSONL (`{"kind", "data", "seq", "hash", ...}`);
plain JSONL (demo trades) is read line by line. Missing files read as empty."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GenerationSpec:
    name: str  # e.g. "v10", "v11", "v11w"
    label: str  # e.g. "V10 LIVE MICROSTRUCTURE SNIPER"
    root: Path  # forward root (observation or verification)
    reports_dir: Path  # reports/<generation>/ (the ONLY writable location)
    state: Path
    freeze: Path
    strategy_hash: str | None  # from the generation's config object (read-only)
    journals: dict[str, Path]  # name -> hash-chained jsonl (episodes, signals, ...)
    demo_journal: Path | None = None  # V10 only: hash-chained STRATEGY_DEMO journal
    demo_trades: Path | None = None  # V10 only: plain jsonl
    demo_state: Path | None = None  # V10 only
    execution_modes: tuple[str, ...] = ("SHADOW",)
    verification: bool = False
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def has_demo(self) -> bool:
        return self.demo_journal is not None


def read_chain(path: Path | None) -> list[dict[str, Any]]:
    """Records of a hash-chained journal (read-only; malformed lines are skipped and counted)."""
    if path is None or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"kind": "__MALFORMED__", "data": {}})
    return out


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"__malformed__": True})
    return out


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def iso(ms: float | int | None) -> str:
    return (
        "N/A"
        if ms is None
        else datetime.fromtimestamp(float(ms) / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    )
