"""Enforce ``store_budget_bytes`` by fading memories, never by dropping them.

This is the only place in the system where a memory loses detail, and the
only place the fidelity axis moves. The depth axis (how reachable a memory
is) belongs to the tree and is untouched here: a memory can be faint and
easy to reach, or vivid and hard to find, and conflating the two would
lose the distinction the whole design rests on.

Order of attack, and why it is rung-major rather than victim-major:

    every D0 -> D1, coldest first
    then every D1 -> D2, coldest first
    then D2 -> D3, then D3 -> D4

int8 costs almost nothing in answering power (recall@10 stays around 0.98,
see somaos/bench/experiments/quantization_fidelity.py) while returning 4x
the bytes. So converting the whole store to int8 is strictly better than
pushing one cold memory all the way to a tally while vivid ones still sit
at float32. Take every cheap loss everywhere before taking an expensive
one anywhere. Within a rung, coldness decides, which is where "the things
you don't use are the things that fade" actually enters.

CORE and TRIGGER are never candidates (N-06). If identity and intent alone
overflow the budget that is a misconfigured agent, not something to solve
by quietly eroding who it is, so it raises.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np

from somaos.broker.memory.node import UNDILUTABLE, Region, make_node
from somaos.broker.memory.tree import AddressCollision, MemoryTree
from somaos.broker.memory.vector import Grade, encode, similarity

#: Rungs that a sweep walks, in the order it walks them.
VECTOR_RUNGS = (Grade.D1_INT8, Grade.D2_BINARY)

#: Below this fidelity a memory is dissolved as a tally (D4) rather than
#: averaged into its parent's centroid (D3). A vector that has been sign
#: quantized and then composed through further steps carries enough
#: angular error that folding it into the parent would move the parent's
#: gist more than it informs it -- the memory would be corrupting the
#: summary of the group it belonged to. One binarization alone lands near
#: 0.8 (the analytic sqrt(2/pi)), so this floor sits just under a memory
#: that has taken a second hit. It is a calibration number: tune it on dev
#: seeds only (N-15), never after seeing holdout results.
COUNTER_FLOOR = 0.7


class QuotaExceeded(RuntimeError):
    """Identity and intent alone do not fit in the configured store."""


@dataclass(frozen=True, slots=True)
class DilutionEvent:
    """One memory losing detail, and why.

    Emitted for every step so that "what faded, and what did it cost"
    is answerable after the fact. Brains give no such audit trail; that is
    a limitation we are deliberately not copying.
    """

    tick: int
    addr_before: str
    addr_after: str
    region: str
    grade_before: str
    grade_after: str
    fidelity_before: float
    fidelity_after: float
    bytes_before: int
    bytes_after: int
    retrieval_strength: float
    reason: str

    def to_jsonable(self) -> dict:
        return asdict(self)


def compose_fidelity(previous: float, step_cosine: float) -> float:
    """Fidelity after one more lossy step, as a rigorous lower bound.

    Cosine is not multiplicative, so the exact answer would need the
    original float32 vector kept around -- which is precisely what the
    budget is trying to avoid. Angular distance, however, *is* a metric on
    the sphere, so the triangle inequality gives

        theta(original, now) <= theta(original, before) + theta(before, now)

    and therefore cos of the summed angle is never above the true cosine.
    Reporting that bound means the number we publish understates how much
    survived, never overstates it, and it falls monotonically, which is
    what I7 requires.

    The cost of that safety is looseness. The bound is a worst case, so
    repeated composition drives it toward zero well before the vector
    stops being useful -- a memory whose bound reads 0.0 may still sit
    squarely in the right cluster. That is acceptable for what the bound
    is used for here (deciding when a memory is too unreliable to shape
    its parent, and filling in the audit log) and unacceptable as a
    reported metric. M1 is measured in the benchmark against ground truth.
    """
    theta_prev = math.acos(max(-1.0, min(1.0, previous)))
    theta_step = math.acos(max(-1.0, min(1.0, step_cosine)))
    return max(0.0, math.cos(min(theta_prev + theta_step, math.pi)))


@dataclass
class DilutionEngine:
    """Keeps a tree inside ``store_budget_bytes``.

    ``store_budget_bytes`` is the knob the project calls "brain size":
    generous, and memories sit at full precision for a long time; tight,
    and everything the agent rarely touches slides down the ladder until
    only the shape of it is left.
    """

    store_budget_bytes: int
    log: list[DilutionEvent] = field(default_factory=list)

    def reserved_bytes(self, tree: MemoryTree) -> int:
        """Bytes held by regions that may never fade."""
        return sum(tree.region_bytes(r) for r in UNDILUTABLE)

    def available_bytes(self, tree: MemoryTree) -> int:
        """What is left for SKILL and ARCHIVE after identity and intent."""
        return self.store_budget_bytes - self.reserved_bytes(tree)

    def enforce(self, tree: MemoryTree, *, tick: int) -> tuple[DilutionEvent, ...]:
        """Fade memories until the tree fits. Returns what it did.

        Idempotent when already within budget, which matters because
        consolidation calls it every cycle.
        """
        reserved = self.reserved_bytes(tree)
        if reserved > self.store_budget_bytes:
            raise QuotaExceeded(
                f"CORE and TRIGGER need {reserved} bytes but the store budget is "
                f"{self.store_budget_bytes}; identity may not be diluted to fit (N-06)"
            )

        produced: list[DilutionEvent] = []
        # Nodes that cannot take their next rung without colliding with one
        # of their own descendants. Held aside for this sweep so the loop
        # keeps making progress rather than choosing the same victim forever.
        blocked: set[str] = set()

        for rung in VECTOR_RUNGS:
            while tree.store_bytes() > self.store_budget_bytes:
                victim = self._pick(tree, target=rung, tick=tick, blocked=blocked)
                if victim is None:
                    break
                before = tree.store_bytes()
                try:
                    event = self._apply_rung(tree, victim, rung, tick=tick)
                except AddressCollision:
                    blocked.add(victim)
                    continue
                produced.append(event)
                if not self._made_progress(tree, victim, before):
                    blocked.add(victim)

        # Dissolution is one pass, not two: whether a memory is folded into
        # its parent's gist (D3) or only tallied (D4) is a property of how
        # far gone that memory already is, not of how many passes the
        # budget needed.
        while tree.store_bytes() > self.store_budget_bytes:
            victim = self._pick(tree, target=Grade.D3_MERGED, tick=tick, blocked=blocked)
            if victim is None:
                break
            before = tree.store_bytes()
            faded = tree.get(victim).fidelity < COUNTER_FLOOR
            produced.append(self._dissolve(tree, victim, tick=tick, counted=faded))
            if not self._made_progress(tree, victim, before):
                blocked.add(victim)

        self.log.extend(produced)
        return tuple(produced)

    @staticmethod
    def _made_progress(tree: MemoryTree, victim: str, bytes_before: int) -> bool:
        """Did that step actually get us anywhere?

        Every step has to either free bytes or retire the node it acted on.
        A step that does neither would be chosen again immediately and
        forever, so the caller sets it aside. Making termination a property
        of the loop rather than of the correctness of every rung means a bug
        in a rung costs a wasted step, not a hung agent.
        """
        return tree.store_bytes() < bytes_before or tree.get(victim) is None

    # ------------------------------------------------------------ internals

    def _candidates(
        self, tree: MemoryTree, target: Grade, blocked: frozenset[str] = frozenset()
    ) -> list[str]:
        out = []
        for region in (Region.ARCHIVE, Region.SKILL):
            for addr in tree.region_members(region):
                if addr in blocked:
                    continue
                node = tree.get(addr)
                if node is None or not node.may_dilute_to(target):
                    continue
                if target in (Grade.D3_MERGED, Grade.D4_COUNTER) and node.parent is None:
                    continue  # nothing to dissolve into
                out.append(addr)
        return out

    def _pick(
        self,
        tree: MemoryTree,
        *,
        target: Grade,
        tick: int,
        blocked: set[str] | None = None,
    ) -> str | None:
        """Least-practised first, then coldest, then deepest.

        Two different things about "unused" matter here, and an earlier
        version conflated them. Retrieval strength decays, so it says how
        reachable a memory is *now* -- that is the depth axis, and on its
        own it let a memory recalled a dozen times be dissolved once
        everything around it was gone, which is the opposite of what a
        memory system should do under pressure.

        Cumulative retrievals do not decay. In Bjork's terms that is storage
        strength, and practice raises it: a memory you keep going back to
        should not merely be easier to find, it should be harder to lose.
        So use_count leads the ordering and retrieval strength breaks ties
        within it. Depth breaks near-ties toward memories already far from
        the entry point, and the address makes the whole choice
        reproducible, which replay needs (N-15).
        """
        candidates = self._candidates(tree, target, frozenset(blocked or ()))
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda a: (
                tree.stat(a).use_count,
                round(tree.retrieval_strength(a, tick=tick), 9),
                -tree.depth_of(a),
                a,
            ),
        )

    def _apply_rung(
        self, tree: MemoryTree, addr: str, grade: Grade, *, tick: int
    ) -> DilutionEvent:
        node = tree.get(addr)
        new_vec = encode(np.asarray(node.vec, dtype=np.float32), grade)
        step = similarity(node.vec, new_vec)
        faded = make_node(
            region=node.region,
            level=node.level,
            vec=new_vec,
            grade=grade,
            parent=node.parent,
            children=node.children,
            n_merged=node.n_merged,
            span=node.span,
            keys=node.keys,
            text_ref=node.text_ref,
            raw_refs=node.raw_refs,
        )
        # Fidelity is not part of the content address, so overriding it here
        # does not invalidate the address make_node just computed.
        faded = replace(faded, fidelity=compose_fidelity(node.fidelity, step))
        strength = tree.retrieval_strength(addr, tick=tick)
        new_addr = tree.replace_node(addr, faded)
        return DilutionEvent(
            tick=tick,
            addr_before=addr,
            addr_after=new_addr,
            region=node.region.name,
            grade_before=node.grade.name,
            grade_after=grade.name,
            fidelity_before=node.fidelity,
            fidelity_after=faded.fidelity,
            bytes_before=node.nbytes,
            bytes_after=faded.nbytes,
            retrieval_strength=strength,
            reason="store_budget_bytes exceeded",
        )

    def _dissolve(
        self, tree: MemoryTree, addr: str, *, tick: int, counted: bool
    ) -> DilutionEvent:
        node = tree.get(addr)
        strength = tree.retrieval_strength(addr, tick=tick)
        grade_after = Grade.D4_COUNTER if counted else Grade.D3_MERGED
        parent = tree.dissolve_into_parent(addr, counted=counted)
        return DilutionEvent(
            tick=tick,
            addr_before=addr,
            addr_after=parent,
            region=node.region.name,
            grade_before=node.grade.name,
            grade_after=grade_after.name,
            fidelity_before=node.fidelity,
            # What is left *of this memory*, which is not the parent's own
            # fidelity: the parent may be pristine and still say almost
            # nothing about the individual that dissolved into it.
            fidelity_after=tree.resolve(addr).fidelity,
            bytes_before=node.nbytes,
            bytes_after=0,
            retrieval_strength=strength,
            reason=(
                "store_budget_bytes exceeded; too faded to shape the parent"
                if counted else "store_budget_bytes exceeded"
            ),
        )
