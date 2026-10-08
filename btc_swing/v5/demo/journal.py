"""Append-only, hash-chained JSONL journal: every line carries `seq` and the sha256 of the previous
line, so an edited, removed or reordered record is detectable (`verify`)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class HashChainJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prev, self.n = GENESIS, 0
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    self.prev = hashlib.sha256(line.encode()).hexdigest()
                    self.n += 1

    def append(self, rec: dict[str, Any]) -> str:
        body = {
            "seq": self.n,
            "prev_hash": self.prev,
            "journaled_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            **rec,
        }
        line = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        with self.path.open("a") as fh:
            fh.write(line + "\n")
        self.prev = hashlib.sha256(line.encode()).hexdigest()
        self.n += 1
        return self.prev

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]

    @staticmethod
    def verify(path: Path) -> tuple[bool, int, str]:
        prev, n = GENESIS, 0
        if not path.exists():
            return True, 0, "empty"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_hash") != prev or rec.get("seq") != n:
                return False, n, f"chain broken at seq {n}"
            prev = hashlib.sha256(line.encode()).hexdigest()
            n += 1
        return True, n, prev
