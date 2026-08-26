"""Deterministic hashing helpers.

Never use Python's built-in ``hash()`` for anything that must be stable
across processes or PYTHONHASHSEED values (str/bytes hashing is randomized
by default). Use these instead.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Stable JSON encoding: sorted keys, no extra whitespace, unicode kept as-is."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_str(s: str) -> str:
    """Return ``sha256:<hex>`` of the UTF-8 encoding of ``s``."""
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def sha256_obj(obj: Any) -> str:
    """Convenience: canonical_json then sha256_str."""
    return sha256_str(canonical_json(obj))
