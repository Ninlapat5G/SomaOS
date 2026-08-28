"""Prospective memory: intentions that fire, rather than memories that are found.

A trigger is not retrieved. Nobody searches their memory for "things I
meant to do when I next see a pharmacy" -- seeing the pharmacy is what
produces the intention. So this region is an interrupt table, not part of
the tree walk, and it costs no context tokens while it waits.

The three kinds and their very different costs are not an implementation
convenience; they follow the prospective-memory literature
(plans/04_HUMAN_MEMORY_BASIS.md section 5):

    EVENT      a cue produces the intention by itself -- spontaneous
               retrieval, essentially free, so: a hash lookup.
    TIME       due at a tick -- a timer wheel, free per tick.
    PREDICATE  a condition nobody can be cued by, so it has to be watched
               -- monitoring, which in people measurably competes with the
               task at hand, so here it is charged against recall_ops.

Charging the predicate kind and not the other two is the point. It makes
"I'll do it when I see X" cheap and "I'll do it if my mood drops" expensive
in the cost model, which is the same asymmetry people show.

SUSPENDED exists for the same reason: intentions that were interrupted go
on surfacing by themselves, while completed ones stop. A registry with
only armed/retired would either keep firing finished intentions or forget
interrupted ones.
"""
from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum


class TriggerKind(Enum):
    EVENT = "event"          # "when I next see X"
    TIME = "time"            # "at 8am", "every morning"
    PREDICATE = "predicate"  # "if my mood drops below k"


class TriggerState(IntEnum):
    ARMED = 0      # waiting, will fire
    FIRED = 1      # fired this tick, awaiting the outcome
    SUSPENDED = 2  # attempted and not completed -- still surfaces
    RETIRED = 3    # done, or cancelled -- never surfaces again


#: States that a check may still fire from. SUSPENDED is included on
#: purpose: an intention you failed to carry out keeps coming back.
LIVE_STATES = frozenset({TriggerState.ARMED, TriggerState.SUSPENDED})


@dataclass(frozen=True, slots=True)
class Trigger:
    """One intention.

    ``action`` is an opaque label. This region decides *when* something
    should happen and never what -- carrying out the action belongs to the
    layer above, and keeping that boundary is what lets the registry stay
    deterministic and testable without a world attached.
    """

    id: str
    kind: TriggerKind
    action: str
    state: TriggerState = TriggerState.ARMED
    # EVENT
    cue: str | None = None
    # TIME
    due_tick: int | None = None
    every: int | None = None      # None = one-shot
    # PREDICATE
    condition: str | None = None
    repeat: bool = False
    created_tick: int = 0
    fired_count: int = 0
    last_fired_tick: int | None = None

    def __post_init__(self) -> None:
        if self.kind is TriggerKind.EVENT and not self.cue:
            raise ValueError("an event trigger needs a cue")
        if self.kind is TriggerKind.TIME and self.due_tick is None:
            raise ValueError("a time trigger needs a due tick")
        if self.kind is TriggerKind.PREDICATE and not self.condition:
            raise ValueError("a predicate trigger needs a condition")
        if self.every is not None and self.every <= 0:
            raise ValueError(f"every must be positive, got {self.every}")

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_STATES

    @property
    def recurring(self) -> bool:
        return self.repeat or self.every is not None


