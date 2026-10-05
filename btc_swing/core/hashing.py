"""Stable hashing for configs, payloads and result rows (generic utility, no strategy logic)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, set | frozenset):
        return sorted(o)
    if isinstance(o, bytes):
        return o.hex()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    raise TypeError(f"not hashable: {type(o)}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, allow_nan=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def round_floats(obj: Any, ndigits: int = 8) -> Any:
    """Round floats recursively so hashes are stable across platforms."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [round_floats(v, ndigits) for v in obj]
    return obj
