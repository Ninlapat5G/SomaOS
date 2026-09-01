"""Who decides where the walk goes next.

The recall machine deliberately does not decide. It offers the legal
moves and takes the one it is given, which is what lets an agent choose
for itself without any of that choosing costing correctness: the moves
are a closed set, every one is checked against the current state, and
the budget is enforced by the machine rather than trusted to whoever is
picking.

That leaves the choosing to a ``Navigator``. Two ship here:

    FastPathNavigator
        best-first search, no model. This is the control the whole
        project rests on -- every published number was measured with it,
        it is what runs when a model is unreachable, and it is what an
        agent-driven walk has to beat to have earned its cost.

    CallableNavigator
        hands the decision to a function. This is where an LLM arrives.

There is no client, no transport and no provider here on purpose:
somaos/modelbus/ is out of scope this phase (CLAUDE.md), and a seam that
takes a callable needs neither. The caller owns the connection.

**What a model is allowed to see** is the other half of the design.
``describe()`` returns plain data -- no vectors, no addresses the caller
did not already have, and text_ref only for what has already been
materialised. A navigator that could read the whole store would be
choosing with information the walk is supposed to be earning.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from somaos.broker.recall.machine import Move, RecallMachine, RecallResult, RecallState


@runtime_checkable
class Navigator(Protocol):
    """Drives one walk from CUE to a finished result."""

    def drive(self, machine: RecallMachine, *, max_materialized: int = 8) -> RecallResult:
        """Take steps until the walk stops, then return what it found."""


class Choice:
    """One option put to a chooser, in terms it can reason about.

    Deliberately thin. ``move`` is the verb, ``addr`` is the handle to
    pass back, and ``score`` is how well it matched the cue. What the
    memory actually *is* stays hidden until it is materialised, because
    a chooser that could read every candidate would not be navigating,
    it would be scanning -- which is the thing the tree exists to avoid.
    """

    __slots__ = ("move", "addr", "score", "level", "keys", "text_ref")

    def __init__(self, move: Move, *, addr: str | None = None, score: float = 0.0,
                 level: int | None = None, keys: tuple[str, ...] = (),
                 text_ref: str = "") -> None:
        self.move = move
        self.addr = addr
        self.score = score
        self.level = level
        self.keys = keys
        self.text_ref = text_ref

    def to_jsonable(self) -> dict:
        out: dict = {"move": self.move.value}
        if self.addr is not None:
            out["addr"] = self.addr
            out["score"] = round(self.score, 4)
        if self.level is not None:
            out["level"] = self.level
        if self.keys:
            out["keys"] = list(self.keys)
        if self.text_ref:
            out["text_ref"] = self.text_ref
        return out

    def __repr__(self) -> str:
        return f"Choice({self.move.value}, addr={self.addr}, score={self.score:.3f})"


def describe(machine: RecallMachine, *, reveal_text: bool = True,
             max_materialized: int | None = None) -> dict:
    """The walk's current position, as data a model can be asked about.

    ``reveal_text`` controls whether the shadow text of the *current*
    node is included. It is on by default because a person debugging a
    walk needs it and a model choosing a direction is helped by it, but
    it is a switch rather than a given: invariant V1 says stripping every
    text_ref must leave retrieval unchanged, and the only way that stays
    checkable is if running without them is a supported mode.
    """
    position = machine._position
    here: dict | None = None
    if position is not None:
        node = machine.tree.get(position)
        if node is not None:
            here = {
                "addr": position,
                "level": int(node.level),
                "keys": list(node.keys),
                "span": list(node.span),
            }
            if reveal_text and node.text_ref:
                here["text_ref"] = node.text_ref

    view = {
        "state": machine.state.value,
        "here": here,
        "ops_left": machine.ops_left,
        "tokens_left": max(
            0, machine.context_budget_tokens - machine._tokens_used
            - machine._resident_tokens
        ),
        "materialized": len(machine._materialized),
        "options": [c.to_jsonable() for c in options(machine, reveal_text=reveal_text)],
    }
    if max_materialized is not None:
        # How many more may still be brought into context. Separate from
        # ops_left because they are separate currencies and a chooser
        # that conflates them navigates badly: moving spends effort,
        # bringing a memory to mind spends context and no effort at all.
        view["can_bring_to_mind"] = max(0, max_materialized - len(machine._materialized))
    return view


def options(machine: RecallMachine, *, reveal_text: bool = True) -> tuple[Choice, ...]:
    """The legal moves, with the neighbours each one would move toward."""
    legal = machine.offer()
    if not legal:
        return ()

    out: list[Choice] = []
    for move in legal:
        # Moves that name no destination. Anything legal must appear here
        # or the chooser cannot reach it, and a move offered by the
        # machine but missing from the menu fails silently -- the walk
        # simply never takes it. ``test_every_legal_move_reaches_the_menu``
        # is what keeps the next move added from landing that way.
        if move in (Move.STOP, Move.MATERIALIZE, Move.GATHER):
            out.append(Choice(move))
            continue
        if move is Move.DESCEND and machine._position is not None:
            ranked = machine.tree.rank_children(
                machine._position, machine._cue, tick=machine._tick, beam=machine.beam
            )
            for addr, score in ranked:
                node = machine.tree.get(addr)
                out.append(Choice(
                    move, addr=addr, score=score,
                    level=None if node is None else int(node.level),
                    keys=() if node is None else node.keys,
                    text_ref=(node.text_ref if reveal_text and node is not None else ""),
                ))
            continue
        if move is Move.ASCEND:
            out.append(Choice(move))
            continue
        if move is Move.LATERAL:
            for addr, score in machine._frontier[1:]:
                node = machine.tree.get(addr)
                out.append(Choice(
                    move, addr=addr, score=score,
                    level=None if node is None else int(node.level),
                    keys=() if node is None else node.keys,
                    text_ref=(node.text_ref if reveal_text and node is not None else ""),
                ))
    return tuple(out)


class FastPathNavigator:
    """Best-first search with backtracking. No model, and the control.

    Every number the project has published was measured with this
    driving. Keeping it as a first-class navigator rather than a fallback
    is the point: when a model does drive a walk, the honest question is
    not "does it work" but "does it beat this", and that comparison only
    exists if this stays a real searcher.
    """

    name = "fast"

    def drive(self, machine: RecallMachine, *, max_materialized: int = 8) -> RecallResult:
        return machine.run_fast_path(max_materialized=max_materialized)

    def __repr__(self) -> str:
        return "FastPathNavigator()"


class NavigationError(RuntimeError):
    """The chooser returned something that is not on the menu."""


class CallableNavigator:
    """Hands each decision to a function -- where an LLM plugs in.

    The function is called with the dict from ``describe()`` and returns
    one of the option dicts it was given, or just ``{"move": "stop"}``.

    A real model will sometimes answer off the menu -- an invented move, a
    stale address, a move that was legal one step ago. **That is a
    malformed message, not a bad decision, and the two must not be
    treated alike.** A bad decision -- descending the wrong branch,
    stopping too early -- is the agent choosing, and it is allowed
    without interference: it costs a step, and ASCEND and LATERAL exist
    so it can be taken back. Only the malformed message is handled here,
    because there is no branch it names to walk toward.

    Even then the first answer is not the last word. The chooser is shown
    the same position again with ``error`` saying what was wrong, up to
    ``max_retries`` times, which is what a person doing this does -- "no,
    not that one" is a correction, not the end of remembering. Only when
    the retries are spent does the walk finish with what it has, counted
    in ``off_menu``. Set ``on_error="raise"`` when measuring: an
    experiment comparing model-driven recall against the fast path must
    not quietly absorb the model's mistakes, or it is measuring the
    absorption.

    ``on_error`` decides what a broken or unreachable chooser costs. The
    default finishes the walk with what it has, so a model going down
    degrades the answer instead of losing the memory.

    **Not every legal move makes progress**, and a chooser that keeps
    picking one will otherwise never stop. Materialising a memory that is
    already in context is a no-op, and so is one refused by the token
    budget; neither spends an op, because neither did any work. That is
    the machine being right -- it will not charge for nothing -- so the
    guard belongs here. A step that moves nothing, materialises nothing
    and spends nothing counts as a stall, and ``max_stalls`` consecutive
    stalls end the walk. A hard step cap backs that up, so no chooser can
    spin whatever it returns.
    """

    name = "callable"

    #: Consecutive no-progress steps tolerated before the walk is ended.
    #: More than zero because a model that stalls once may correct itself
    #: on being shown the same position again; a few is not a strategy.
    MAX_STALLS = 3

    #: Malformed answers tolerated at one position before giving up on it.
    #: A model that names a move that does not exist gets shown the menu
    #: again with the reason, the same way a person who reaches for the
    #: wrong memory gets to say "no, the other one". Bounded because a
    #: chooser that cannot produce a legal move after three tries is not
    #: going to on the fourth, and retries cost real model calls.
    MAX_RETRIES = 2

    def __init__(
        self,
        choose,
        *,
        reveal_text: bool = True,
        on_error: str = "stop",
        max_stalls: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        if on_error not in ("stop", "raise"):
            raise ValueError(f"on_error must be 'stop' or 'raise', got {on_error!r}")
        self._choose = choose
        self.reveal_text = reveal_text
        self.on_error = on_error
        self.max_stalls = self.MAX_STALLS if max_stalls is None else int(max_stalls)
        self.max_retries = self.MAX_RETRIES if max_retries is None else int(max_retries)
        #: How many times the chooser was consulted on the last walk. The
        #: unit a model-in-the-loop run is billed in.
        self.calls = 0
        #: Steps on the last walk that changed nothing. Worth reporting:
        #: a model that stalls often is one that does not understand the
        #: menu it is being shown.
        self.stalls = 0
        #: Times the chooser answered with something not on the menu.
        #: Separate from ``stalls`` because they mean different things: a
        #: stall is a legal move that achieved nothing, this is a move
        #: that did not exist.
        self.off_menu = 0
        #: Off-menu answers the chooser then corrected when shown the menu
        #: again. Worth its own counter: a model that recovers is usable
        #: with retries, one that never does needs a different prompt.
        self.recovered = 0

    @staticmethod
    def _progress(machine: RecallMachine) -> tuple:
        return (machine.path.ops_used, len(machine._materialized), machine._position)

    def drive(self, machine: RecallMachine, *, max_materialized: int = 8) -> RecallResult:
        # GATHER enforces the ceiling inside the machine, so the machine
        # has to be told the same number this loop is checking against or
        # one move could buy more context than the other.
        machine.max_materialized = max_materialized
        self.calls = 0
        self.stalls = 0
        self.off_menu = 0
        self.recovered = 0
        stalled = 0
        # Every navigating move spends an op and every useful materialise
        # adds a memory, so this bounds a walk that is making progress and
        # only bites on one that is not.
        cap = machine.ops_budget + max_materialized + self.max_stalls + 1

        for _ in range(cap):
            if machine.state not in (RecallState.NAVIGATE, RecallState.RESIDENT):
                break
            legal = machine.offer()
            if not legal:
                break
            if len(machine._materialized) >= max_materialized:
                machine.step(Move.STOP)
                machine.path.stopped_by = "materialized budget"
                break

            view = describe(machine, reveal_text=self.reveal_text,
                            max_materialized=max_materialized)
            move, addr, failure = self._consult(view, legal)
            if failure is not None:
                machine.step(Move.STOP)
                machine.path.stopped_by = failure
                break

            before = self._progress(machine)
            try:
                machine.step(move, addr=addr)
            except Exception:
                # A stale or invented address reaches the machine as an
                # unknown one -- the same malformed-message case, caught
                # one layer later. Retry it the same way.
                self.off_menu += 1
                move, addr, failure = self._consult(
                    view, legal,
                    reason=f"address {addr!r} is not one of the options",
                    already_tried=1,
                )
                if failure is not None:
                    machine.step(Move.STOP)
                    machine.path.stopped_by = failure
                    break
                # No credit here: _consult was given a ``reason``, so it
                # already counted the correction. Crediting again scored
                # one correction as two recoveries, which is the wrong way
                # for this counter to be wrong -- it reads as a model that
                # takes correction better than it does.
                try:
                    machine.step(move, addr=addr)
                except Exception:
                    if self.on_error == "raise":
                        raise
                    machine.step(Move.STOP)
                    machine.path.stopped_by = "chooser went off menu"
                    break
            if move is Move.STOP:
                break

            if self._progress(machine) == before:
                stalled += 1
                self.stalls += 1
                if stalled >= self.max_stalls:
                    machine.step(Move.STOP)
                    machine.path.stopped_by = "chooser stalled"
                    break
            else:
                stalled = 0
        else:
            if machine.state in (RecallState.NAVIGATE, RecallState.RESIDENT):
                machine.step(Move.STOP)
                machine.path.stopped_by = "step cap"

        if machine.state in (RecallState.NAVIGATE, RecallState.RESIDENT):
            machine.step(Move.STOP)
        return machine.finish()

    def _consult(
        self,
        view: dict,
        legal: tuple[Move, ...],
        *,
        reason: str | None = None,
        already_tried: int = 0,
    ) -> tuple[Move | None, str | None, str | None]:
        """Ask the chooser, giving it a chance to correct a bad answer.

        Returns ``(move, addr, None)`` on success, or
        ``(None, None, why_the_walk_should_stop)`` once the retries are
        spent. A retry re-sends the *same* position with ``error`` set,
        because the position has not changed -- nothing was stepped.
        """
        attempt = already_tried
        while True:
            payload = view if reason is None else {**view, "error": reason}
            try:
                self.calls += 1
                picked = self._choose(payload)
                move, addr = self._parse(picked, legal)
                if attempt > already_tried or reason is not None:
                    self.recovered += 1
                return move, addr, None
            except NavigationError as exc:
                self.off_menu += 1
                if self.on_error == "raise":
                    raise
                if attempt >= self.max_retries:
                    return None, None, "chooser went off menu"
                attempt += 1
                reason = str(exc)
            except Exception:
                if self.on_error == "raise":
                    raise
                # A chooser that threw is not a chooser that answered
                # wrongly -- there is nothing to correct, so no retry.
                return None, None, "chooser failed"

    @staticmethod
    def _parse(picked, legal: tuple[Move, ...]) -> tuple[Move, str | None]:
        if isinstance(picked, Move):
            move, addr = picked, None
        elif isinstance(picked, dict):
            raw = picked.get("move")
            try:
                move = raw if isinstance(raw, Move) else Move(raw)
            except ValueError as exc:
                raise NavigationError(
                    f"{raw!r} is not a move; legal now: "
                    f"{[m.value for m in legal]}"
                ) from exc
            addr = picked.get("addr")
        else:
            raise NavigationError(
                f"chooser returned {type(picked).__name__}, expected a dict or a Move"
            )

        if move not in legal:
            raise NavigationError(
                f"move {move.value!r} is not legal here; legal now: "
                f"{[m.value for m in legal]}"
            )
        return move, addr

    def __repr__(self) -> str:
        return f"CallableNavigator(on_error={self.on_error!r})"
