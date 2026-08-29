"""Where a real embedding model plugs in (N-13).

Phase 0b measured a structure, not an encoder, so similarity has been
computed by a deterministic hash: two memories are close exactly when
they share keys. That was the right call for isolating the structural
claim -- nothing in the results can be attributed to a good encoder,
because there wasn't one -- but it is not what an application wants.

This module is the seam. ``Embedder`` is the whole contract; the tree,
the dilution ladder and the walk go through it and know nothing else.

**Swapping this changes the geometry, and the geometry is what the
thresholds were tuned on.** With hashes, "close" means the same keys.
With a real encoder, "close" means related meaning, so unrelated
memories that happen to share a word move apart and related memories
that share no word move together. The constants that decide when a
group crystallises into a habit (ConsolidationMachine.COHERENCE), when
the tree splits (MAX_CHILDREN) and how much retrieval strength counts
against similarity (STRENGTH_WEIGHT) all sit on that geometry and will
need re-tuning. Expect the numbers to move; do not report the old ones
against a new encoder.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from somaos.broker.memory.vector import DEFAULT_DIM, embed


@runtime_checkable
class Embedder(Protocol):
    """Turns symbols into a direction the engine can compare.

    Two requirements, both load-bearing:

    * **unit length.** Similarity is a dot product and fidelity is an
      angle, so a vector whose length carries information would make
      "how similar" and "how much" the same number, and dilution would
      silently change relevance.
    * **stable across processes.** Addresses are content-derived. If the
      same input embedded differently on a second run, a saved store
      would stop resolving its own addresses.
    """

    @property
    def dim(self) -> int:
        """Dimensions per vector. Fixes the byte cost of every rung."""

    def encode(self, keys: tuple[str, ...]) -> np.ndarray:
        """Embed one bag of symbols as a unit vector of length ``dim``."""

    def encode_batch(self, batches: tuple[tuple[str, ...], ...]) -> np.ndarray:
        """Embed many at once. A network-backed encoder should override
        this -- one request beats N, and consolidation embeds in bulk."""


class HashEmbedder:
    """The Phase 0b encoder: deterministic, offline, meaning-blind.

    Kept as the default and as the control. Every published number was
    measured on this, so it stays available for reproducing them, and it
    is what runs when no model is reachable.
    """

    def __init__(self, *, dim: int = DEFAULT_DIM, seed_root: str = "soma") -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = int(dim)
        self.seed_root = seed_root

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, keys: tuple[str, ...]) -> np.ndarray:
        return embed(tuple(keys), dim=self._dim, seed_root=self.seed_root)

    def encode_batch(self, batches: tuple[tuple[str, ...], ...]) -> np.ndarray:
        if not batches:
            return np.zeros((0, self._dim), dtype=np.float32)
        return np.stack([self.encode(keys) for keys in batches])

    def __repr__(self) -> str:
        return f"HashEmbedder(dim={self._dim}, seed_root={self.seed_root!r})"


class CallableEmbedder:
    """Wraps any function into an Embedder.

    This is how a real model arrives without the broker taking on a
    client, a transport or a provider dependency. The caller owns the
    connection and hands in something that maps symbols to a vector::

        soma = SomaOS(embedder=CallableEmbedder(my_model.encode, dim=768))

    The function's output is normalised here rather than trusted, because
    an encoder that returns unnormalised vectors is common and the
    failure it causes downstream -- fidelity bounds above 1.0, dilution
    appearing to improve a memory -- is far from the cause.
    """

    def __init__(
        self,
        fn,
        *,
        dim: int,
        batch_fn=None,
    ) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._fn = fn
        self._batch_fn = batch_fn
        self._dim = int(dim)

    @property
    def dim(self) -> int:
        return self._dim

    def _check(self, vec) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        if arr.size != self._dim:
            raise ValueError(
                f"embedder returned {arr.size} dimensions, expected {self._dim}"
            )
        norm = float(np.linalg.norm(arr))
        if norm == 0.0:
            return arr
        return (arr / norm).astype(np.float32)

    def encode(self, keys: tuple[str, ...]) -> np.ndarray:
        return self._check(self._fn(tuple(keys)))

    def encode_batch(self, batches: tuple[tuple[str, ...], ...]) -> np.ndarray:
        if not batches:
            return np.zeros((0, self._dim), dtype=np.float32)
        if self._batch_fn is not None:
            raw = np.asarray(self._batch_fn(tuple(batches)), dtype=np.float32)
            if raw.shape != (len(batches), self._dim):
                raise ValueError(
                    f"batch embedder returned {raw.shape}, "
                    f"expected {(len(batches), self._dim)}"
                )
            return np.stack([self._check(row) for row in raw])
        return np.stack([self.encode(keys) for keys in batches])

    def __repr__(self) -> str:
        return f"CallableEmbedder(dim={self._dim})"


#: What runs when nobody says otherwise. Every Phase 0b number is this one.
DEFAULT_EMBEDDER = HashEmbedder()


def bytes_per_memory(embedder: Embedder) -> dict[str, int]:
    """Storage cost per rung at this embedder's dimensionality.

    Exposed because changing the encoder changes every capacity figure in
    plans/05_EMBEDDED_TARGET.md: those assume 256 dimensions, and a 768
    dimension model triples them. A caller sizing a store for a device
    should ask here rather than reuse a number from the document.
    """
    from somaos.broker.memory.vector import Grade, nbytes

    probe = np.zeros(embedder.dim, dtype=np.float32)
    return {g.name: nbytes(probe, g) for g in Grade}
