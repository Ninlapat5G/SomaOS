"""Consolidation: the cycle that turns experience into structure.

This is the "sleep" of the system, and it is batched for a reason that is
not cost. Complementary Learning Systems (McClelland, McNaughton &
O'Reilly 1995) explains why brains keep a fast episodic store separate
from a slow structural one: a system that extracts structure has to learn
slowly and from interleaved samples, because learning quickly from
whatever just happened overwrites what it already knew. Abstracting after
every observation would produce a personality that lurched with each
conversation.

Four phases, run in order:

    REPLAY     gather what happened in the window
    ABSTRACT   turn repetition into habit -- the only place CORE and SKILL
               grow by themselves
    REBALANCE  keep the tree walkable: split what has grown too wide
    ENFORCE    make the store fit its budget

Order matters. Abstracting before enforcing means a pattern gets the
chance to become a habit before capacity pressure fades the episodes it
would have been drawn from -- otherwise a tight budget would not merely
cost detail, it would cost the agent the ability to learn who it is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from somaos.broker.dilution import DilutionEngine, DilutionEvent
from somaos.broker.memory.node import (
    ArchiveLevel,
    CoreLevel,
    MemoryNode,
    Region,
    make_node,
)
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.regions.core import CoreQuotaExceeded, CoreSet
from somaos.broker.memory.vector import similarity

#: How many times something has to happen before it counts as a pattern.
#: Calibration, not truth: tune on dev seeds only (N-15).
MIN_REPEATS = 4

#: How alike those occurrences have to be. This is the difference between
#: "does this every time" and "happened to do it more than once" -- without
#: it, any busy topic would crystallise into a personality trait.
COHERENCE = 0.55

#: How many times a habit has to have held before it stops being
#: something the agent does and becomes something the agent is. Set well
#: above MIN_REPEATS on purpose: a routine earns a SKILL cheaply, identity
#: should not be cheap.
CORE_REPEATS = 12

#: And over how long a stretch. Frequency alone is a routine -- "does this
#: every morning this week". A trait is a pattern that has survived time,
#: which is the distinction McAdams draws between characteristic
#: adaptations, which shift with circumstances, and dispositional traits,
#: which move over years. Without this a fortnight of anything would become
#: a personality.
CORE_SPAN = 200

#: Children above which a node is split. A wide node makes the beam miss
#: things: with beam b and c children, a walk sees b/c of what is there,
#: so width is what quietly turns a bounded walk into a lossy one.
MAX_CHILDREN = 12


class ConsolidationPhase(Enum):
    AWAKE = "awake"
    REPLAY = "replay"
    ABSTRACT = "abstract"
    REBALANCE = "rebalance"
    ENFORCE = "enforce"


@dataclass(frozen=True, slots=True)
class Crystallisation:
    """A pattern that became a habit, and the evidence it came from."""

    addr: str
    region: str
    keys: tuple[str, ...]
    from_addrs: tuple[str, ...]
    coherence: float
    occurrences: int

    def to_jsonable(self) -> dict:
        return {
            "addr": self.addr, "region": self.region, "keys": list(self.keys),
            "from_addrs": list(self.from_addrs), "coherence": round(self.coherence, 6),
            "occurrences": self.occurrences,
        }


@dataclass
class ConsolidationReport:
    tick: int
    replayed: int = 0
    crystallised: list[Crystallisation] = field(default_factory=list)
    split: list[str] = field(default_factory=list)
    diluted: list[DilutionEvent] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0

    def to_jsonable(self) -> dict:
        return {
            "tick": self.tick,
            "replayed": self.replayed,
            "crystallised": [c.to_jsonable() for c in self.crystallised],
            "split": list(self.split),
            "diluted": [d.to_jsonable() for d in self.diluted],
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
        }


@dataclass
class ConsolidationMachine:
    """Runs one consolidation cycle over a tree.

    Deterministic throughout: every iteration is over a sorted collection,
    so two runs on the same store produce the same structure, which replay
    depends on (N-15).

    Pass ``core`` to let identity grow on its own. Without it the agent only
    ever has the persona it was seeded with; with it, a pattern that has
    held for long enough is promoted into CORE and starts shaping every tick
    rather than waiting to be recalled.
    """

    dilution: DilutionEngine
    core: "CoreSet | None" = None
    min_repeats: int = MIN_REPEATS
    coherence: float = COHERENCE
    max_children: int = MAX_CHILDREN
    core_repeats: int = CORE_REPEATS
    core_span: int = CORE_SPAN
    phase: ConsolidationPhase = ConsolidationPhase.AWAKE

    def run(self, tree: MemoryTree, *, tick: int, window: int = 256) -> ConsolidationReport:
        report = ConsolidationReport(tick=tick, bytes_before=tree.store_bytes())

        self.phase = ConsolidationPhase.REPLAY
        recent = self._replay(tree, tick=tick, window=window)
        report.replayed = len(recent)

        self.phase = ConsolidationPhase.ABSTRACT
        report.crystallised = self._abstract(tree, tick=tick)

        self.phase = ConsolidationPhase.REBALANCE
        report.split = self._rebalance(tree, tick=tick)

        self.phase = ConsolidationPhase.ENFORCE
        report.diluted = list(self.dilution.enforce(tree, tick=tick))

        self.phase = ConsolidationPhase.AWAKE
        report.bytes_after = tree.store_bytes()
        return report

    # ------------------------------------------------------------ REPLAY

    def _replay(self, tree: MemoryTree, *, tick: int, window: int) -> tuple[str, ...]:
        """What happened recently enough to be worth thinking about.

        Interleaved by construction: the window is taken across the whole
        store rather than per topic, so abstraction sees a mixed sample
        instead of whatever the agent did last.
        """
        cutoff = tick - window
        return tuple(
            addr for addr in tree.region_members(Region.ARCHIVE)
            if tree.get(addr) is not None and tree.get(addr).span[1] >= cutoff
        )

    # ------------------------------------------------------------ ABSTRACT

    def _abstract(self, tree: MemoryTree, *, tick: int) -> list[Crystallisation]:
        """Turn repetition into habit.

        A habit is a pattern *within* experience, not a property of a whole
        chapter. An earlier version required every child of a node to share
        a key, which meant a routine only counted if nothing else had ever
        happened under the same topic -- and since routines and one-off
        events naturally sit side by side, it found nothing at all. The
        tree crystallised no habits and scored zero on "what does this
        person usually do" while the flat baselines scored one, purely
        because they kept the instances around.

        So candidates are coherent *subsets*: the same thing lived through
        many times, which content addressing has already collapsed into one
        node with a high occurrence count, and groups of near-identical
        siblings. Each has to clear three bars, and every one of them was
        needed to stop this promoting nonsense:

        1. It happened often enough, counted in occurrences rather than
           distinct nodes -- doing exactly the same thing forty mornings
           dedupes to one node, and counting nodes would score that as one.
        2. The occurrences resemble each other beyond resembling their
           parent. Every child of "mornings" is about mornings, so a
           grab-bag under a shared topic scores high on raw similarity;
           coherence is measured with the parent's direction projected out.
        3. They share something the parent does not already say. A group
           whose only commonality is its subject is the subject.
        """
        out: list[Crystallisation] = []
        for parent in tree.region_members(Region.ARCHIVE):
            node = tree.get(parent)
            if node is None or node.level != int(ArchiveLevel.GENERAL_EVENT):
                continue
            children = [
                tree.get(c) for c in sorted(tree.children_of(parent))
                if tree.get(c) is not None
            ]
            if not children:
                continue

            for group in self._habit_candidates(tree, children):
                crystal = self._crystallise(tree, node, group, tick)
                if crystal is None:
                    continue
                out.append(crystal)
                promoted = self._promote_to_core(
                    tree, tuple(crystal.keys), _centroid(group),
                    crystal.occurrences,
                    (min(c.span[0] for c in group), max(c.span[1] for c in group)),
                    crystal.addr, tick,
                )
                if promoted is not None:
                    out.append(promoted)
        return out

    def _habit_candidates(self, tree, children) -> list[list[MemoryNode]]:
        """Subsets of a node's children that might be a habit.

        A single node lived through many times is the commonest shape and
        is a candidate on its own. Beyond that, groups of siblings that
        resemble each other -- several variations on one routine -- are
        candidates too, so a habit does not have to be byte-identical every
        time to be noticed.
        """
        candidates: list[list[MemoryNode]] = []
        for child in children:
            if tree.occurrences(child.addr) >= self.min_repeats:
                candidates.append([child])
        if len(children) >= self.min_repeats:
            for group in self._group(children):
                if len(group) >= self.min_repeats:
                    candidates.append(group)
        return candidates

    def _crystallise(self, tree, parent, group, tick) -> Crystallisation | None:
        occurrences = sum(tree.occurrences(c.addr) for c in group)
        if occurrences < self.min_repeats:
            return None

        shared = tuple(
            k for k in _shared_keys(group) if k not in set(parent.keys)
        )
        if not shared:
            return None
        if _coherence(group, against=parent.vec) < self.coherence:
            return None

        centroid = _centroid(group)
        habit = make_node(
            region=Region.SKILL,
            level=int(CoreLevel.ADAPTATION),
            vec=centroid,
            keys=shared,
            n_merged=occurrences,
            span=(min(c.span[0] for c in group), max(c.span[1] for c in group)),
            text_ref=f"habit: {' + '.join(shared)} ({occurrences}x)",
            raw_refs=tuple(c.addr for c in group),
        )
        # Already crystallised? Content addressing answers that for free --
        # but the habit may since have been diluted, in which case its
        # original address no longer names a live node while still
        # resolving to the faded one. Checking the tree alone would call it
        # new every cycle and re-crystallise it forever.
        if habit.addr in tree or tree.alias.resolve(habit.addr) != habit.addr:
            return None
        addr = tree.insert(habit, tick=tick)
        return Crystallisation(
            addr=addr, region=Region.SKILL.name, keys=shared,
            from_addrs=tuple(c.addr for c in group),
            coherence=_coherence(group, against=parent.vec), occurrences=occurrences,
        )

    def _promote_to_core(
        self, tree, shared, centroid, occurrences, span, from_addr, tick
    ) -> Crystallisation | None:
        """A habit that has held long enough stops being what the agent does
        and becomes what the agent is.

        Two bars, and both matter. Frequency says the pattern is real;
        duration says it is not a phase. Something done fifty times in a
        week is a project, not a character trait.
        """
        if self.core is None:
            return None
        if occurrences < self.core_repeats:
            return None
        if span[1] - span[0] < self.core_span:
            return None

        trait = make_node(
            region=Region.CORE,
            level=int(CoreLevel.ADAPTATION),
            vec=centroid,
            keys=shared,
            n_merged=occurrences,
            span=span,
            text_ref=f"tends to: {' + '.join(shared)}",
            raw_refs=(from_addr,),
        )
        if trait.addr in tree or tree.alias.resolve(trait.addr) != trait.addr:
            return None
        try:
            addr = self.core.emerge(tree, trait, CoreLevel.ADAPTATION)
        except CoreQuotaExceeded:
            # Identity is full. That is a fact about the configuration, not
            # a reason to evict something the agent has already become, so
            # the pattern simply stays a SKILL.
            return None
        return Crystallisation(
            addr=addr, region=Region.CORE.name, keys=shared,
            from_addrs=(from_addr,), coherence=1.0, occurrences=occurrences,
        )

    # ------------------------------------------------------------ REBALANCE

    def _rebalance(self, tree: MemoryTree, *, tick: int) -> list[str]:
        """Split nodes too wide for a walk to see past.

        A node with far more children than the beam is where a bounded walk
        quietly becomes a lossy one: the walk looks at b of c children and
        never learns the rest exist. Worse, ranking children means comparing
        the cue against every one of them, so a wide node also makes the
        walk pay the linear scan it was supposed to avoid.

        The children are partitioned into groups, one intermediate node per
        group, so width converts into depth. An earlier version moved only
        the overflow into a single bucket, which built a linked list rather
        than a tree -- 600 memories under a node came out 9 deep and still
        568 wide, and measured comparisons per recall stayed at the full
        store size. Grouping is by similarity so a bucket means something a
        walk can steer by, rather than being an arbitrary slice.

        Buckets are inserted between the parent and its children, so no
        address changes owner and nothing becomes unreachable.
        """
        split: list[str] = []
        for parent in tree.region_members(Region.ARCHIVE):
            node = tree.get(parent)
            if node is None:
                continue
            children = [
                tree.get(c) for c in sorted(tree.children_of(parent))
                if tree.get(c) is not None
            ]
            if len(children) <= self.max_children:
                continue

            for group in self._group(children):
                if len(group) < 2:
                    continue
                bucket = make_node(
                    region=node.region,
                    level=node.level,
                    vec=_centroid(group),
                    keys=node.keys,
                    n_merged=sum(c.n_merged for c in group),
                    span=(min(c.span[0] for c in group), max(c.span[1] for c in group)),
                    text_ref=f"{node.text_ref} ({len(group)})" if node.text_ref else "",
                )
                if bucket.addr in tree or tree.alias.resolve(bucket.addr) != bucket.addr:
                    continue
                bucket_addr = tree.insert(bucket, parent=parent, tick=tick)
                for child in group:
                    tree.reparent(child.addr, bucket_addr)
                split.append(bucket_addr)
        return split

    def _group(self, children: list[MemoryNode]) -> list[list[MemoryNode]]:
        """Partition children into groups of at most ``max_children``.

        Seeds are chosen greedily as the children furthest from each other,
        then everything joins its nearest seed. That is k-center: cheap,
        deterministic given a sorted input, and it puts things that are
        alike together, which is the property the walk steers by. A group
        that still comes out too wide is left for the next cycle to split
        again, since a bucket is an ordinary node.
        """
        k = math.ceil(len(children) / self.max_children)
        if k < 2:
            return [children]

        vecs = [np.asarray(c.vec, dtype=np.float32) for c in children]
        seeds = [0]
        while len(seeds) < k:
            far, best = None, None
            for i in range(len(children)):
                if i in seeds:
                    continue
                worst = min(similarity(vecs[i], vecs[j]) for j in seeds)
                if best is None or worst < best:
                    far, best = i, worst
            if far is None:
                break
            seeds.append(far)

        groups: list[list[MemoryNode]] = [[] for _ in seeds]
        for i, child in enumerate(children):
            nearest = max(
                range(len(seeds)), key=lambda si: (similarity(vecs[i], vecs[seeds[si]]), -si)
            )
            groups[nearest].append(child)
        return [g for g in groups if g]


def _centroid(nodes: list[MemoryNode]) -> np.ndarray:
    stacked = np.stack([np.asarray(n.vec, dtype=np.float32) for n in nodes])
    weights = np.array([float(n.n_merged) for n in nodes], dtype=np.float32)
    return (stacked * weights[:, None]).sum(axis=0) / weights.sum()


def _coherence(nodes: list[MemoryNode], *, against: np.ndarray | None = None) -> float:
    """How alike these occurrences are, ignoring what they merely share with
    ``against`` (their parent).

    Without the projection this measures the wrong thing. Every child of a
    topic points partly along that topic, so a set of unrelated errands
    filed under "misc" scores as coherent as a routine repeated daily. The
    parent direction is removed first, so what is compared is what the
    occurrences have in common *beyond* their subject.

    A single occurrence is trivially coherent with itself, which is correct
    -- the same thing done many times is exactly the case habits are made
    of, and repetition is checked separately.
    """
    if len(nodes) == 1:
        return 1.0
    vecs = [np.asarray(n.vec, dtype=np.float32) for n in nodes]
    if against is not None:
        axis = np.asarray(against, dtype=np.float32)
        norm = float(axis @ axis)
        if norm > 0:
            vecs = [v - axis * float(v @ axis) / norm for v in vecs]
    weights = np.array([float(n.n_merged) for n in nodes], dtype=np.float32)
    centre = (np.stack(vecs) * weights[:, None]).sum(axis=0) / weights.sum()
    if not float(np.linalg.norm(centre)):
        return 0.0
    return float(np.mean([similarity(centre, v) for v in vecs]))


def _shared_keys(nodes: list[MemoryNode]) -> tuple[str, ...]:
    """Keys every occurrence has. What the habit is *about*."""
    common = set(nodes[0].keys)
    for node in nodes[1:]:
        common &= set(node.keys)
    return tuple(sorted(common))
