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
from somaos.broker.memory.vector import similarity

#: How many times something has to happen before it counts as a pattern.
#: Calibration, not truth: tune on dev seeds only (N-15).
MIN_REPEATS = 4

#: How alike those occurrences have to be. This is the difference between
#: "does this every time" and "happened to do it more than once" -- without
#: it, any busy topic would crystallise into a personality trait.
COHERENCE = 0.55

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
    """

    dilution: DilutionEngine
    min_repeats: int = MIN_REPEATS
    coherence: float = COHERENCE
    max_children: int = MAX_CHILDREN
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

        Three conditions, and every one of them was needed to stop this
        promoting nonsense:

        1. It happened often enough. Counted in *occurrences*, not distinct
           children: doing exactly the same thing six mornings running
           dedupes to one node, and counting children would score that as
           one event while scoring six unrelated errands as six.

        2. The occurrences resemble each other beyond resembling their
           parent. Similarity to the group centroid alone is not enough --
           every child of "mornings" is about mornings, so any grab-bag
           under a shared topic scores high. Coherence is therefore measured
           on what is left after the parent's direction is projected out:
           are these the same thing, or merely the same subject?

        3. They share something the parent does not already say. A habit
           whose only shared key is its parent's topic is not a habit, it is
           the topic.
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
            occurrences = sum(tree.occurrences(c.addr) for c in children)
            if occurrences < self.min_repeats:
                continue

            shared = tuple(k for k in _shared_keys(children) if k not in set(node.keys))
            if not shared:
                continue

            coherence = _coherence(children, against=node.vec)
            if coherence < self.coherence:
                continue
            centroid = _centroid(children)
            habit = make_node(
                region=Region.SKILL,
                level=int(CoreLevel.ADAPTATION),
                vec=centroid,
                keys=shared,
                n_merged=occurrences,
                span=(min(c.span[0] for c in children), max(c.span[1] for c in children)),
                text_ref=f"habit: {' + '.join(shared)} ({occurrences}x)",
                raw_refs=tuple(c.addr for c in children),
            )
            if habit.addr in tree:
                continue  # already crystallised; content addressing makes this free
            addr = tree.insert(habit, tick=tick)
            out.append(Crystallisation(
                addr=addr, region=Region.SKILL.name, keys=shared,
                from_addrs=tuple(c.addr for c in children),
                coherence=coherence, occurrences=occurrences,
            ))
        return out

    # ------------------------------------------------------------ REBALANCE

    def _rebalance(self, tree: MemoryTree, *, tick: int) -> list[str]:
        """Split nodes that have grown too wide for the beam to see past.

        A node with far more children than the beam is where a bounded walk
        silently becomes a lossy one: the walk looks at b of c children and
        never learns the rest exist. Splitting restores the invariant that
        depth, not width, is what a walk pays for.

        Splitting inserts an intermediate node rather than moving anything
        out, so no address changes owner and nothing becomes unreachable.
        """
        split: list[str] = []
        for parent in tree.region_members(Region.ARCHIVE):
            node = tree.get(parent)
            if node is None:
                continue
            children = sorted(tree.children_of(parent))
            if len(children) <= self.max_children:
                continue
            overflow = [tree.get(c) for c in children[self.max_children:]]
            overflow = [c for c in overflow if c is not None]
            if len(overflow) < 2:
                continue
            bucket = make_node(
                region=node.region,
                level=node.level,
                vec=_centroid(overflow),
                keys=node.keys,
                n_merged=sum(c.n_merged for c in overflow),
                span=(min(c.span[0] for c in overflow), max(c.span[1] for c in overflow)),
                text_ref=f"{node.text_ref} (overflow)" if node.text_ref else "",
            )
            if bucket.addr in tree:
                continue
            bucket_addr = tree.insert(bucket, parent=parent, tick=tick)
            for child in overflow:
                tree.reparent(child.addr, bucket_addr)
            split.append(bucket_addr)
        return split


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
