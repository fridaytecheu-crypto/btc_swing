"""Immutable raw archive store (file based).

Every downloaded archive file is kept byte-for-byte under `<data_dir>/btc/raw/<provider>/<key>`
with its sha256 recorded in an append-only JSONL manifest. Re-putting an identical file is a
no-op; putting a *different* file at an existing key raises — raw data is never overwritten.
File based on purpose: archive files are large binary blobs, not JSON payloads, so no database
is needed for immutability.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from btc_swing.providers.base import ArchiveFetch, ArchiveMeta


class ImmutabilityError(RuntimeError):
    pass


class ArchiveStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "btc" / "raw"
        self.manifest_path = self.root / "manifest.jsonl"

    def path_for(self, provider: str, key: str) -> Path:
        return self.root / provider / key

    def put(self, fetch: ArchiveFetch) -> Path:
        meta = fetch.meta
        path = self.path_for(meta.provider, meta.key)
        if path.exists():
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing != meta.sha256:
                raise ImmutabilityError(
                    f"{path} exists with sha256 {existing}; refusing to overwrite with {meta.sha256}"
                )
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(fetch.raw_bytes)
        tmp.replace(path)
        self._append_manifest(meta)
        return path

    def _append_manifest(self, meta: ArchiveMeta) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = asdict(meta)
        row["dataset"] = meta.dataset.value
        row["timeframe"] = meta.timeframe.value if meta.timeframe else None
        row["fetched_at"] = meta.fetched_at.isoformat()
        with self.manifest_path.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.manifest_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def known_sha(self, provider: str, key: str) -> str | None:
        for e in reversed(self.entries()):
            if e["provider"] == provider and e["key"] == key:
                return str(e["sha256"])
        return None

    def read(self, provider: str, key: str) -> bytes:
        path = self.path_for(provider, key)
        data = path.read_bytes()
        known = self.known_sha(provider, key)
        if known is not None and hashlib.sha256(data).hexdigest() != known:
            raise ImmutabilityError(f"{path} content hash mismatch against manifest")
        return data


def utcnow_iso(dt: datetime) -> str:
    return dt.isoformat()
