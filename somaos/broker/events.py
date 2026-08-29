"""What the outside world hands the runtime, and what it asks back.

Until now these types lived in ``somaos.bench.lifeworld`` and the runtime
imported them, which had the dependency backwards: the thing being
measured pointed at the thing measuring it. An application would have had
to import the benchmark to feed the memory anything.

So they live here, in three forms that are deliberately separate:

    Observation / Cue / Intent
        concrete dataclasses. What an application constructs.

    ObservationLike / CueLike / IntentLike
        structural protocols. What the runtime accepts. The bench's own
        richer types carry ground-truth fields the runtime must never
        see, so they satisfy these protocols without being converted --
        no adapter in the hot loop, and no way for a policy to reach the
        answer key by accident.

``keys`` is the only field the memory itself is built from. Everything
else is bookkeeping: ``tick`` orders things in time, ``topic`` is a
convenience for grouping, and ``text_ref`` is the human-readable shadow
that the engine is forbidden to read (N-02).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

IntentKind = Literal["time", "event"]


# --------------------------------------------------------------- protocols

@runtime_checkable
class ObservationLike(Protocol):
    """Something that happened, as the runtime needs to see it."""

    @property
    def keys(self) -> tuple[str, ...]: ...
    @property
    def tick(self) -> int: ...
    @property
    def topic(self) -> str: ...


@runtime_checkable
class CueLike(Protocol):
    """A stimulus to remember from. Never carries the answer."""

    @property
    def cue_topics(self) -> tuple[str, ...]: ...
    @property
    def cue_entities(self) -> tuple[str, ...]: ...
    @property
    def tick(self) -> int: ...


@runtime_checkable
class IntentLike(Protocol):
    """Something meant to be done later."""

    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> IntentKind: ...
    @property
    def due_tick(self) -> int | None: ...
    @property
    def cue(self) -> str | None: ...
    @property
    def action(self) -> str: ...


# -------------------------------------------------------------- concrete

@dataclass(frozen=True, slots=True)
class Observation:
    """One thing that happened, ready to be remembered.

    ``keys`` are the symbols the memory is built from -- who, what, where,
    whatever the application decides is salient. They are what similarity
    is computed over, so how an application chooses them decides what the
    agent finds related to what.

    ``text_ref`` is for humans. The engine never reads it, and stripping
    every one of them must leave retrieval unchanged (invariant V1).
    """

    keys: tuple[str, ...]
    tick: int
    topic: str = ""
    text_ref: str = ""

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("an observation with no keys cannot be remembered")

    @classmethod
    def of(cls, *keys: str, tick: int, topic: str = "", text_ref: str = "") -> Observation:
        """Convenience for the common case: ``Observation.of("alice", "coffee", tick=3)``."""
        return cls(keys=tuple(keys), tick=tick, topic=topic or (keys[0] if keys else ""),
                   text_ref=text_ref)


@dataclass(frozen=True, slots=True)
class Cue:
    """What the agent is reminded by, when it tries to remember.

    Split into topics and entities because that is how the cue vector is
    built, not because the runtime treats them differently -- it does not.
    The split is there so an application that knows which of its symbols
    are people and which are subjects can keep saying so.
    """

    cue_topics: tuple[str, ...] = ()
    cue_entities: tuple[str, ...] = ()
    tick: int = 0

    def __post_init__(self) -> None:
        if not self.cue_topics and not self.cue_entities:
            raise ValueError("a cue with nothing in it would match everything")

    @classmethod
    def about(cls, *topics: str, tick: int = 0, entities: tuple[str, ...] = ()) -> Cue:
        return cls(cue_topics=tuple(topics), cue_entities=tuple(entities), tick=tick)


@dataclass(frozen=True, slots=True)
class Intent:
    """Something to do at a time, or on seeing something.

    A time intent costs nothing per tick (a timer wheel) and an event
    intent costs nothing per cue (a keyed lookup). Neither is searched
    for, which is the point: in people, remembering to do something is
    not the same faculty as remembering that something happened.
    """

    id: str
    kind: IntentKind
    due_tick: int | None = None
    cue: str | None = None
    action: str = "act"

    def __post_init__(self) -> None:
        if self.kind == "time" and self.due_tick is None:
            raise ValueError(f"time intent {self.id!r} has no due tick")
        if self.kind == "event" and not self.cue:
            raise ValueError(f"event intent {self.id!r} has no cue to fire on")


@dataclass
class Recollection:
    """What one attempt to remember produced, and what it cost.

    ``cost`` is reported rather than hidden because on a constrained host
    the caller needs to be able to spend its own budget deliberately.
    """

    keys: tuple[tuple[str, ...], ...]
    text_refs: tuple[str, ...]
    tokens: int
    comparisons: int
    ops: int
    path: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.keys)
