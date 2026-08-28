"""WP-A7 -- consolidation: repetition becoming structure.

The interesting tests here are the negative ones. Crystallising too
eagerly is worse than not crystallising at all, because a wrong habit does
not sit inert -- it is resident, it is undilutable, and it shapes every
subsequent decision.
"""
from __future__ import annotations

import pytest

from somaos.broker.consolidation import ConsolidationMachine, ConsolidationPhase
from somaos.broker.consolidation.machine import MAX_CHILDREN
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import DEFAULT_DIM, Grade, embed

D0_BYTES = DEFAULT_DIM * 4
ROOMY = 10 ** 7


def _machine(budget=ROOMY, **kw):
    return ConsolidationMachine(dilution=DilutionEngine(store_budget_bytes=budget), **kw)


def _general(tree, topic):
    node = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=embed((topic,)), keys=(topic,),
    )
    return tree.insert(node)


def _episode(tree, parent, keys, tick):
    return tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=embed(tuple(keys)), keys=tuple(keys), span=(tick, tick),
    ), parent=parent, tick=tick)


def _routine(tree, topic="morning", extra=("coffee", "desk"), times=6):
    parent = _general(tree, topic)
    for tick in range(times):
        _episode(tree, parent, (topic, *extra), tick)
    return parent


def _grab_bag(tree, topic="misc", items=("taxes", "rain", "film", "argument", "recipe")):
    parent = _general(tree, topic)
    for tick, item in enumerate(items):
        _episode(tree, parent, (topic, item), tick)
    return parent


# ---------------------------------------------------------------- occurrences


def test_living_through_the_same_thing_twice_is_counted_not_discarded():
    """Content addressing dedupes storage; it must not dedupe history."""
    tree = MemoryTree()
    parent = _general(tree, "morning")
    first = _episode(tree, parent, ("morning", "coffee"), 0)
    second = _episode(tree, parent, ("morning", "coffee"), 1)
    assert first == second
    assert len(tree) == 2  # the parent and one episode
    assert tree.occurrences(first) == 2


def test_repetition_widens_the_span_it_covers():
    tree = MemoryTree()
    parent = _general(tree, "morning")
    for tick in (3, 9, 1):
        addr = _episode(tree, parent, ("morning", "coffee"), tick)
    assert tree.get(addr).span == (1, 9)


def test_repetition_makes_a_memory_more_available():
    tree = MemoryTree()
    parent = _general(tree, "morning")
    addr = _episode(tree, parent, ("morning", "coffee"), 0)
    faded = tree.retrieval_strength(addr, tick=5000)
    _episode(tree, parent, ("morning", "coffee"), 5000)
    assert tree.retrieval_strength(addr, tick=5000) > faded


def test_occurrences_are_not_recollections():
    """Living through something and remembering it are different events."""
    tree = MemoryTree()
    parent = _general(tree, "morning")
    addr = _episode(tree, parent, ("morning", "coffee"), 0)
    _episode(tree, parent, ("morning", "coffee"), 1)
    assert tree.occurrences(addr) == 2
    assert tree.stat(addr).use_count == 0


# ---------------------------------------------------------------- crystallising


def test_a_repeated_routine_becomes_a_habit():
    tree = MemoryTree()
    _routine(tree)
    report = _machine().run(tree, tick=20)
    assert len(report.crystallised) == 1
    habit = report.crystallised[0]
    assert set(habit.keys) == {"coffee", "desk"}
    assert habit.occurrences == 6


def test_a_habit_lands_in_skill_not_archive():
    """Habits are reached by situation, not by topic similarity."""
    tree = MemoryTree()
    _routine(tree)
    report = _machine().run(tree, tick=20)
    node = tree.get(report.crystallised[0].addr)
    assert node.region is Region.SKILL
    assert tree.by_key("coffee")


def test_the_habit_keeps_pointers_to_what_it_was_drawn_from():
    """A habit must be answerable for itself; nothing is asserted from nowhere."""
    tree = MemoryTree()
    _routine(tree)
    habit = _machine().run(tree, tick=20).crystallised[0]
    assert habit.from_addrs
    for addr in habit.from_addrs:
        assert tree.resolve(addr) is not None


# ---------------------------------------------------------------- not crystallising


def test_a_grab_bag_under_one_topic_is_not_a_habit():
    """The bug this test exists for: every child of a topic points partly
    along that topic, so unfiltered similarity called five unrelated
    errands a personality trait."""
    tree = MemoryTree()
    _grab_bag(tree)
    assert _machine().run(tree, tick=20).crystallised == []


def test_doing_something_a_couple_of_times_is_not_a_habit():
    tree = MemoryTree()
    _routine(tree, times=2)
    assert _machine(min_repeats=4).run(tree, tick=20).crystallised == []


def test_sharing_only_the_parents_own_topic_is_not_a_habit():
    """If the only thing in common is the subject, that is the subject."""
    tree = MemoryTree()
    parent = _general(tree, "work")
    for tick, task in enumerate(("a", "b", "c", "d", "e", "f")):
        _episode(tree, parent, ("work", task), tick)
    assert _machine().run(tree, tick=20).crystallised == []


