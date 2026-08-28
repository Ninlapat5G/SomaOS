"""WP-A4 -- region rules: identity that stays put, intentions that fire.

The claims under test are the ones the design leans on: cued and timed
intentions cost nothing to keep armed while watched conditions cost every
tick, a suspended intention keeps coming back while a finished one does
not, and identity is capped at admission rather than eroded later.
"""
from __future__ import annotations

import pytest

from somaos.broker.memory.node import CoreLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import DEFAULT_DIM, embed
from somaos.broker.regions.core import CoreQuotaExceeded, CoreSet
from somaos.broker.regions.trigger import (
    Trigger,
    TriggerKind,
    TriggerRegistry,
    TriggerState,
)

D0_BYTES = DEFAULT_DIM * 4


def _event(tid="t", cue="pharmacy", **kw):
    return Trigger(id=tid, kind=TriggerKind.EVENT, cue=cue, action="do it", **kw)


def _time(tid="t", due=10, **kw):
    return Trigger(id=tid, kind=TriggerKind.TIME, due_tick=due, action="do it", **kw)


def _pred(tid="t", cond="mood_low", **kw):
    return Trigger(id=tid, kind=TriggerKind.PREDICATE, condition=cond, action="rest", **kw)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", [
    dict(kind=TriggerKind.EVENT, cue=None),
    dict(kind=TriggerKind.TIME, due_tick=None),
    dict(kind=TriggerKind.PREDICATE, condition=None),
])
def test_a_trigger_must_carry_what_its_kind_needs(bad):
    with pytest.raises(ValueError):
        Trigger(id="t", action="x", **bad)


def test_a_non_positive_period_is_refused():
    with pytest.raises(ValueError):
        Trigger(id="t", kind=TriggerKind.TIME, due_tick=1, every=0, action="x")


def test_arming_the_same_id_twice_is_refused():
    registry = TriggerRegistry()
    registry.arm(_event())
    with pytest.raises(ValueError):
        registry.arm(_event())


# ---------------------------------------------------------------- cost model


def test_waiting_for_a_cue_costs_nothing_however_many_are_armed():
    """Spontaneous retrieval: 'when I next see X' does not tax you meanwhile."""
    registry = TriggerRegistry()
    for i in range(500):
        registry.arm(_event(f"t{i}", cue=f"cue{i}"))
    registry.on_event("cue7", tick=1)
    registry.on_event("nothing-armed-for-this", tick=2)
    assert registry.ops_used == 0


def test_waiting_for_a_time_costs_nothing_per_tick():
    registry = TriggerRegistry()
    for i in range(500):
        registry.arm(_time(f"t{i}", due=1000 + i))
    for tick in range(50):
        registry.on_tick(tick)
    assert registry.ops_used == 0


def test_watching_a_condition_costs_one_op_per_armed_condition_per_check():
    """Monitoring is the expensive kind, in people and here."""
    registry = TriggerRegistry()
    for i in range(8):
        registry.arm(_pred(f"t{i}", cond=f"c{i}"))
    registry.check_predicates({}, tick=1)
    assert registry.ops_used == 8
    registry.check_predicates({}, tick=2)
    assert registry.ops_used == 16


def test_retiring_a_condition_stops_paying_for_it():
    registry = TriggerRegistry()
    for i in range(4):
        registry.arm(_pred(f"t{i}", cond=f"c{i}"))
    registry.retire("t0")
    registry.retire("t1")
    assert registry.monitoring_load() == 2
    registry.check_predicates({}, tick=1)
    assert registry.ops_used == 2


# ---------------------------------------------------------------- firing


def test_a_cue_fires_only_its_own_intentions():
    registry = TriggerRegistry()
    registry.arm(_event("t1", cue="pharmacy"))
    registry.arm(_event("t2", cue="pharmacy"))
    registry.arm(_event("t3", cue="library"))
    assert {t.id for t in registry.on_event("pharmacy", tick=1)} == {"t1", "t2"}
    assert registry.on_event("gym", tick=1) == ()


def test_a_timed_intention_fires_when_due_not_before():
    registry = TriggerRegistry()
    registry.arm(_time("t1", due=10))
    assert registry.on_tick(9) == ()
    assert [t.id for t in registry.on_tick(10)] == ["t1"]


def test_a_late_tick_still_fires_what_was_due():
    """Ticks can be skipped; an intention must not be silently missed."""
    registry = TriggerRegistry()
    registry.arm(_time("t1", due=10))
    assert [t.id for t in registry.on_tick(99)] == ["t1"]


def test_a_recurring_intention_rearms_itself():
    registry = TriggerRegistry()
    registry.arm(_time("t1", due=10, every=5))
    assert [t.id for t in registry.on_tick(10)] == ["t1"]
    registry.complete("t1", tick=10)
    assert [t.id for t in registry.on_tick(15)] == ["t1"]
    assert registry.get("t1").fired_count == 2


def test_a_condition_fires_only_when_it_holds():
    registry = TriggerRegistry()
    registry.arm(_pred("t1", cond="mood_low"))
    assert registry.check_predicates({"mood_low": False}, tick=1) == ()
    assert [t.id for t in registry.check_predicates({"mood_low": True}, tick=2)] == ["t1"]


