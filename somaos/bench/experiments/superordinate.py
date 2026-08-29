"""Does the store degrade the way a damaged semantic memory does?

Run:  python -m somaos.bench.experiments.superordinate

Everything measured so far asks whether the design works. This asks
something different and much harder to pass: whether it goes wrong the way
people go wrong.

In semantic dementia there is a documented, replicated pattern -- the
superordinate advantage. As the disease progresses, patients lose specific
names before general ones: they will say "animal" long after they have
stopped being able to say "zebra", and the literature reports the same
ordering across comprehension, naming and non-verbal tasks.

So: shrink the store, and ask the same picture three ways.

    subordinate   which exact one is this          "the labrador next door"
    basic         what kind of thing is this       "a dog"
    superordinate what family does it belong to    "an animal"

⚠️ On circularity, because this is the obvious objection and it is half
right. The dilution ladder was deliberately built so that sign bits keep
category and lose instance, so "general survives specific" is designed in
and finding it here proves nothing on its own.

What is *not* designed in is the shape. Nothing in the ladder knows about
three levels; it knows about a vector and how many bits are left. If the
store had two behaviours -- exact, then a cliff where basic and
superordinate vanish together -- it would be reproducing a two-level split
we built, and the human three-level ordering would not be there. A graded
ordering, with basic sitting between the other two across a range of store
sizes, is a property of how the representation degrades rather than
something written into it.

That is the claim under test. Anything less specific is unfalsifiable and
worth nothing.

Two things the first version of this file got wrong, both of which made
the answer meaningless and both of which are fixed here:

**Unequal chance levels.** Picking one superordinate out of six is a
one-in-six guess; picking one exemplar out of two hundred is not. Scoring
raw accuracy across levels measured how many alternatives each question
had, not how much of the memory survived. Every probe is now a
six-alternative forced choice -- the correct answer plus five distractors
drawn from the same parent -- so chance is 1/6 at all three levels and the
numbers can be compared to each other. That is also how the patient
comprehension studies are run.

**A stored trace identical to the concept.** Storing the canonical vector
for "labrador" and then asking which canonical vector it is nearest to is
a lookup, not a memory test, and it stays perfect until the vector is
destroyed outright -- which is why the first run showed no gradation, only
a cliff at the very end. You do not encounter the platonic labrador; you
encounter one, once. Each exemplar now carries per-instance variation, so
identifying it later requires the trace to have kept something, and the
three levels can come apart.
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

N_SUPER = 6        # animal, vehicle, tool, ...
N_BASIC = 6        # dog, cat, horse, ...       (per superordinate)
N_SUB = 6          # labrador, poodle, ...      (per basic)

#: Alternatives per question. Same at every level so chance is 1/6
#: throughout and the three curves mean the same thing.
N_ALTERNATIVES = 6
CHANCE = 1.0 / N_ALTERNATIVES

#: How much one encounter differs from the concept. Without this the probe
#: is a lookup rather than a memory test.
INSTANCE_NOISE = 0.45

#: Store sizes, from comfortable down to severe. Standing in for disease
#: progression: the representation is intact, then progressively coarser.
BUDGETS = (600_000, 200_000, 80_000, 30_000, 12_000, 5_000, 2_000, 800)


def _taxonomy(rng):
    """A three-level hierarchy, with each exemplar seen once and imperfectly."""
    items, super_vecs, basic_vecs, sub_vecs = [], {}, {}, {}
    for s in range(N_SUPER):
        sup = f"kind{s}"
        super_vecs[sup] = embed((sup,))
        for b in range(N_BASIC):
            basic = f"{sup}-type{b}"
            basic_vecs[basic] = embed((sup, basic))
            for x in range(N_SUB):
                sub = f"{basic}-one{x}"
                canonical = embed((sup, basic, sub))
                sub_vecs[sub] = canonical
                seen = canonical + rng.standard_normal(canonical.size).astype(
                    np.float32
                ) * INSTANCE_NOISE / np.sqrt(canonical.size)
                items.append({
                    "sub": sub, "basic": basic, "super": sup,
                    "keys": (sup, basic, sub), "seen": seen,
                })
    return items, super_vecs, basic_vecs, sub_vecs


def _forced_choice(vec, correct: str, pool: list[str], vectors, rng) -> int:
    """Six-alternative forced choice: is the trace nearest the right one?"""
    distractors = [name for name in pool if name != correct]
    if len(distractors) > N_ALTERNATIVES - 1:
        picked = rng.choice(len(distractors), N_ALTERNATIVES - 1, replace=False)
        distractors = [distractors[i] for i in picked]
    options = [correct, *distractors]
    best = max(options, key=lambda name: similarity(vec, vectors[name]))
    return int(best == correct)


def run(seed: int = 5) -> list[dict]:
    rng = np.random.default_rng(seed)
    items, super_vecs, basic_vecs, sub_vecs = _taxonomy(rng)

    # Distractor pools: siblings under the same parent, which is what makes
    # the specific question hard and the general one easy.
    subs_of = {}
    basics_of = {}
    for item in items:
        subs_of.setdefault(item["basic"], []).append(item["sub"])
        basics_of.setdefault(item["super"], set()).add(item["basic"])

    tree = MemoryTree(beam=4)
    parents, addrs = {}, {}
    for tick, item in enumerate(items):
        sup = item["super"]
        if sup not in parents:
            parents[sup] = tree.insert(make_node(
                region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
                vec=super_vecs[sup], keys=(sup,), span=(tick, tick),
            ), tick=tick)
        addrs[item["sub"]] = tree.insert(make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=item["seen"], keys=item["keys"], span=(tick, tick),
        ), parent=parents[sup], tick=tick)

    # Let the tree grow its own middle tier before anything is degraded.
    #
    # This turned out to be the whole experiment. Without it the store has
    # exactly two levels -- the superordinate parent and the exemplars --
    # so there is no representation for a basic-level answer to fall back
    # on, and basic tracks subordinate exactly. Nothing is told where dogs
    # end and cats begin; REBALANCE splits a node once it is wider than the
    # beam can see past, and groups the children by similarity because that
    # is what makes a bucket steerable. Whether those buckets line up with
    # the basic level is the question, not the setup.
    consolidation = ConsolidationMachine(
        dilution=DilutionEngine(store_budget_bytes=10 ** 9)
    )
    for cycle in range(6):
        consolidation.run(tree, tick=len(items) + cycle, window=10 ** 6)

    rows = []
    for budget in BUDGETS:
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=10_000)
        probe_rng = np.random.default_rng(seed)  # same questions every round
        sub_hits, basic_hits, super_hits = [], [], []
        for item in items:
            vec = tree.resolve(addrs[item["sub"]]).node.vec
            sub_hits.append(_forced_choice(
                vec, item["sub"], subs_of[item["basic"]], sub_vecs, probe_rng))
            basic_hits.append(_forced_choice(
                vec, item["basic"], sorted(basics_of[item["super"]]),
                basic_vecs, probe_rng))
            super_hits.append(_forced_choice(
                vec, item["super"], sorted(super_vecs), super_vecs, probe_rng))
        rows.append({
            "store_budget_bytes": budget,
            "store_bytes": tree.store_bytes(),
            "subordinate": round(float(np.mean(sub_hits)), 3),
            "basic": round(float(np.mean(basic_hits)), 3),
            "superordinate": round(float(np.mean(super_hits)), 3),
            "chance": round(CHANCE, 3),
            "grades": {g: c for g, c in tree.grade_histogram().items() if c},
            "nodes": len(tree),
            "all_resolve": all(tree.resolve(a) is not None for a in addrs.values()),
        })
    return rows


def verdict(rows: list[dict]) -> dict:
    """Is the ordering there, and is it graded rather than a cliff?"""
    ordered = sum(
        1 for r in rows
        if r["superordinate"] >= r["basic"] >= r["subordinate"] - 1e-9
    )
    graded = sum(
        1 for r in rows
        if r["superordinate"] > r["basic"] > r["subordinate"]
    )
    above_chance = sum(1 for r in rows if r["superordinate"] > CHANCE * 1.5)
    return {
        "rows": len(rows),
        "rows_with_correct_ordering": ordered,
        "rows_where_basic_sits_strictly_between": graded,
        "rows_where_superordinate_beats_chance": above_chance,
        "ordering_holds_everywhere": ordered == len(rows),
        "graded_not_a_cliff": graded >= 3,
    }


def main() -> int:
    rows = run()
    out = {"curve": rows, "verdict": verdict(rows)}
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
