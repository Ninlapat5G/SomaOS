"""Does the store learn how deep the world is, or only ever three levels?

Run:  python -m somaos.bench.experiments.depth

The superordinate-advantage run showed the store degrading across three
levels of generality in the order people do, with the middle level built
by consolidation rather than specified. The obvious next question is
whether three was the answer or the question: given a world organised two,
three, four or five levels deep, does the structure that emerges follow,
or does it always settle at the depth that happened to be tested first?

This matters beyond the psychology. A memory that can only ever represent
a fixed number of levels of generality is a memory that has to be told
about its domain in advance. One that reads the depth out of what it has
seen does not.

Each world is a strict hierarchy -- every item belongs to exactly one
parent at every level -- with roughly the same number of items regardless
of depth, so the depths are compared at equal size rather than at equal
branching. Every question is a six-alternative forced choice against
siblings under the same parent, so chance is 1/6 at every level of every
world and the curves are comparable across the whole run.

The claim under test: for a world D levels deep, the store should keep
broader levels longer than narrower ones, at every depth of world.
"""
from __future__ import annotations

import json
import sys

import numpy as np

from somaos.broker.consolidation import ConsolidationMachine
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import embed, similarity

DEPTHS = (2, 3, 4, 5)
TARGET_ITEMS = 256
N_ALTERNATIVES = 6
CHANCE = 1.0 / N_ALTERNATIVES
INSTANCE_NOISE = 0.45
BUDGETS = (600_000, 80_000, 20_000, 8_000, 3_000, 1_200)


def _branching(depth: int) -> int:
    """Branching that lands near TARGET_ITEMS, so depth is the only variable."""
    return max(2, round(TARGET_ITEMS ** (1.0 / depth)))


def _world(depth: int, rng):
    """A strict hierarchy `depth` levels deep, plus a vector per concept."""
    b = _branching(depth)
    concepts: list[dict[str, str]] = [{}]
    for level in range(depth):
        grown = []
        for path in concepts:
            for i in range(b):
                child = dict(path)
                child[f"L{level}"] = "-".join(
                    [path[f"L{k}"] for k in range(level)] + [f"n{i}"]
                )
                grown.append(child)
        concepts = grown

    vectors: dict[str, np.ndarray] = {}
    items = []
    for path in concepts:
        names = [path[f"L{k}"] for k in range(depth)]
        for level in range(depth):
            name = names[level]
            if name not in vectors:
                vectors[name] = embed(tuple(names[: level + 1]))
        canonical = vectors[names[-1]]
        seen = canonical + rng.standard_normal(canonical.size).astype(
            np.float32
        ) * INSTANCE_NOISE / np.sqrt(canonical.size)
        items.append({"names": names, "seen": seen, "keys": tuple(names)})
    return items, vectors, b


def _siblings(items, depth):
    """Everything that shares a parent, per level. The distractor pool."""
    pools: list[dict[str, set[str]]] = [{} for _ in range(depth)]
    for item in items:
        names = item["names"]
        for level in range(depth):
            parent = names[level - 1] if level else "root"
            pools[level].setdefault(parent, set()).add(names[level])
    return pools


def _forced_choice(vec, correct, options, vectors, rng) -> int:
    others = [name for name in options if name != correct]
    if len(others) > N_ALTERNATIVES - 1:
        picked = rng.choice(len(others), N_ALTERNATIVES - 1, replace=False)
        others = [others[i] for i in picked]
    best = max([correct, *others], key=lambda n: similarity(vec, vectors[n]))
    return int(best == correct)


def run_depth(depth: int, seed: int = 5) -> dict:
    rng = np.random.default_rng(seed)
    items, vectors, branching = _world(depth, rng)
    pools = _siblings(items, depth)

    tree = MemoryTree(beam=4)
    roots: dict[str, str] = {}
    addrs = []
    for tick, item in enumerate(items):
        top = item["names"][0]
        if top not in roots:
            roots[top] = tree.insert(make_node(
                region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
                vec=vectors[top], keys=(top,), span=(tick, tick),
            ), tick=tick)
        addrs.append(tree.insert(make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=item["seen"], keys=item["keys"], span=(tick, tick),
        ), parent=roots[top], tick=tick))

    # The store is given a top tier and the items. Everything between is
    # built by consolidation, from similarity alone.
    consolidation = ConsolidationMachine(
        dilution=DilutionEngine(store_budget_bytes=10 ** 9)
    )
    for cycle in range(6):
        consolidation.run(tree, tick=len(items) + cycle, window=10 ** 6)
    nodes_after_growth = len(tree)

    curve = []
    for budget in BUDGETS:
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=10 ** 5)
        probe_rng = np.random.default_rng(seed)
        hits = np.zeros(depth)
        for addr, item in zip(addrs, items):
            vec = tree.resolve(addr).node.vec
            for level in range(depth):
                parent = item["names"][level - 1] if level else "root"
                hits[level] += _forced_choice(
                    vec, item["names"][level],
                    sorted(pools[level][parent]), vectors, probe_rng,
                )
        curve.append({
            "store_budget_bytes": budget,
            "store_bytes": tree.store_bytes(),
            # index 0 = broadest level, index depth-1 = most specific
            "by_level": [round(float(h / len(items)), 3) for h in hits],
            "all_resolve": all(tree.resolve(a) is not None for a in addrs),
        })

    return {
        "depth": depth,
        "branching": branching,
        "items": len(items),
        "nodes_after_growth": nodes_after_growth,
        "curve": curve,
        "verdict": _verdict(curve, depth),
    }


#: How far two adjacent levels have to be apart to count as distinct
#: rather than as one level reported twice.
SEPARATION = 0.03


def _verdict(curve, depth) -> dict:
    """Broader levels should outlast narrower ones -- and stay distinguishable.

    Ordering alone is cheap: a store that collapsed every level below the
    first into one undifferentiated mush would still be "ordered". What
    says the structure was actually learned is how many adjacent levels
    stay apart, which is reported as a count rather than a pass/fail so the
    capacity shows up as a number instead of hiding behind a threshold.
    """
    ordered = 0
    best_separated = 0
    for row in curve:
        levels = row["by_level"]
        if all(levels[i] >= levels[i + 1] - 1e-9 for i in range(depth - 1)):
            ordered += 1
        separated = sum(
            1 for i in range(depth - 1)
            if levels[i] > levels[i + 1] + SEPARATION
        )
        best_separated = max(best_separated, separated)
    return {
        "rows": len(curve),
        "rows_ordered": ordered,
        "ordering_holds_everywhere": ordered == len(curve),
        # depth - 1 boundaries exist; how many did the store actually keep?
        "boundaries_available": depth - 1,
        "boundaries_kept": best_separated,
        "levels_resolved": best_separated + 1,
    }


def main() -> int:
    out = [run_depth(d) for d in DEPTHS]
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