def test_a_custom_evaluator_can_replace_the_placeholder():
    registry = TriggerRegistry()
    registry.arm(_pred("t1", cond="energy < 3"))
    fired = registry.check_predicates(
        {"energy": 1}, tick=1, evaluate=lambda cond, w: w["energy"] < 3
    )
    assert [t.id for t in fired] == ["t1"]


# ---------------------------------------------------------------- the FSM


def test_an_interrupted_intention_keeps_coming_back():
    """Suspended intentions still surface; that is why the state exists."""
    registry = TriggerRegistry()
    registry.arm(_event("t1"))
    registry.on_event("pharmacy", tick=1)
    registry.suspend("t1")
    assert registry.get("t1").state is TriggerState.SUSPENDED
    assert [t.id for t in registry.on_event("pharmacy", tick=5)] == ["t1"]


def test_a_finished_intention_stops():
    registry = TriggerRegistry()
    registry.arm(_event("t1"))
    registry.on_event("pharmacy", tick=1)
    registry.complete("t1", tick=1)
    assert registry.get("t1").state is TriggerState.RETIRED
    assert registry.on_event("pharmacy", tick=5) == ()


def test_a_cancelled_intention_never_fires_again():
    registry = TriggerRegistry()
    registry.arm(_time("t1", due=10, every=5))
    registry.retire("t1")
    assert registry.on_tick(50) == ()


def test_firing_records_when_and_how_often():
    registry = TriggerRegistry()
    registry.arm(_event("t1"))
    registry.on_event("pharmacy", tick=3)
    registry.suspend("t1")
    registry.on_event("pharmacy", tick=8)
    trigger = registry.get("t1")
    assert trigger.fired_count == 2
    assert trigger.last_fired_tick == 8


def test_live_listing_is_ordered_and_excludes_retired():
    registry = TriggerRegistry()
    for tid in ("t3", "t1", "t2"):
        registry.arm(_event(tid, cue=tid))
    registry.retire("t2")
    assert [t.id for t in registry.live()] == ["t1", "t3"]


def test_state_histogram_accounts_for_every_trigger():
    registry = TriggerRegistry()
    registry.arm(_event("t1"))
    registry.arm(_event("t2", cue="x"))
    registry.retire("t2")
    assert sum(registry.state_histogram().values()) == len(registry) == 2


# ---------------------------------------------------------------- CORE


def _core_node(keys):
    return make_node(region=Region.CORE, level=0, vec=embed(tuple(keys)), keys=tuple(keys))


def test_identity_is_capped_at_admission_not_eroded_later():
    """N-06: if identity does not fit, that is a config error to raise."""
    tree = MemoryTree()
    core = CoreSet(quota_bytes=2 * D0_BYTES)
    core.admit(tree, _core_node(("careful",)), CoreLevel.TRAIT)
    core.admit(tree, _core_node(("curious",)), CoreLevel.TRAIT)
    with pytest.raises(CoreQuotaExceeded):
        core.admit(tree, _core_node(("stubborn",)), CoreLevel.TRAIT)


def test_only_core_nodes_go_in_core():
    tree = MemoryTree()
    core = CoreSet(quota_bytes=10 * D0_BYTES)
    archive = make_node(region=Region.ARCHIVE, level=1, vec=embed(("event",)))
    with pytest.raises(ValueError):
        core.admit(tree, archive, CoreLevel.TRAIT)


def test_zones_come_out_slowest_changing_first():
    """Prompt order, chosen so the prefix stays stable and cacheable."""
    tree = MemoryTree()
    core = CoreSet(quota_bytes=10 * D0_BYTES)
    core.admit(tree, _core_node(("my", "story")), CoreLevel.NARRATIVE)
    core.admit(tree, _core_node(("careful",)), CoreLevel.TRAIT)
    core.admit(tree, _core_node(("ship", "weekly")), CoreLevel.ADAPTATION)
    assert [z.level for z in core.zones(tree)] == [
        CoreLevel.TRAIT, CoreLevel.ADAPTATION, CoreLevel.NARRATIVE
    ]


def test_zone_order_is_stable_across_calls():
    tree = MemoryTree()
    core = CoreSet(quota_bytes=10 * D0_BYTES)
    for keys in (("b",), ("a",), ("c",)):
        core.admit(tree, _core_node(keys), CoreLevel.TRAIT)
    assert core.zones(tree) == core.zones(tree)


def test_resident_tokens_are_paid_every_tick_before_any_recall():
    tree = MemoryTree()
    core = CoreSet(quota_bytes=10 * D0_BYTES, tokens_per_node=32)
    core.admit(tree, _core_node(("careful",)), CoreLevel.TRAIT)
    core.admit(tree, _core_node(("my", "story")), CoreLevel.NARRATIVE)
    assert core.resident_tokens(tree) == 64


def test_empty_core_costs_nothing():
    tree = MemoryTree()
    core = CoreSet(quota_bytes=D0_BYTES)
    assert core.zones(tree) == ()
    assert core.resident_tokens(tree) == 0
