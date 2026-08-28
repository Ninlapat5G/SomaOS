"""The stored unit and the regions it can live in.

Regions are separate because the memory systems they model are separate:
losing episodic encoding does not cost you your skills, which is the
classic H.M. dissociation (plans/04_HUMAN_MEMORY_BASIS.md section 1). They
differ in how they are indexed, whether they sit in context for free, and
crucially whether they may be diluted at all (N-06).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum

import numpy as np

from somaos.broker.memory.address import content_address
from somaos.broker.memory.vector import Grade, fidelity_of, nbytes


class Region(IntEnum):
    CORE = 0      # identity: always resident, never diluted
    TRIGGER = 1   # intentions: an interrupt table, not retrieval; never diluted
    SKILL = 2     # procedures: keyed by situation cue, not by topic
    ARCHIVE = 3   # experience and knowledge: the tree proper


#: Regions whose contents must survive capacity pressure untouched (N-06).
#: If identity or intent could fade because the store filled up, an agent
#: would stop being the same agent under load, which breaks everything
#: downstream of it.
UNDILUTABLE = frozenset({Region.CORE, Region.TRIGGER})

#: The furthest each region may be diluted.
MAX_GRADE = {
    Region.CORE: Grade.D0_EXACT,
    Region.TRIGGER: Grade.D0_EXACT,
    Region.SKILL: Grade.D3_MERGED,
    Region.ARCHIVE: Grade.D4_COUNTER,
}


class CoreLevel(IntEnum):
    """Sub-levels of CORE, ordered by how slowly they change (McAdams).

    Separating them matters because they need different revision rules: a
    trait should not be rewritten by one contradicting episode, while an
    adaptation legitimately shifts with circumstances.
    """

    TRAIT = 0        # broad dispositions, change over years
    ADAPTATION = 1   # goals, values, coping strategies, change over months
    NARRATIVE = 2    # the life story -- and the root of the ARCHIVE tree


class ArchiveLevel(IntEnum):
    """Levels of the autobiographical tree (Conway & Pleydell-Pearce).

    GENERAL_EVENT is where a walk starts. That is not an optimisation
    guess: generative retrieval in people begins at the general-event
    level and moves down or up from there.
    """

    VERBATIM = 0        # surface detail, minutes
    SPECIFIC_EVENT = 1  # one episode, hours
    GENERAL_EVENT = 2   # a run of episodes, days to weeks  <- walk entry point
    LIFETIME_PERIOD = 3 # a chapter, months to years
    NARRATIVE = 4       # CORE.narrative, the root


WALK_ENTRY_LEVEL = int(ArchiveLevel.GENERAL_EVENT)


@dataclass(slots=True)
class NodeStat:
    """Usage bookkeeping. Drives the depth axis, never the fidelity axis.

    Kept mutable and separate from the node so that recording a read does
    not change the node's content, and therefore does not change its
    address. Reading a memory must not rewrite it.
    """

    last_used_tick: int = 0
    use_count: int = 0
    hit_count: int = 0
    miss_count: int = 0

    def record_use(self, tick: int, *, hit: bool = True) -> None:
        self.last_used_tick = max(self.last_used_tick, tick)
        self.use_count += 1
        if hit:
            self.hit_count += 1
        else:
            self.miss_count += 1


@dataclass(frozen=True, slots=True)
class MemoryNode:
    """One memory, at whatever grade it currently survives at.

    Frozen: a node's content determines its address, so mutating content in
    place would silently invalidate every address pointing at it. Dilution
    produces a *new* node plus an alias (see address.AliasTable); it never
    edits one.
    """

    addr: str
    region: Region
    level: int
    vec: np.ndarray
    grade: Grade
    fidelity: float
    parent: str | None = None
    children: tuple[str, ...] = ()
    n_merged: int = 1
    span: tuple[int, int] = (0, 0)
    keys: tuple[str, ...] = ()
    text_ref: str = ""
    raw_refs: tuple[str, ...] = ()

    @property
    def nbytes(self) -> int:
        """Bytes this node costs the store budget.

        Only the vector is charged. Keys, provenance and text are metadata
        that a real store would keep on cheaper media, and charging them
        here would make the "brain size" knob measure the wrong thing.
        """
        return nbytes(self.vec, self.grade)

    @property
    def is_gradeless(self) -> bool:
        """True once the node has no vector of its own (merged or counted)."""
        return self.grade in (Grade.D3_MERGED, Grade.D4_COUNTER)

    def may_dilute_to(self, grade: Grade) -> bool:
        """Is ``grade`` a legal next state for this node? (N-04, N-06)"""
        if self.region in UNDILUTABLE:
            return False
        return self.grade < grade <= MAX_GRADE[self.region]

    def with_stats_unchanged(self, **changes) -> "MemoryNode":
        """Return a copy with ``changes`` applied and the address recomputed."""
        node = replace(self, **changes)
        return replace(node, addr=address_of(node))


def address_of(node: MemoryNode) -> str:
    return content_address(
        vec=node.vec,
        grade=node.grade,
        level=node.level,
        region=node.region.name,
        children=node.children,
    )


def make_node(
    *,
    region: Region,
    level: int,
    vec: np.ndarray,
    grade: Grade = Grade.D0_EXACT,
    parent: str | None = None,
    children: tuple[str, ...] = (),
    n_merged: int = 1,
    span: tuple[int, int] = (0, 0),
    keys: tuple[str, ...] = (),
    text_ref: str = "",
    raw_refs: tuple[str, ...] = (),
    original: np.ndarray | None = None,
) -> MemoryNode:
    """Build a node with its address and fidelity filled in.

    ``original`` is the D0 vector this node descends from, used to compute
    fidelity; when absent the node is assumed to be its own original, which
    is the right default for something just perceived.
    """
    vec = np.asarray(vec)
    fid = 1.0 if original is None else fidelity_of(original, vec)
    node = MemoryNode(
        addr="",
        region=region,
        level=level,
        vec=vec,
        grade=grade,
        fidelity=fid,
        parent=parent,
        children=children,
        n_merged=n_merged,
        span=span,
        keys=keys,
        text_ref=text_ref,
        raw_refs=raw_refs,
    )
    return replace(node, addr=address_of(node))
