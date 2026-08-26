"""Per-stream deterministic RNG seeding (see plans/01_DECISIONS.md D-08).

Every source of randomness in this codebase must derive its seed from a
``(seed_root, stream_name)`` pair via this module. This lets a single
top-level ``seed_root`` fan out into many independent, reproducible
random streams (e.g. "obs", "query", "topics") without them interfering
with each other, and without ever touching the global ``random`` module
or numpy's global RNG state.
"""
from __future__ import annotations

import hashlib
import random


def stream_seed(seed_root: str, stream_name: str) -> int:
    """Derive a 64-bit integer seed for a named stream.

    Deterministic across processes and PYTHONHASHSEED values because it
    goes through sha256, not Python's built-in hash().
    """
    key = f"{seed_root}:{stream_name}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big")


def make_rng(seed_root: str, stream_name: str) -> random.Random:
    """Return a fresh, independently-seeded random.Random for this stream."""
    return random.Random(stream_seed(seed_root, stream_name))