def test_one_vivid_memory_repeated_never_is_not_a_habit():
    tree = MemoryTree()
    parent = _general(tree, "trip")
    _episode(tree, parent, ("trip", "the aurora"), 0)
    assert _machine().run(tree, tick=20).crystallised == []


def test_crystallising_is_idempotent():
    """Content addressing makes the second pass free, and it must stay free:
    a habit that re-crystallises every cycle would multiply without bound."""
    tree = MemoryTree()
    _routine(tree)
    machine = _machine()
    assert len(machine.run(tree, tick=20).crystallised) == 1
    assert machine.run(tree, tick=21).crystallised == []
    assert machine.run(tree, tick=22).crystallised == []


def test_crystallising_is_deterministic():
    def run():
        tree = MemoryTree()
        _routine(tree, topic="morning")
        _routine(tree, topic="evening", extra=("walk", "river"))
        _grab_bag(tree)
        report = _machine().run(tree, tick=30)
        return sorted((c.keys, c.occurrences) for c in report.crystallised)

    assert run() == run()


# ---------------------------------------------------------------- rebalance


def test_a_node_wider_than_the_beam_gets_split():
    """Width is where a bounded walk quietly becomes a lossy one."""
    tree = MemoryTree(beam=4)
    parent = _general(tree, "busy")
    for tick in range(MAX_CHILDREN + 6):
        _episode(tree, parent, ("busy", f"e{tick}"), tick)
    report = _machine().run(tree, tick=100)
    assert report.split
    assert len(tree.children_of(parent)) <= MAX_CHILDREN + 1


def test_splitting_never_makes_anything_unreachable():
    tree = MemoryTree(beam=4)
    parent = _general(tree, "busy")
    kids = [_episode(tree, parent, ("busy", f"e{i}"), i) for i in range(MAX_CHILDREN + 6)]
    _machine().run(tree, tick=100)
    for kid in kids:
        assert tree.resolve(kid) is not None
        assert tree.depth_of(kid) >= 1


def test_a_narrow_node_is_left_alone():
    tree = MemoryTree()
    parent = _general(tree, "quiet")
    for tick in range(3):
        _episode(tree, parent, ("quiet", f"e{tick}"), tick)
    assert _machine().run(tree, tick=100).split == []


def test_reparenting_refuses_to_build_a_cycle():
    tree = MemoryTree()
    parent = _general(tree, "topic")
    child = _episode(tree, parent, ("topic", "x"), 0)
    with pytest.raises(ValueError):
        tree.reparent(parent, child)
    with pytest.raises(ValueError):
        tree.reparent(child, child)


def test_reparenting_leaves_the_node_itself_untouched():
    """A change to the tree's shape is not a change to any memory in it."""
    tree = MemoryTree()
    a, b = _general(tree, "a"), _general(tree, "b")
    child = _episode(tree, a, ("a", "x"), 0)
    before = tree.get(child).vec.tobytes()
    tree.reparent(child, b)
    assert tree.get(child).addr == child
    assert tree.get(child).vec.tobytes() == before
    assert child in tree.children_of(b)
    assert child not in tree.children_of(a)


# ---------------------------------------------------------------- the cycle


def test_phases_run_in_order_and_return_to_awake():
    tree = MemoryTree()
    _routine(tree)
    machine = _machine()
    assert machine.phase is ConsolidationPhase.AWAKE
    machine.run(tree, tick=20)
    assert machine.phase is ConsolidationPhase.AWAKE


def test_abstraction_happens_before_the_budget_bites():
    """Order matters: a tight budget should cost detail, not cost the agent
    the ability to learn who it is."""
    tree = MemoryTree()
    _routine(tree, times=8)
    _grab_bag(tree)
    # Tight enough that the store cannot hold what it has at full precision,
    # so ENFORCE has to act after ABSTRACT has already run.
    budget = 3 * D0_BYTES
    report = _machine(budget=budget).run(tree, tick=20)
    assert report.crystallised          # the habit was formed
    assert report.diluted               # and the budget still bit
    assert tree.store_bytes() <= budget


def test_replay_only_looks_at_the_window():
    tree = MemoryTree()
    parent = _general(tree, "old")
    _episode(tree, parent, ("old", "thing"), 0)
    recent = _machine().run(tree, tick=10_000, window=100).replayed
    assert recent == 0


def test_the_report_serialises_for_the_record():
    tree = MemoryTree()
    _routine(tree)
    blob = _machine().run(tree, tick=20).to_jsonable()
    assert blob["crystallised"][0]["keys"]
    assert set(blob) >= {"tick", "replayed", "crystallised", "split", "diluted",
                         "bytes_before", "bytes_after"}


def test_consolidation_never_loses_an_address():
    tree = MemoryTree(beam=4)
    _routine(tree, times=8)
    _grab_bag(tree)
    parent = _general(tree, "busy")
    kids = [_episode(tree, parent, ("busy", f"e{i}"), i) for i in range(MAX_CHILDREN + 4)]
    everything = list(tree.addresses())
    machine = _machine(budget=6 * D0_BYTES)
    for tick in (100, 200, 300):
        machine.run(tree, tick=tick)
    for addr in everything + kids:
        assert tree.resolve(addr) is not None
