"""Append-only, hash-chained JSONL journal. Each record carries `prev_hash` and its own `hash`
(sha256 of the canonical record without `hash`), so any edit, deletion or reordering breaks the
chain at that record. Records are fsync'ed on write. A journal can forbid tags: the strategy
journal refuses EXECUTION_SMOKE records so smoke activity can never enter strategy performance."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def _canon(rec: dict[str, Any]) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)


class HashChainJournal:
    def __init__(self, path: Path, name: str, forbid_tags: tuple[str, ...] = ()) -> None:
        self.path = path
        self.name = name
        self.forbid_tags = forbid_tags
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._prev = GENESIS
        if self.path.exists():
            last = None
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
            if last is not None:
                r = json.loads(last)
                self._seq, self._prev = int(r["seq"]) + 1, str(r["hash"])

    def append(self, kind: str, data: dict[str, Any], tag: str | None = None) -> dict[str, Any]:
        if tag in self.forbid_tags:
            raise ValueError(f"journal {self.name} refuses records tagged {tag}")
        rec: dict[str, Any] = {
            "journal": self.name,
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "tag": tag,
            "data": _clean(data),
            "prev_hash": self._prev,
        }
        rec["hash"] = hashlib.sha256(_canon(rec).encode()).hexdigest()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(_canon(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._seq += 1
        self._prev = rec["hash"]
        return rec

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()
        ]


def verify_chain(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": True, "records": 0, "first_bad_seq": None, "reason": "no journal"}
    prev = GENESIS
    n = 0
    for i, line in enumerate(x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()):
        r = json.loads(line)
        h = r.pop("hash", None)
        if r.get("seq") != i:
            return {"ok": False, "records": i, "first_bad_seq": i, "reason": "sequence break"}
        if r.get("prev_hash") != prev:
            return {"ok": False, "records": i, "first_bad_seq": i, "reason": "prev_hash mismatch"}
        if hashlib.sha256(_canon(r).encode()).hexdigest() != h:
            return {"ok": False, "records": i, "first_bad_seq": i, "reason": "hash mismatch"}
        prev = str(h)
        n += 1
    return {"ok": True, "records": n, "first_bad_seq": None, "last_hash": prev}
