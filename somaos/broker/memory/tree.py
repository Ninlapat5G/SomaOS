"""The memory tree: structure, addressing, and the depth axis (N-03, N-08).

Two things are easy to conflate here and must not be, because they are the
two independent axes of forgetting (plans/03_MEMORY_ARCHITECTURE.md
section 3):

  * ``level`` is *what a node is* -- a verbatim detail, one episode, a run
    of episodes, a chapter of life. It comes from Conway's hierarchy and
    never changes because a node went unread. Demoting a memory to a
    coarser level because nobody looked at it would be a lie about its
    content.

  * ``retrieval_strength`` is *how easy it is to reach right now*. It
    rises when a memory is used and decays when it is not. It never
    touches content, so a cold memory is still exactly the memory it was;
    it just ranks below its siblings, so a bounded walk stops seeing it
    and needs to spend extra steps to dig it out.

That split is Bjork & Bjork's retrieval strength versus storage strength.
Fidelity -- the other axis, owned by the dilution engine -- is the storage
side; nothing in this module ever changes it.

The walk is bounded by construction: at each level it looks at the top
``beam`` children by combined similarity and retrieval strength, so the
cost of a recall is O(depth x beam) and never a function of how much the
agent has ever experienced (N-08).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from somaos.broker.memory.address import AliasTable
from somaos.broker.memory.node import (
    UNDILUTABLE,
    ArchiveLevel,
    MemoryNode,
    NodeStat,
    Region,
    WALK_ENTRY_LEVEL,
    make_node,
)
from somaos.broker.memory.vector import Grade, similarity

#: How much of the gap to full strength one use closes. 0.5 means a single
#: retrieval halves the distance to 1.0 -- fast enough that one deliberate
#: recall visibly helps, slow enough that strength still carries history.
USE_GAIN = 0.5

#: Per-tick multiplicative decay of retrieval strength. 0.999 gives a
#: half-life near 700 ticks, so a memory untouched for a simulated season
#: drops out of the default beam without ever leaving the store.
DECAY_PER_TICK = 0.999

#: Floor on retrieval strength. Strictly positive because zero would make a
#: memory unreachable, which N-01 forbids -- deep is allowed, gone is not.
MIN_RETRIEVAL_STRENGTH = 1e-3


def _compose(previous: float, step: float) -> float:
    """Angular-distance composition; see dilution.engine.compose_fidelity.

    Duplicated here rather than imported to keep the memory core free of a
    dependency on the dilution package, which sits above it.
    """
    theta_prev = math.acos(max(-1.0, min(1.0, previous)))
    theta_step = math.acos(max(-1.0, min(1.0, step)))
    return max(0.0, math.cos(min(theta_prev + theta_step, math.pi)))


class UnknownAddress(KeyError):
    """Raised for an address the tree has never seen. Distinct from a diluted one."""


class AddressCollision(RuntimeError):
    """A node's new content collides with one of its own descendants.

    Most collisions are not errors at all: two memories that have faded to
    the same representation *are* indistinguishable, and merging them is
    what dilution means -- distinct experiences blurring into one as detail
    goes. ``replace_node`` handles that case by merging.

    This is the case it cannot: when the colliding node sits below the one
    being replaced, taking that address would make a node its own ancestor.
    The caller has to skip instead. Proceeding corrupts the tree in a way
    that surfaces much later, as a walk that never terminates.
    """


@dataclass(frozen=True, slots=True)
class Resolution:
    """What ``resolve`` hands back: the node standing today, how far the
    alias chain had to be walked, and a *lower bound* on how much of the
    requested memory survives.

    ``fidelity`` is a bound, not a measurement, and a deliberately
    conservative one. Computing the true cosine against the original would
    mean keeping the original float32 vector, which is exactly what the
    store budget exists to avoid, so the store composes per-step angles
    instead. Angular distance obeys the triangle inequality, so the
    composition never overstates what survived -- but it is a worst case,
    and after several steps it decays much faster than the real thing,
    reaching 0.0 for memories that in practice still land in the right
    part of the space.

    So: trust this number to be safe, not to be accurate. Never report it
    as the answer to "how much was lost". The benchmark measures that
    against ground truth it holds itself, for the same reason the old
    harness priced page faults from the trace rather than from the policy:
    a component must not be allowed to score its own work.
    """

    node: MemoryNode
    fidelity: float
    hops: int
    requested: str

    @property
    def is_original(self) -> bool:
        return self.hops == 0


@dataclass(slots=True)
class _Entry:
    node: MemoryNode
    stat: NodeStat
    retrieval_strength: float
    last_decay_tick: int
    #: How many times this exact content has been perceived. Content
    #: addressing means the second identical experience returns the first
    #: one's address instead of storing a duplicate, which is right for
    #: storage and wrong for everything that needs to know something
    #: happened repeatedly -- doing the same thing every morning would look
    #: like doing it once. So repetition is counted here.
    occurrences: int = 1


class MemoryTree:
    """Content-addressed store with a bounded walk.

    Holds every region. CORE and TRIGGER are flat -- identity and intent
    are looked up, not searched for -- while SKILL and ARCHIVE are the tree
    proper.
    """

    def __init__(self, *, beam: int = 4, decay_per_tick: float = DECAY_PER_TICK) -> None:
        if beam < 1:
            raise ValueError(f"beam must be at least 1, got {beam}")
        self.beam = beam
        self.decay_per_tick = decay_per_tick
        self._entries: dict[str, _Entry] = {}
        self._children: dict[str, list[str]] = {}
        self._by_region: dict[Region, set[str]] = {r: set() for r in Region}
        self._by_key: dict[str, set[str]] = {}
        self.alias = AliasTable()
        #: Per-hop cosine: how much of the *content at this address* carried
        #: over into whatever replaced it. One entry per alias link, so a
        #: memory that has moved several times composes its own history on
        #: read rather than any single hop standing in for the whole chain.
        #: Storing a running total here instead was a bug: the first hop's
        #: value (int8, near-lossless) was reported for an address that had
        #: since been binarised, overstating what survived.
        self._step_cosine: dict[str, float] = {}
        #: Tally of what has been fully dissolved into an ancestor (D4).
        #: Kept so that a counted-away memory can still answer "something
        #: like this happened, n times" rather than nothing at all.
        self.counters: dict[str, int] = {}

    # ------------------------------------------------------------ inserting

    def insert(self, node: MemoryNode, *, parent: str | None = None, tick: int = 0) -> str:
        """Add a node, optionally under ``parent``. Returns its address.

        Inserting content that is already present is a no-op returning the
        existing address: that is the dedupe N-07 buys, and it must not
        silently create a second copy with different statistics.
        """
        # An address that has been retired must not come back to life. The
        # content is the same, but it has since been diluted and forwarded,
        # and re-inserting it here would leave two live nodes for one
        # address -- the fresh copy under the old address and the faded one
        # the alias points at -- so resolve() would answer with one while
        # everything else operated on the other. Perceiving retired content
        # again is a repeat occurrence of what it became, not a new memory.
        current = self.alias.resolve(node.addr)
        if current != node.addr:
            keeper = self._entries.get(current)
            if keeper is None:
                # A forwarding chain that ends nowhere means something
                # deleted a node without leaving an alias. Fail loudly here
                # rather than inserting at the retired address, which would
                # leave the address both live and forwarded -- a state that
                # surfaces much later as a walk that never terminates.
                raise UnknownAddress(
                    f"{node.addr} forwards to {current}, which is not in the tree"
                )
            self._record_repeat(keeper, node, tick)
            return current

        if node.addr in self._entries:
            existing = self._entries[node.addr]
            self._record_repeat(existing, node, tick)
            return node.addr
        if parent is not None:
            parent = self.alias.resolve(parent)
            if parent not in self._entries:
                raise UnknownAddress(f"parent {parent} is not in the tree")
            node = replace(node, parent=parent)
            # The parent's address depends on its children, but rewriting it
            # on every insert would churn every ancestor address on every
            # observation. Child lists are tracked here and folded into
            # addresses at consolidation instead.
            self._children.setdefault(parent, []).append(node.addr)
        self._entries[node.addr] = _Entry(
            node=node,
            stat=NodeStat(last_used_tick=tick, use_count=0),
            retrieval_strength=1.0,
            last_decay_tick=tick,
        )
        self._children.setdefault(node.addr, [])
        self._by_region[node.region].add(node.addr)
        for key in node.keys:
            self._by_key.setdefault(key, set()).add(node.addr)
        return node.addr

    def _record_repeat(self, entry: _Entry, node: MemoryNode, tick: int) -> None:
        """Living through the same thing again: count it, widen its span,
        and make it as available as recalling it would."""
        entry.occurrences += 1
        entry.node = replace(
            entry.node,
            span=(
                min(entry.node.span[0], node.span[0]),
                max(entry.node.span[1], node.span[1]),
            ),
        )
        self._apply_decay(entry, tick)
        gap = 1.0 - entry.retrieval_strength
        entry.retrieval_strength = min(1.0, entry.retrieval_strength + gap * USE_GAIN)

    # ------------------------------------------------------------ resolving

    def resolve(self, addr: str) -> Resolution:
        """Follow the alias chain and return what stands today (I1).

        Never returns None for an address the tree has issued. If the
        memory has been merged away its ancestor answers, with the fidelity
        of what is left -- coarser, but an answer.
        """
        current = self.alias.resolve(addr)
        hops = len(self.alias.chain(addr)) - 1
        entry = self._entries.get(current)
        if entry is None:
            raise UnknownAddress(f"{addr} was never issued by this tree")
        return Resolution(
            node=entry.node,
            fidelity=self._surviving(addr),
            hops=hops,
            requested=addr,
        )

    def _surviving(self, addr: str) -> float:
        """Lower bound on how much of ``addr`` is left, composed over its hops.

        Composition matters: two steps that each keep 0.9 do not keep 0.9
        together. Angles add, so the bound falls faster than any single hop
        suggests -- which is the conservative direction.
        """
        chain = self.alias.chain(addr)
        surviving = 1.0
        for link in chain[:-1]:
            surviving = _compose(surviving, self._step_cosine.get(link, 1.0))
        return surviving

    def get(self, addr: str) -> MemoryNode | None:
        """Raw lookup with no alias following. For internals and tests."""
        entry = self._entries.get(addr)
        return None if entry is None else entry.node

    def __contains__(self, addr: str) -> bool:
        return addr in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def addresses(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def children_of(self, addr: str) -> tuple[str, ...]:
        return tuple(self._children.get(addr, ()))

    def region_members(self, region: Region) -> tuple[str, ...]:
        return tuple(sorted(self._by_region[region]))

    def by_key(self, key: str) -> tuple[str, ...]:
        """Exact symbolic lookup -- the O(1) path SKILL and TRIGGER use.

        Deliberately not similarity: a habit fires because the situation
        matches its cue, not because the topic is vaguely related.
        """
        return tuple(sorted(self._by_key.get(key, ())))

    # ------------------------------------------------------------ depth axis

    def retrieval_strength(self, addr: str, *, tick: int) -> float:
        """Current accessibility of a memory, after decay up to ``tick``."""
        entry = self._entries[self.alias.resolve(addr)]
        self._apply_decay(entry, tick)
        return entry.retrieval_strength

    def _apply_decay(self, entry: _Entry, tick: int) -> None:
        elapsed = tick - entry.last_decay_tick
        if elapsed <= 0:
            return
        decayed = entry.retrieval_strength * (self.decay_per_tick ** elapsed)
        entry.retrieval_strength = max(MIN_RETRIEVAL_STRENGTH, decayed)
        entry.last_decay_tick = tick

    def touch(self, addr: str, *, tick: int, hit: bool = True) -> None:
        """Record a use: raises retrieval strength, leaves content alone.

        Retrieval practice makes a memory easier to reach, not more
        detailed. Fidelity is untouched here on purpose -- what dilution
        threw away does not come back because you thought about it again.
        """
        entry = self._entries[self.alias.resolve(addr)]
        self._apply_decay(entry, tick)
        entry.stat.record_use(tick, hit=hit)
        gap = 1.0 - entry.retrieval_strength
        entry.retrieval_strength = min(1.0, entry.retrieval_strength + gap * USE_GAIN)

    def stat(self, addr: str) -> NodeStat:
        return self._entries[self.alias.resolve(addr)].stat

    def occurrences(self, addr: str) -> int:
        """How many times this exact experience has been had.

        Distinct from ``stat.use_count``, which counts recollections. Living
        through something and remembering it are different events, and a
        habit is built from the first kind.
        """
        return self._entries[self.alias.resolve(addr)].occurrences

    # ------------------------------------------------------------ walking

    def entry_points(self, region: Region = Region.ARCHIVE) -> tuple[str, ...]:
        """Where a walk starts: the general-event level, not the root.

        Conway found generative retrieval begins mid-hierarchy. It is also
        cheaper -- the levels above rarely discriminate between candidates,
        so descending through them spends steps to learn nothing.
        """
        return tuple(
            sorted(
                addr for addr in self._by_region[region]
                if self._entries[addr].node.level == WALK_ENTRY_LEVEL
            )
        )

    def rank_children(
        self, addr: str, cue: np.ndarray, *, tick: int, beam: int | None = None
    ) -> tuple[tuple[str, float], ...]:
        """Top-``beam`` children of ``addr`` by relevance and accessibility.

        The score multiplies similarity by retrieval strength, which is
        what makes a cold memory hard rather than impossible to find: it
        still scores, it just falls below the beam and needs a wider one --
        that is, more ops -- to surface. Ties break on address so the walk
        is deterministic (N-15's replay requirement).
        """
        width = self.beam if beam is None else beam
        scored = []
        for child in self._children.get(self.alias.resolve(addr), ()):
            entry = self._entries[child]
            self._apply_decay(entry, tick)
            sim = similarity(cue, entry.node.vec)
            scored.append((child, sim * entry.retrieval_strength))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return tuple(scored[:width])

    def depth_of(self, addr: str) -> int:
        """Hops from ``addr`` up to a node with no parent."""
        current = self.alias.resolve(addr)
        depth = 0
        seen = {current}
        while (parent := self._entries[current].node.parent) is not None:
            if parent in seen:
                raise RuntimeError(f"parent chain from {addr} loops at {parent}")
            seen.add(parent)
            current = parent
            depth += 1
        return depth

    # ------------------------------------------------------------ rewriting

    def replace_node(self, old_addr: str, new_node: MemoryNode) -> str:
        """Swap a node for a re-encoded version and forward its address.

        Used by the dilution engine. Statistics and retrieval strength
        carry over: how reachable a memory is has nothing to do with how
        much detail it kept, and losing its history on every dilution step
        would make an already-faded memory look freshly minted.
        """
        old_addr = self.alias.resolve(old_addr)
        entry = self._entries.get(old_addr)
        if entry is None:
            raise UnknownAddress(f"{old_addr} is not in the tree")
        if new_node.fidelity > entry.node.fidelity + 1e-9:
            raise ValueError(
                f"fidelity may not rise: {entry.node.fidelity} -> {new_node.fidelity}"
            )
        if new_node.addr == old_addr:
            return old_addr

        # Where does this content already live? Either it is held by a live
        # node, or it was held by one that has since been retired and
        # forwarded. Both are collisions, and both resolve the same way:
        # merge, because two memories that have faded to the same
        # representation are the same memory now. What must never happen is
        # taking the address anyway -- that leaves one address both live and
        # forwarded, and the corruption only surfaces later as a walk that
        # never terminates.
        holder = self.alias.resolve(new_node.addr)
        if holder != new_node.addr or new_node.addr in self._entries:
            if holder not in self._entries:
                raise UnknownAddress(
                    f"{new_node.addr} forwards to {holder}, which is not in the tree"
                )
            if holder == old_addr:
                return old_addr
            if self._is_descendant(holder, of=old_addr):
                raise AddressCollision(
                    f"{old_addr} would take the address of its own descendant "
                    f"{holder}; skip this node instead"
                )
            return self._merge_into(old_addr, holder, entry)

        self._entries[new_node.addr] = _Entry(
            node=new_node,
            stat=entry.stat,
            retrieval_strength=entry.retrieval_strength,
            last_decay_tick=entry.last_decay_tick,
        )
        self._children[new_node.addr] = self._children.pop(old_addr, [])
        for child in self._children[new_node.addr]:
            child_entry = self._entries[child]
            child_entry.node = replace(child_entry.node, parent=new_node.addr)
        parent = new_node.parent
        if parent is not None and parent in self._children:
            self._children[parent] = [
                new_node.addr if c == old_addr else c for c in self._children[parent]
            ]
        self._by_region[entry.node.region].discard(old_addr)
        self._by_region[new_node.region].add(new_node.addr)
        for key in entry.node.keys:
            self._by_key.get(key, set()).discard(old_addr)
        for key in new_node.keys:
            self._by_key.setdefault(key, set()).add(new_node.addr)

        del self._entries[old_addr]
        self.alias.add(old_addr, new_node.addr)
        self._step_cosine[old_addr] = similarity(entry.node.vec, new_node.vec)
        return new_node.addr

    def _is_descendant(self, addr: str, *, of: str) -> bool:
        """Does ``addr`` sit somewhere below ``of``?"""
        walker = self._entries[addr].node.parent
        seen = set()
        while walker is not None and walker not in seen:
            if walker == of:
                return True
            seen.add(walker)
            entry = self._entries.get(walker)
            walker = entry.node.parent if entry else None
        return False

    def _merge_into(self, old_addr: str, survivor: str, entry: _Entry) -> str:
        """Two memories have faded into the same thing. Keep one.

        This is not a failure mode; it is dilution working. Once two
        experiences quantise to the same vector there is nothing left that
        distinguishes them, and pretending otherwise would mean keeping two
        nodes that no walk could ever tell apart. The survivor absorbs the
        other's occurrences and children, and the retired address forwards,
        so both are still answerable -- with the same answer, which is the
        truthful one.
        """
        keeper = self._entries[survivor]
        keeper.occurrences += entry.occurrences
        keeper.stat.use_count += entry.stat.use_count
        keeper.stat.last_used_tick = max(
            keeper.stat.last_used_tick, entry.stat.last_used_tick
        )
        keeper.retrieval_strength = max(keeper.retrieval_strength, entry.retrieval_strength)
        keeper.node = replace(
            keeper.node,
            span=(
                min(keeper.node.span[0], entry.node.span[0]),
                max(keeper.node.span[1], entry.node.span[1]),
            ),
        )

        for child in self._children.pop(old_addr, []):
            child_entry = self._entries[child]
            child_entry.node = replace(child_entry.node, parent=survivor)
            self._children.setdefault(survivor, []).append(child)

        parent = entry.node.parent
        if parent is not None:
            parent = self.alias.resolve(parent)
            self._children[parent] = [
                c for c in self._children.get(parent, []) if c != old_addr
            ]

        self._by_region[entry.node.region].discard(old_addr)
        for key in entry.node.keys:
            self._by_key.get(key, set()).discard(old_addr)
        del self._entries[old_addr]

        self.alias.add(old_addr, survivor)
        self._step_cosine[old_addr] = similarity(entry.node.vec, keeper.node.vec)
        return survivor

    def dissolve_into_parent(self, addr: str, *, counted: bool = False) -> str:
        """Fold a node into its parent (D3), or tally it away (D4).

        The node stops existing as an individual: its address forwards to
        the parent, so a query holding it still gets an answer -- about the
        group it belonged to rather than about itself.

        The two rungs differ in whether the memory still gets a say in what
        that group is. At D3 its vector is averaged into the parent's
        centroid, so it still shapes the gist. At D4 it contributes only a
        tally, because a memory that has already been through sign
        quantization and several compositions carries enough angular error
        that averaging it in would corrupt the parent more than it informs
        it. Either way the parent's count grows, so "things like this
        happened here, n times" survives -- which is why the last rung is
        still not deletion.

        Grandchildren re-parent upward rather than being dropped; nothing
        may become unreachable.
        """
        addr = self.alias.resolve(addr)
        entry = self._entries.get(addr)
        if entry is None:
            raise UnknownAddress(f"{addr} is not in the tree")
        parent = entry.node.parent
        if parent is None:
            raise ValueError("cannot dissolve a node with no parent; it would vanish")
        if entry.node.region in UNDILUTABLE:
            raise ValueError(f"{entry.node.region.name} may never be dissolved")
        parent = self.alias.resolve(parent)

        for child in self._children.get(addr, []):
            child_entry = self._entries[child]
            child_entry.node = replace(child_entry.node, parent=parent)
            self._children[parent].append(child)
        self._children.pop(addr, None)
        self._children[parent] = [c for c in self._children[parent] if c != addr]

        parent_entry = self._entries[parent]
        # n_merged and span are not part of the content address, so the
        # parent can record that it now stands for more without becoming a
        # different node.
        parent_entry.node = replace(
            parent_entry.node,
            n_merged=parent_entry.node.n_merged + (0 if counted else entry.node.n_merged),
            span=(
                min(parent_entry.node.span[0], entry.node.span[0]),
                max(parent_entry.node.span[1], entry.node.span[1]),
            ),
        )

        self._by_region[entry.node.region].discard(addr)
        for key in entry.node.keys:
            self._by_key.get(key, set()).discard(addr)
        del self._entries[addr]

        self.alias.add(addr, parent)
        self.counters[parent] = self.counters.get(parent, 0) + entry.node.n_merged
        self._step_cosine[addr] = similarity(
            entry.node.vec, self._entries[parent].node.vec
        )
        return parent

    def reparent(self, addr: str, new_parent: str) -> None:
        """Move a node under a different parent, keeping its content.

        Used by consolidation to split a node that has grown wider than the
        beam. Nothing about the node itself changes, so its address is
        unaffected and no alias is needed -- this is a change to the shape
        of the tree, not to any memory in it.
        """
        addr = self.alias.resolve(addr)
        new_parent = self.alias.resolve(new_parent)
        if addr not in self._entries:
            raise UnknownAddress(f"{addr} is not in the tree")
        if new_parent not in self._entries:
            raise UnknownAddress(f"{new_parent} is not in the tree")
        if addr == new_parent:
            raise ValueError("a node cannot be its own parent")

        walker = new_parent
        while walker is not None:
            if walker == addr:
                raise ValueError(
                    f"reparenting {addr} under {new_parent} would create a cycle"
                )
            entry = self._entries.get(walker)
            walker = entry.node.parent if entry else None

        entry = self._entries[addr]
        old_parent = entry.node.parent
        if old_parent is not None:
            old_parent = self.alias.resolve(old_parent)
            self._children[old_parent] = [
                c for c in self._children.get(old_parent, []) if c != addr
            ]
        entry.node = replace(entry.node, parent=new_parent)
        self._children.setdefault(new_parent, []).append(addr)

    # ------------------------------------------------------------ accounting

    def store_bytes(self) -> int:
        """Total bytes the vectors cost. What ``store_budget_bytes`` caps."""
        return sum(entry.node.nbytes for entry in self._entries.values())

    def region_bytes(self, region: Region) -> int:
        return sum(
            self._entries[addr].node.nbytes for addr in self._by_region[region]
        )

    def grade_histogram(self) -> dict[str, int]:
        out = {g.name: 0 for g in Grade}
        for entry in self._entries.values():
            out[entry.node.grade.name] += 1
        return out

    def mean_fidelity(self, region: Region | None = None) -> float:
        addrs = (
            self._by_region[region] if region is not None else set(self._entries)
        )
        if not addrs:
            return 1.0
        return sum(self._entries[a].node.fidelity for a in addrs) / len(addrs)
