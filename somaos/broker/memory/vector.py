"""Graded vectors: the knowledge the engine actually decides on (N-02).

Two things live here, and they are deliberately in the same module because
they have to agree on a representation:

1. ``embed`` -- a deterministic, seeded stand-in for a real embedding
   model (N-13). Phase 0b is testing whether the *structure* works, not
   whether an embedding model is good, so a real model would only add an
   uncontrolled variable and a network dependency. Everything downstream
   goes through ``embed``/``similarity``, so swapping in a real encoder is
   a one-module change.

2. The dilution grades D0-D4 (N-04). A grade is not a compression setting;
   it is a statement about *which questions the vector can still answer*.
   The ladder is ordered by what it destroys, not by how many bytes it
   saves -- see plans/03_MEMORY_ARCHITECTURE.md section 3.2, and
   somaos/bench/experiments/quantization_fidelity.py for the measurements
   that fixed the ordering:

       D0 float32  instance ranking + category
       D1 int8     instance ranking + category   (recall@10 0.98-0.996)
       D2 binary   category only                 (recall@10 0.21-0.74,
                                                  right cluster 0.79-1.00)
       D3 merged   handled by the tree, not here: the node's vector is
                   folded into its parent's centroid and the address
                   forwards (see address.AliasTable)
       D4 counter  handled by the tree: nothing survives but a tally on an
                   ancestor

   So this module encodes D0-D2 (a vector still exists) and treats D3/D4
   as structural states that carry no vector of their own.

There is no dimensionality-reduction rung. Sign bits are SimHash: the bit
count *is* the sample size of the angle estimate, so cutting dimensions
degrades the estimate faster than it saves bytes. Measured, not assumed.
"""
from __future__ import annotations

import hashlib
from enum import IntEnum

import numpy as np

from somaos.util.rng import stream_seed

DEFAULT_DIM = 256


class Grade(IntEnum):
    """Dilution rungs. Ordering is meaningful: a node may only move up."""

    D0_EXACT = 0
    D1_INT8 = 1
    D2_BINARY = 2
    D3_MERGED = 3
    D4_COUNTER = 4


GRADE_ORDER = tuple(Grade)

#: Grades that still carry a vector of their own.
VECTOR_GRADES = (Grade.D0_EXACT, Grade.D1_INT8, Grade.D2_BINARY)

_DTYPE_FOR = {
    Grade.D0_EXACT: np.float32,
    Grade.D1_INT8: np.int8,
    Grade.D2_BINARY: np.int8,  # stored as +-1; packed size accounted separately
}

#: Bits per dimension actually needed to store each grade.
_BITS_PER_DIM = {
    Grade.D0_EXACT: 32,
    Grade.D1_INT8: 8,
    Grade.D2_BINARY: 1,
}


class GradeError(ValueError):
    """Raised on an illegal grade transition or an operation on a gradeless node."""


def _unit(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    if norm == 0.0:
        return v.astype(np.float32, copy=True)
    return (v / norm).astype(np.float32)


def embed(keys: tuple[str, ...], *, dim: int = DEFAULT_DIM, seed_root: str = "soma") -> np.ndarray:
    """Deterministic stand-in embedding for a set of symbolic keys (N-13).

    Each key contributes a fixed pseudo-random unit direction, and the
    result is their normalised sum. This gives the two properties the
    structure actually depends on: items sharing keys are close, and the
    mapping is stable across processes (it goes through sha256, never
    Python's randomised ``hash()``).

    It is emphatically not a semantic encoder -- it knows nothing about
    meaning beyond exact key overlap. Phase 0.5 swaps this for a real one.
    """
    if dim <= 0:
        raise ValueError(f"dim must be positive, got {dim}")
    acc = np.zeros(dim, dtype=np.float64)
    for key in sorted(set(keys)):
        seed = stream_seed(seed_root, f"embed:{key}")
        acc += np.random.default_rng(seed).standard_normal(dim)
    if not len(keys):
        return np.zeros(dim, dtype=np.float32)
    return _unit(acc)


def cue_vector(
    topics: tuple[str, ...],
    entities: tuple[str, ...],
    *,
    dim: int = DEFAULT_DIM,
    seed_root: str = "soma",
) -> np.ndarray:
    """Build the query-side vector that a walk starts from (RecallMachine CUE)."""
    return embed(tuple(topics) + tuple(entities), dim=dim, seed_root=seed_root)


def encode(vec: np.ndarray, grade: Grade) -> np.ndarray:
    """Re-encode a vector at ``grade``. Only D0-D2 carry vectors."""
    if grade not in VECTOR_GRADES:
        raise GradeError(f"{grade.name} carries no vector of its own; the tree handles it")
    v = np.asarray(vec, dtype=np.float32)
    if grade is Grade.D0_EXACT:
        return v.astype(np.float32, copy=True)
    if grade is Grade.D1_INT8:
        scale = float(np.abs(v).max())
        if scale == 0.0:
            return np.zeros_like(v, dtype=np.int8)
        return np.round(v / scale * 127.0).astype(np.int8)
    # D2: sign bits. np.sign leaves zeros at 0, which would be a third
    # symbol in what must be a two-symbol code, so zeros go to +1.
    signs = np.sign(v)
    signs[signs == 0] = 1.0
    return signs.astype(np.int8)


def _as_float(vec: np.ndarray) -> np.ndarray:
    return np.asarray(vec, dtype=np.float32)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity, computed in float regardless of storage grade.

    Comparing an int8 or sign-bit vector this way is exactly the SimHash
    estimator when both sides are sign bits: agreement fraction maps
    monotonically to the angle, so ranking by cosine over +-1 vectors and
    ranking by Hamming distance give the same order.
    """
    fa, fb = _as_float(a), _as_float(b)
    na, nb = float(np.linalg.norm(fa)), float(np.linalg.norm(fb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(fa @ fb / (na * nb))


def fidelity_of(original: np.ndarray, current: np.ndarray) -> float:
    """How much of the original direction survives, in [0, 1] (N-04).

    This is the number that makes "dilution" measurable rather than a
    figure of speech. Clamped at 0 because a negative cosine means the
    concept is gone, not inverted-but-present.
    """
    return max(0.0, similarity(original, current))


def nbytes(vec: np.ndarray, grade: Grade) -> int:
    """Storage cost of one vector at ``grade``, in bytes.

    Sign bits are counted at 1 bit per dimension even though numpy holds
    them in an int8 array: the budget must reflect what a real store would
    write, not what this process happens to allocate. Rounded up to whole
    bytes.
    """
    if grade not in VECTOR_GRADES:
        return 0
    dim = int(np.asarray(vec).size)
    return (dim * _BITS_PER_DIM[grade] + 7) // 8


def vector_digest(vec: np.ndarray, grade: Grade) -> str:
    """Stable hex digest of a vector as stored at ``grade``.

    Used by content addressing. Encoding to the grade first means two
    nodes that have been diluted to the same representation share an
    address, which is the dedupe property N-07 asks for.
    """
    if grade not in VECTOR_GRADES:
        return hashlib.sha256(f"gradeless:{int(grade)}".encode("utf-8")).hexdigest()
    encoded = encode(vec, grade) if np.asarray(vec).dtype != _DTYPE_FOR[grade] else vec
    payload = np.ascontiguousarray(encoded).tobytes()
    return hashlib.sha256(payload).hexdigest()
