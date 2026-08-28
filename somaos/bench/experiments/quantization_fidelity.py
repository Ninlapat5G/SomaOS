"""Measure what each rung of the dilution ladder actually destroys.

Run:  python -m somaos.bench.experiments.quantization_fidelity

This exists because the ladder in plans/03_MEMORY_ARCHITECTURE.md picks
binary (1 bit per dimension) as the rung where a memory stops being an
individual and becomes a category. That is a strong claim about what
survives compression, so it is measured here rather than asserted.

The question each rung has to answer is not "how small is it" but "what
does it stop being able to answer":

    recall@10   -- can it still tell you *which item*        (verbatim)
    gist        -- can it still tell you *what kind of item*  (gist)

Caveat, stated up front: these are synthetic isotropic Gaussian clusters.
Real embeddings are anisotropic, and binary quantization is known to be
sensitive to exactly that covariance structure, so treat the absolute
numbers as an upper bound and re-measure on real embeddings at Phase 0.5.
What transfers is the *shape* of the result -- which rung kills which
kind of question -- not the third decimal place.
"""
from __future__ import annotations

import json
import sys

import numpy as np

DIM = 256
N_ITEMS = 8000
N_TRIALS = 300
# (n_clusters, sigma): sigma is the noise norm around a unit centroid, so
# larger sigma means clusters bleed into each other.
SETTINGS = ((50, 0.6), (200, 0.9), (500, 1.2), (1000, 1.5))


def _cos_to(matrix: np.ndarray, q: np.ndarray) -> np.ndarray:
    return matrix @ q / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q))


def to_int8(v: np.ndarray) -> np.ndarray:
    scale = np.abs(v).max(axis=-1, keepdims=True)
    scale[scale == 0] = 1.0
    return np.round(v / scale * 127).astype(np.int8).astype(np.float32)


def to_binary(v: np.ndarray) -> np.ndarray:
    return np.sign(v)


def _clustered_store(rng, dim, n, k, sigma):
    centroids = rng.standard_normal((k, dim))
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    labels = rng.integers(0, k, n)
    store = centroids[labels] + rng.standard_normal((n, dim)) * sigma / np.sqrt(dim)
    return store, centroids, labels


def measure(seed: int = 11) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for k, sigma in SETTINGS:
        store, centroids, labels = _clustered_store(rng, DIM, N_ITEMS, k, sigma)
        s_i8, s_bin = to_int8(store), to_binary(store)
        rec_i8, rec_bin = [], []
        gist_i8 = gist_bin = 0
        for _ in range(N_TRIALS):
            c = rng.integers(0, k)
            q = centroids[c] + rng.standard_normal(DIM) * sigma / np.sqrt(DIM)
            gold = set(np.argsort(-_cos_to(store, q))[:10].tolist())

            got = set(np.argsort(-_cos_to(s_i8, to_int8(q)))[:10].tolist())
            rec_i8.append(len(gold & got) / 10)
            gist_i8 += labels[int(np.argmax(_cos_to(s_i8, to_int8(q))))] == c

            # Hamming agreement is the SimHash estimator of the angle
            # (Charikar 2002): P[bits agree] = 1 - theta/pi.
            agree = (s_bin == to_binary(q)).mean(axis=1)
            rec_bin.append(len(gold & set(np.argsort(-agree)[:10].tolist())) / 10)
            gist_bin += labels[int(np.argmax(agree))] == c

        rows.append({
            "n_clusters": k, "sigma": sigma, "dim": DIM,
            "recall_at_10_int8": float(np.mean(rec_i8)),
            "recall_at_10_binary": float(np.mean(rec_bin)),
            "gist_int8": gist_i8 / N_TRIALS,
            "gist_binary": gist_bin / N_TRIALS,
        })
    return rows


def measure_merge(seed: int = 11) -> list[dict]:
    """D3: fold m children into the parent centroid. Does the parent still
    name the cluster its children came from?"""
    rng = np.random.default_rng(seed)
    k, sigma = 500, 1.2
    store, centroids, labels = _clustered_store(rng, DIM, N_ITEMS, k, sigma)
    rows = []
    for m in (2, 4, 8, 16, 32):
        ok = ok_bin = trials = 0
        for _ in range(400):
            c = rng.integers(0, k)
            pool = np.where(labels == c)[0]
            if len(pool) < 2:
                continue
            idx = rng.choice(pool, size=min(m, len(pool)), replace=False)
            parent = store[idx].mean(axis=0)
            ok += int(np.argmax(_cos_to(centroids, parent))) == c
            b = to_binary(parent)
            ok_bin += int(np.argmax((to_binary(centroids) == b).mean(axis=1))) == c
            trials += 1
        rows.append({"merged_children": m,
                     "parent_names_cluster": ok / trials,
                     "binary_parent_names_cluster": ok_bin / trials})
    return rows


def main() -> int:
    out = {"quantization": measure(), "centroid_merge": measure_merge()}
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
