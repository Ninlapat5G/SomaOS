"""The recall state machine (N-08, N-09).

Remembering is a walk, not a lookup. The agent picks the direction and the
engine takes the step, which is the division that lets the model be as
free-form as it likes without any of it costing correctness: the moves are
a closed set, every one of them is checked, the budget is enforced here
rather than trusted to the caller, and the whole path is recorded.

That recording is not instrumentation. The sequence of steps *is* the
explanation of why a memory came to mind, so explain() falls out of the
walk instead of needing a separate lineage system bolted on later.

Why a state machine and not one function:

    IDLE         nothing in flight. The only safe point to snapshot or
                 replay from, because no partial walk is outstanding.
    CUE          the single place the outside world gets in, so input is
                 validated once, and the entry point is chosen -- at the
                 general-event level, following Conway, not at the root.
    RESIDENT     identity, fired intentions, and any procedure the
                 situation itself calls up. Kept separate from NAVIGATE so
                 that "it was already there" and "I went and found it" do
                 not get charged the same.
    NAVIGATE     the only state that spends recall_ops, so the ceiling is
                 enforced in exactly one place.
    MATERIALIZE  the only state that spends context tokens and the only
                 state permitted to touch text_ref (invariant V2).
    SETTLE       all side effects, together and last: usage recorded, the
                 walked nodes promoted, the path closed. Nothing mutates
                 mid-walk, so a walk is replayable.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from somaos.broker.memory.node import MemoryNode, Region
from somaos.broker.memory.tree import STRENGTH_WEIGHT, MemoryTree
from somaos.broker.memory.vector import cue_vector, similarity


class RecallState(Enum):
    IDLE = "idle"
    CUE = "cue"
    RESIDENT = "resident"
    NAVIGATE = "navigate"
    MATERIALIZE = "materialize"
    SETTLE = "settle"


class Move(Enum):
    """What the agent may ask for. A closed set, so nothing unchecked runs."""

    DESCEND = "descend"          # toward detail
    ASCEND = "ascend"            # toward gist
    LATERAL = "lateral"          # to a neighbour of the current node
    MATERIALIZE = "materialize"  # bring it into context, paying tokens
    STOP = "stop"


class IllegalMove(RuntimeError):
    """The move is not available from the current state or position."""


@dataclass(frozen=True, slots=True)
class WalkStep:
    move: Move
    addr: str | None
    score: float
    ops_after: int
    note: str = ""


@dataclass(slots=True)
class WalkPath:
    """Where the walk went, and why it stopped.

    This is the audit record and the explanation in one: replaying these
    steps reproduces the walk, and reading them says which memories led to
    which.
    """

    steps: list[WalkStep] = field(default_factory=list)
    ops_used: int = 0
    stopped_by: str = ""
    materialized: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict:
        return {
            "ops_used": self.ops_used,
            "stopped_by": self.stopped_by,
            "materialized": list(self.materialized),
            "steps": [
                {"move": s.move.value, "addr": s.addr, "score": round(s.score, 6),
                 "ops_after": s.ops_after, "note": s.note}
                for s in self.steps
            ],
        }


@dataclass(frozen=True, slots=True)
class RecallResult:
    """What a completed walk hands to the layer above."""

    nodes: tuple[MemoryNode, ...]
    path: WalkPath
    tokens_used: int
    resident_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.tokens_used + self.resident_tokens


def structural_tokens(node: MemoryNode) -> int:
    """Default token cost: derived from what a node *is*, never from its text.

    Deliberate. If cost depended on ``text_ref`` then deleting the shadow
    text would change which memories fit in the budget, and invariant V1 --
    strip every text_ref and the traversal is unchanged -- would be false.

    The benchmark overrides this with real counts taken from the trace, for
    the same reason the previous harness priced page faults from the trace
    and not from the policy: a component that prices its own output will
    eventually price it favourably.
    """
    return 8 + 8 * max(0, node.level)


class RecallMachine:
    """One walk. Construct, ``begin``, step, ``finish``.

    Reused across walks by calling ``begin`` again; the machine returns to
    IDLE after ``finish`` so that a half-finished walk can never leak into
    the next one.
    """

    def __init__(
        self,
        tree: MemoryTree,
        *,
        ops_budget: int = 32,
        context_budget_tokens: int = 2048,
        beam: int | None = None,
        tokens_of: Callable[[MemoryNode], int] = structural_tokens,
    ) -> None:
        self.tree = tree
        self.ops_budget = ops_budget
        self.context_budget_tokens = context_budget_tokens
        self.beam = beam if beam is not None else tree.beam
        self.tokens_of = tokens_of

        self.state = RecallState.IDLE
        self.path = WalkPath()
        self._cue: np.ndarray | None = None
        self._tick = 0
        self._position: str | None = None
        self._frontier: tuple[tuple[str, float], ...] = ()
        self._materialized: list[str] = []
        self._tokens_used = 0
        self._resident: tuple[str, ...] = ()
        self._resident_tokens = 0
        self._visited: list[str] = []
        #: Most addresses the search held at once during the last walk.
        #: Recorded because on a memory-constrained host the walk's working
        #: set is a hard limit, not an accounting detail: the frontier has
        #: to fit in RAM even when the store lives in flash.
        self.peak_working_addresses = 0

    # ------------------------------------------------------------ CUE

    def begin(
        self,
        *,
        topics: Sequence[str] = (),
        entities: Sequence[str] = (),
        tick: int = 0,
        resident: Sequence[str] = (),
        region: Region = Region.ARCHIVE,
    ) -> RecallState:
        """Turn a stimulus into a cue vector and pick where to start.

        ``resident`` is what is already in context -- identity, and any
        intention that just fired. It is admitted without spending a single
        op, because it was never searched for.

        Procedures come in the same way, and this is not a shortcut. A
        habit is a context-response association: seeing the situation is
        what produces it, with no search through episodic memory and no
        detour through what you were trying to achieve. Squire's
        dissociation is the evidence -- H.M. could not form new episodes
        and still got better at a skill every day -- so routing procedural
        recall through the ARCHIVE walk would model the one arrangement the
        patient data rules out. It is a keyed lookup, so it costs no ops.

        Leaving this out was measurable: the tree crystallised habits
        correctly and then scored zero on "what does this person usually
        do", because nothing could reach them.
        """
        self.path = WalkPath()
        self._cue = cue_vector(tuple(topics), tuple(entities))
        self._tick = tick
        self._materialized = []
        self._tokens_used = 0
        self._visited = []
        self.state = RecallState.CUE

        self._resident = tuple(resident) + self._cued_procedures(
            tuple(topics) + tuple(entities), exclude=set(resident)
        )
        self._resident_tokens = sum(
            self.tokens_of(self.tree.resolve(addr).node) for addr in self._resident
        )
        if self._resident:
            self.state = RecallState.RESIDENT
            self.path.steps.append(
                WalkStep(Move.MATERIALIZE, None, 1.0, self.path.ops_used,
                         note=f"resident: {len(self._resident)} node(s), no walk needed")
            )

        entries = self.tree.entry_points(region)
        if not entries:
            self._frontier = ()
            self._position = None
            self.state = RecallState.NAVIGATE
            return self.state

        # Choosing among entry points is itself a step, and it is charged
        # as one: this is the "which chapter of my life was that in?"
        # moment, and pretending it is free would understate every walk.
        # Same rule as rank_children: relevance leads, accessibility
        # adjusts. Choosing which chapter to start in must not be decided by
        # which chapter was opened last.
        scored = sorted(
            (
                (
                    addr,
                    similarity(self._cue, self.tree.get(addr).vec)
                    + STRENGTH_WEIGHT * self.tree.retrieval_strength(addr, tick=tick),
                )
                for addr in self._counted(entries)
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )
        self.path.ops_used += 1
        self._frontier = tuple(scored[: self.beam])
        self._position = self._frontier[0][0] if self._frontier else None
        self.path.steps.append(
            WalkStep(Move.DESCEND, self._position,
                     self._frontier[0][1] if self._frontier else 0.0,
                     self.path.ops_used, note="entry at general-event level")
        )
        self.state = RecallState.NAVIGATE
        return self.state

    def _cued_procedures(self, cue_keys, *, exclude: set[str]) -> tuple[str, ...]:
        """Procedures whose situation the cue names. O(1) per key, no ops.

        Indexed by key rather than by similarity on purpose: similarity
        answers "what is this about", and a habit is not retrieved by
        being on-topic. It fires because the situation matches.
        """
        found: list[str] = []
        for key in cue_keys:
            for addr in self.tree.by_key(key):
                node = self.tree.get(addr)
                if node is None or node.region is not Region.SKILL:
                    continue
                if addr in exclude or addr in found:
                    continue
                found.append(addr)
        return tuple(sorted(found))

    def _counted(self, addrs):
        """Charge the tree's comparison counter for choosing an entry point.

        Picking where to start is a scan over entry points, and leaving it
        uncounted would understate the cost of every walk.
        """
        for addr in addrs:
            self.tree.comparisons += 1
            yield addr

    # ------------------------------------------------------------ NAVIGATE

    @property
    def position(self) -> str | None:
        return self._position

    @property
    def ops_left(self) -> int:
        return max(0, self.ops_budget - self.path.ops_used)

    def offer(self) -> tuple[Move, ...]:
        """Moves that are legal right now -- the agent's tool menu.

        Offering only what is legal means a model that picks badly wastes a
        step, never corrupts the walk.
        """
        if self.state not in (RecallState.NAVIGATE, RecallState.RESIDENT):
            return ()
        if self.ops_left <= 0:
            return (Move.STOP,)
        moves = [Move.STOP]
        if self._position is not None:
            moves.append(Move.MATERIALIZE)
            if self.tree.children_of(self._position):
                moves.append(Move.DESCEND)
            if self.tree.get(self._position).parent is not None:
                moves.append(Move.ASCEND)
            if len(self._frontier) > 1:
                moves.append(Move.LATERAL)
        return tuple(sorted(set(moves), key=lambda m: m.value))

    def step(self, move: Move, *, addr: str | None = None) -> RecallState:
        """Take one step. Raises rather than silently doing something else."""
        if self.state in (RecallState.IDLE, RecallState.SETTLE):
            raise IllegalMove(f"no walk in progress (state {self.state.value})")
        if move is Move.STOP:
            return self._stop("agent stopped")
        if self.ops_left <= 0 and move is not Move.MATERIALIZE:
            return self._stop("ops_exhausted")
        if move not in self.offer():
            raise IllegalMove(f"{move.value} is not available here")

        if move is Move.MATERIALIZE:
            return self._materialize(addr or self._position)
        if move is Move.DESCEND:
            return self._descend()
        if move is Move.ASCEND:
            return self._ascend()
        return self._lateral()

    def _descend(self) -> RecallState:
        ranked = self.tree.rank_children(
            self._position, self._cue, tick=self._tick, beam=self.beam
        )
        self.path.ops_used += 1
        if not ranked:
            self.path.steps.append(
                WalkStep(Move.DESCEND, self._position, 0.0, self.path.ops_used,
                         note="no children")
            )
            return self.state
        self._frontier = ranked
        self._position = ranked[0][0]
        self._visited.append(self._position)
        self.path.steps.append(
            WalkStep(Move.DESCEND, self._position, ranked[0][1], self.path.ops_used)
        )
        return self.state

    def _ascend(self) -> RecallState:
        parent = self.tree.get(self._position).parent
        self.path.ops_used += 1
        self._position = self.tree.alias.resolve(parent)
        self._visited.append(self._position)
        self._frontier = ((self._position, 0.0),)
        self.path.steps.append(
            WalkStep(Move.ASCEND, self._position,
                     similarity(self._cue, self.tree.get(self._position).vec),
                     self.path.ops_used)
        )
        return self.state

    def _lateral(self) -> RecallState:
        """Step to the next-best sibling already on the frontier.

        Spreading activation: having reached one memory, its neighbours are
        cheap to reach, which is why the frontier is kept rather than
        recomputed.
        """
        self.path.ops_used += 1
        remaining = tuple(a for a in self._frontier if a[0] != self._position)
        if not remaining:
            self.path.steps.append(
                WalkStep(Move.LATERAL, self._position, 0.0, self.path.ops_used,
                         note="no neighbours left")
            )
            return self.state
        self._position, score = remaining[0]
        self._frontier = remaining
        self._visited.append(self._position)
        self.path.steps.append(
            WalkStep(Move.LATERAL, self._position, score, self.path.ops_used)
        )
        return self.state

    # ------------------------------------------------------------ MATERIALIZE

    def _materialize(self, addr: str | None) -> RecallState:
        """Bring a memory into context. The only place tokens are spent.

        A memory that does not fit is refused rather than truncated: half a
        memory in context is worse than none, because it reads as complete.
        """
        if addr is None:
            raise IllegalMove("nothing to materialize")
        resolved = self.tree.resolve(addr)
        if resolved.node.addr in self._materialized:
            return self.state
        cost = self.tokens_of(resolved.node)
        if self._tokens_used + self._resident_tokens + cost > self.context_budget_tokens:
            self.path.steps.append(
                WalkStep(Move.MATERIALIZE, resolved.node.addr, resolved.fidelity,
                         self.path.ops_used, note="refused: context budget")
            )
            return self._stop("context_budget_exhausted")
        self._materialized.append(resolved.node.addr)
        self._tokens_used += cost
        self.path.steps.append(
            WalkStep(Move.MATERIALIZE, resolved.node.addr, resolved.fidelity,
                     self.path.ops_used, note=f"{cost} tokens")
        )
        return self.state

    # ------------------------------------------------------------ fast path

    def run_fast_path(self, *, max_materialized: int = 8) -> RecallResult:
        """Walk without asking anyone: best-first, with backtracking.

        This is what runs when the model is not consulted and what still
        runs when the model bus is down, so it has to be a real searcher
        rather than a placeholder -- otherwise agent-directed recall would
        only ever be compared against something broken.

        It keeps one frontier for the whole walk instead of taking the best
        child at each node in turn. Greedy descent cannot recover from a
        wrong turn: it was measured bringing back four neighbours of the
        wanted memory while the memory itself sat five levels down at full
        precision, unreached, with most of its step budget unspent. People
        do not search that way either -- "no, not that trip, the other
        one" is backtracking, and it is the whole reason ASCEND and LATERAL
        exist as moves.

        With a shared frontier a branch that turns out to be poor simply
        stops producing high scores, and the search returns to the best
        unexplored candidate anywhere it has been.
        """
        seen: dict[str, float] = {}
        frontier: list[tuple[float, str]] = []

        for addr, score in self._frontier:
            frontier.append((score, addr))
            seen[addr] = score
        if self._position is not None and self._position not in seen:
            seen[self._position] = 0.0
        self.peak_working_addresses = max(self.peak_working_addresses, len(seen))

        while frontier and self.ops_left > 0:
            frontier.sort(key=lambda pair: (-pair[0], pair[1]))
            score, addr = frontier.pop(0)
            self._position = addr
            self._visited.append(addr)
            children = self.tree.rank_children(
                addr, self._cue, tick=self._tick, beam=self.beam
            )
            self.path.ops_used += 1
            self.path.steps.append(
                WalkStep(Move.DESCEND, addr, score, self.path.ops_used)
            )
            for child, child_score in children:
                if child in seen:
                    continue
                seen[child] = child_score
                frontier.append((child_score, child))
            self.peak_working_addresses = max(self.peak_working_addresses, len(seen))

        # Report what the search found, best first. Materialising in score
        # order rather than in the order the walk happened to pass things
        # is what lets a bounded budget spend itself on the closest
        # matches instead of on whatever was nearest the entrance.
        for addr, _ in sorted(seen.items(), key=lambda pair: (-pair[1], pair[0])):
            if len(self._materialized) >= max_materialized:
                break
            if self.state is not RecallState.NAVIGATE:
                break
            self._materialize(addr)

        if self.state is RecallState.NAVIGATE:
            self._stop("fast path complete")
        return self.finish()

    # ------------------------------------------------------------ SETTLE

    def _stop(self, reason: str) -> RecallState:
        self.path.stopped_by = reason
        self.state = RecallState.MATERIALIZE
        return self.state

    def finish(self) -> RecallResult:
        """Close the walk: record uses, promote what was reached, return.

        Every side effect happens here and nowhere else. During the walk
        the tree is only read, which is what makes a walk safe to abandon
        and cheap to replay.
        """
        if not self.path.stopped_by:
            self.path.stopped_by = "finished"
        self.state = RecallState.SETTLE

        for addr in self._materialized:
            self.tree.touch(addr, tick=self._tick, hit=True)
        for addr in self._visited:
            if addr not in self._materialized:
                self.tree.touch(addr, tick=self._tick, hit=False)

        self.path.materialized = list(self._materialized)
        nodes = tuple(
            self.tree.resolve(addr).node
            for addr in tuple(self._resident) + tuple(self._materialized)
        )
        result = RecallResult(
            nodes=nodes,
            path=self.path,
            tokens_used=self._tokens_used,
            resident_tokens=self._resident_tokens,
        )
        self.state = RecallState.IDLE
        self._position = None
        self._frontier = ()
        return result
