"""The M1 curve in miniature: what a smaller store actually costs.

Run:  python -m somaos.bench.experiments.capacity_curve

This is the shape the whole project stands or falls on. Sweep
``store_budget_bytes`` from generous to punishing and watch two things
separately:

    detail  -- can the store still tell one memory from its neighbours
    gist    -- does it still land in the right part of the space at all

A design that works looks like detail sliding while gist holds up: forget
what exactly was said, still know roughly what it was about. A design that
does not looks like both falling off the same cliff, which is what
"the store filled up and things were lost" looks like in a graph.

This is a smoke-scale preview on synthetic data, not the Phase 0b
measurement. The real M1 runs through the bench harness on pre-registered
seeds. What this is good for is catching the day the curve turns into a
cliff, which no unit test would notice.

Read the gist column with suspicion: there are only a handful of
well-separated groups here, so staying in the right one is an easy test
and it pegs at 1.0. That flatness is the *floor* of the claim, not
evidence for its strength -- a design that fails this is definitely
broken, but passing it proves little. Overlapping groups and real
embeddings will move it, and that is the number worth arguing about.
"""
from __future__ import annotations

import json
import sys

import numpy as np

from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import embed, similarity

N_GROUPS = 8
PER_GROUP = 12
BUDGETS = (200_000, 60_000, 20_000, 8_000, 3_000, 1_200, 500, 200)


def _build() -> tuple[MemoryTree, dict[str, np.ndarray], dict[str, str]]:
    """A tree of general events, each with a run of specific ones under it."""
    tree = MemoryTree()
    originals: dict[str, np.ndarray] = {}
    group_of: dict[str, str] = {}
    for g in range(N_GROUPS):
        topic = f"topic{g}"
        root = make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
            vec=embed((topic,)), keys=(topic,),
        )
        tree.insert(root)
        for i in range(PER_GROUP):
            node = make_node(
                region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
                vec=embed((topic, f"e{g}_{i}")), keys=(topic, f"e{g}_{i}"),
            )
            addr = tree.insert(node, parent=root.addr, tick=g * PER_GROUP + i)
            originals[addr] = np.array(node.vec, dtype=np.float32)
            group_of[addr] = topic
    return tree, originals, group_of


def _score(tree, originals, group_of) -> tuple[float, float]:
    """detail = cosine to the memory's own original.
    gist   = does it still sit closest to its own group's centroid."""
    centroids = {
        topic: embed((topic,)) for topic in sorted(set(group_of.values()))
    }
    detail, gist = [], []
    for addr, original in originals.items():
        node = tree.resolve(addr).node
        detail.append(similarity(original, node.vec))
        best = max(centroids, key=lambda t: similarity(centroids[t], node.vec))
        gist.append(1.0 if best == group_of[addr] else 0.0)
    return float(np.mean(detail)), float(np.mean(gist))


def main() -> int:
    tree, originals, group_of = _build()
    rows = []
    for budget in BUDGETS:
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=50_000)
        detail, gist = _score(tree, originals, group_of)
        rows.append({
            "store_budget_bytes": budget,
            "store_bytes": tree.store_bytes(),
            "detail": round(detail, 4),
            "gist": round(gist, 4),
            "grades": {g: c for g, c in tree.grade_histogram().items() if c},
            "all_addresses_resolve": all(
                tree.resolve(a) is not None for a in originals
            ),
        })
    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