@dataclass
class TriggerRegistry:
    """Armed intentions, indexed so that the cheap kinds stay cheap.

    ``ops_used`` accumulates the monitoring cost: zero for cued and timed
    intentions however many are armed, and one per armed predicate that
    has to be evaluated. Callers charge it against ``recall_ops_budget``.
    """

    _by_id: dict[str, Trigger] = field(default_factory=dict)
    _by_cue: dict[str, set[str]] = field(default_factory=dict)
    _due: list[tuple[int, str]] = field(default_factory=list)  # min-heap
    _predicates: set[str] = field(default_factory=set)
    ops_used: int = 0

    # ------------------------------------------------------------ arming

    def arm(self, trigger: Trigger) -> str:
        if trigger.id in self._by_id:
            raise ValueError(f"trigger {trigger.id} is already registered")
        self._by_id[trigger.id] = trigger
        if trigger.kind is TriggerKind.EVENT:
            self._by_cue.setdefault(trigger.cue, set()).add(trigger.id)
        elif trigger.kind is TriggerKind.TIME:
            heapq.heappush(self._due, (trigger.due_tick, trigger.id))
        else:
            self._predicates.add(trigger.id)
        return trigger.id

    def get(self, trigger_id: str) -> Trigger:
        return self._by_id[trigger_id]

    def __len__(self) -> int:
        return len(self._by_id)

    def live(self) -> tuple[Trigger, ...]:
        return tuple(
            sorted((t for t in self._by_id.values() if t.is_live), key=lambda t: t.id)
        )

    # ------------------------------------------------------------ firing

    def on_event(self, cue: str, *, tick: int) -> tuple[Trigger, ...]:
        """Spontaneous retrieval: the cue produces the intention. O(1).

        Costs no ops however many intentions are armed, which is the
        modelled claim -- "when I next see X" does not tax you while you
        wait for X.
        """
        fired = [
            self._by_id[tid]
            for tid in sorted(self._by_cue.get(cue, ()))
            if self._by_id[tid].is_live
        ]
        return tuple(self._mark_fired(t, tick) for t in fired)

    def on_tick(self, tick: int) -> tuple[Trigger, ...]:
        """Timed intentions that have come due. Amortised O(1) per tick."""
        fired: list[Trigger] = []
        while self._due and self._due[0][0] <= tick:
            _, tid = heapq.heappop(self._due)
            trigger = self._by_id[tid]
            if not trigger.is_live:
                continue
            fired.append(self._mark_fired(trigger, tick))
            if trigger.every is not None:
                nxt = replace(
                    self._by_id[tid], due_tick=tick + trigger.every,
                    state=TriggerState.ARMED,
                )
                self._by_id[tid] = nxt
                heapq.heappush(self._due, (nxt.due_tick, tid))
        return tuple(fired)

    def check_predicates(
        self,
        world: Mapping[str, object],
        *,
        tick: int,
        evaluate: Callable[[str, Mapping[str, object]], bool] | None = None,
    ) -> tuple[Trigger, ...]:
        """Monitoring: walk every armed condition and see if it holds.

        This is the expensive kind, and it is expensive on purpose. Each
        evaluation costs one op, so an agent that leaves many conditions
        armed pays for the vigilance every tick -- which is what monitoring
        does to people.

        ``evaluate`` defaults to a key lookup so the registry stays free of
        an expression language; a real condition compiler plugs in here.
        """
        checker = evaluate or _default_condition
        fired: list[Trigger] = []
        for tid in sorted(self._predicates):
            trigger = self._by_id[tid]
            if not trigger.is_live:
                continue
            self.ops_used += 1
            if checker(trigger.condition, world):
                fired.append(self._mark_fired(trigger, tick))
        return tuple(fired)

    def _mark_fired(self, trigger: Trigger, tick: int) -> Trigger:
        updated = replace(
            trigger,
            state=TriggerState.FIRED,
            fired_count=trigger.fired_count + 1,
            last_fired_tick=tick,
        )
        self._by_id[trigger.id] = updated
        return updated

    # ------------------------------------------------------------ outcomes

    def complete(self, trigger_id: str, *, tick: int) -> Trigger:
        """The intention was carried out.

        A recurring intention re-arms; a one-shot retires and stops
        surfacing, which is the distinction the literature draws between
        finished and merely suspended intentions.
        """
        trigger = self._by_id[trigger_id]
        if trigger.recurring:
            updated = replace(trigger, state=TriggerState.ARMED)
        else:
            updated = replace(trigger, state=TriggerState.RETIRED)
        self._by_id[trigger_id] = updated
        return updated

    def suspend(self, trigger_id: str) -> Trigger:
        """Attempted and not completed: keep it live so it comes back."""
        updated = replace(self._by_id[trigger_id], state=TriggerState.SUSPENDED)
        self._by_id[trigger_id] = updated
        return updated

    def retire(self, trigger_id: str) -> Trigger:
        """Cancelled or expired. Retired intentions never fire again."""
        updated = replace(self._by_id[trigger_id], state=TriggerState.RETIRED)
        self._by_id[trigger_id] = updated
        return updated

    # ------------------------------------------------------------ accounting

    def state_histogram(self) -> dict[str, int]:
        out = {s.name: 0 for s in TriggerState}
        for trigger in self._by_id.values():
            out[trigger.state.name] += 1
        return out

    def monitoring_load(self) -> int:
        """How many conditions are being watched right now.

        The number to keep an eye on: it is the per-tick tax the agent has
        chosen to pay, and the thing that should stay small.
        """
        return sum(1 for tid in self._predicates if self._by_id[tid].is_live)


def _default_condition(condition: str, world: Mapping[str, object]) -> bool:
    """Truthiness of ``world[condition]``. A placeholder for a real compiler."""
    return bool(world.get(condition, False))
