"""End-to-end: the five modules composing into something that behaves like memory.

Every module here passes its own unit tests. This file exists because that
was not enough -- running them together for a couple of hundred simulated
days surfaced four bugs that no single-module test could see, and each of
them ended in the same place: a reclaim loop that never terminated.

The regression tests below pin those four. The simulation test pins the
behaviour they were breaking.
"""
from __future__ import annotations

import pytest

from somaos.bench.experiments.agent_life import live
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import AddressCollision, MemoryTree, UnknownAddress
from somaos.broker.memory.vector import Grade, embed, encode


def _node(keys, level=ArchiveLevel.SPECIFIC_EVENT, **kw):
    return make_node(region=Region.ARCHIVE, level=int(level),
                     vec=embed(tuple(keys)), keys=tuple(keys), **kw)


def _fade(tree, addr, grade=Grade.D2_BINARY):
    node = tree.get(addr)
    return tree.replace_node(addr, make_node(
        region=node.region, level=node.level,
        vec=encode(node.vec, grade), grade=grade, original=node.vec,
        parent=node.parent, keys=node.keys,
    ))


# ------------------------------------------------------- regressions


def test_a_retired_address_is_never_brought_back_to_life():
    """Perceiving content that has since faded is a repeat of what it became.

    Re-inserting it at its old address would leave that address both live
    and forwarded, so resolve() would answer with one node while everything
    else operated on another. It surfaced as consolidation re-crystallising
    the same habit forever.
    """
    tree = MemoryTree()
    original = tree.insert(_node(("morning", "coffee")))
    faded = _fade(tree, original)

    again = tree.insert(_node(("morning", "coffee")))

    assert again == faded
    assert tree.get(original) is None
    assert tree.resolve(original).node.addr == faded
    assert tree.occurrences(faded) == 2


def test_two_memories_that_fade_into_the_same_thing_become_one():
    """Not a failure mode -- this is dilution working.

    Once two experiences quantise to the same vector nothing distinguishes
    them, and keeping two nodes no walk could tell apart would be a lie.
    """
    tree = MemoryTree()
    a = tree.insert(_node(("shared", "one")))
    b = tree.insert(_node(("shared", "two")))
    tree.touch(a, tick=1)

    faded_a = _fade(tree, a)
    node_b = tree.get(b)
    survivor = tree.replace_node(b, make_node(
        region=node_b.region, level=node_b.level,
        vec=tree.get(faded_a).vec, grade=Grade.D2_BINARY, original=node_b.vec,
        keys=node_b.keys,
    ))

    assert survivor == faded_a
    assert tree.resolve(a).node.addr == survivor
    assert tree.resolve(b).node.addr == survivor
    assert tree.occurrences(survivor) == 2
    assert tree.stat(survivor).use_count == 1  # history carried across


def test_a_node_can_never_become_its_own_ancestor():
    """The collision that has to be refused rather than merged."""
    tree = MemoryTree()
    parent = tree.insert(_node(("topic",), ArchiveLevel.GENERAL_EVENT))
    child = tree.insert(_node(("topic", "x")), parent=parent)
    child_node = tree.get(child)

    colliding = make_node(
        region=child_node.region, level=child_node.level,
        vec=child_node.vec, grade=child_node.grade, keys=child_node.keys,
    )
    with pytest.raises(AddressCollision):
        tree.replace_node(parent, colliding)

    assert tree.get(child).parent == parent
    assert tree.depth_of(child) == 1


def test_a_forwarding_chain_that_ends_nowhere_is_reported_loudly():
    tree = MemoryTree()
    addr = tree.insert(_node(("thing",)))
    tree.alias.add(addr, "addr:nowhere")
    with pytest.raises(UnknownAddress):
        tree.insert(_node(("thing",)))


def test_reclaim_gives_up_instead_of_spinning():
    """Termination is a property of the loop, not of every rung being right.

    A step that neither frees bytes nor retires its target would be chosen
    again immediately, forever. The loop sets such a victim aside, so a bug
    in a rung costs a wasted step rather than a hung agent.
    """
    tree = MemoryTree()
    root = tree.insert(_node(("topic",), ArchiveLevel.GENERAL_EVENT))
    for i in range(30):
        tree.insert(_node(("topic", f"e{i}")), parent=root, tick=i)

    engine = DilutionEngine(store_budget_bytes=1)
    events = engine.enforce(tree, tick=500)  # unsatisfiable on purpose
    assert events
    assert engine.enforce(tree, tick=501) is not None  # and it returns


def test_practice_protects_a_memory_from_fading():
    """Bjork: retrieval practice raises storage strength, not just access.

    Retrieval strength decays, so on its own it let a memory recalled a
    dozen times be dissolved once everything around it was gone -- the
    opposite of what should happen under pressure.
    """
    tree = MemoryTree()
    root = tree.insert(_node(("topic",), ArchiveLevel.GENERAL_EVENT))
    kids = [tree.insert(_node(("topic", f"e{i}")), parent=root, tick=i) for i in range(8)]
    cherished = kids[3]
    for tick in range(1, 10):
        tree.touch(cherished, tick=tick * 50)

    DilutionEngine(store_budget_bytes=4 * 1024).enforce(tree, tick=1000)

    cherished_grade = tree.resolve(cherished).node.grade
    others = [tree.resolve(k).node.grade for k in kids if k != cherished]
    assert cherished_grade <= min(others)


# ------------------------------------------------------- the simulation


@pytest.fixture(scope="module")
def life():
    return live(days=200)


def test_nothing_is_ever_lost(life):
    assert life["all_addresses_resolve"]
    assert life["addresses_issued"] > 500


def test_the_store_stays_within_its_budget(life):
    assert life["over_budget_after_any_cycle"] == []
    assert life["store_bytes"] <= life["store_budget_bytes"]


def test_routines_become_habits_and_one_offs_do_not(life):
    habits = {name.split(":")[1].split("(")[0].strip() for name, _ in life["habits_formed"]}
    assert habits == {"coffee + inbox", "river + walk"}
    assert all(count >= 15 for _, count in life["habits_formed"])


def test_a_day_the_agent_keeps_returning_to_stays_sharp(life):
    landmark = life["landmark"]
    assert landmark["recollections"] > 5
    assert landmark["retrieval_strength"] > 0.9
    assert landmark["still_found_by_a_cold_walk"]
    assert landmark["grade"] in ("D0_EXACT", "D1_INT8")


def test_a_day_never_revisited_fades_without_disappearing(life):
    stale = life["never_revisited_early_day"]
    assert stale["still_resolves"]
    assert stale["grade"] in ("D1_INT8", "D2_BINARY", "D3_MERGED", "D4_COUNTER")
    # Detail is gone; what it was about is not.
    assert stale["still_about_its_topic"] > 0.3


def test_pressure_starts_a_cycle_not_only_the_clock(life):
    """A store that reclaims only on a timer overshoots for as long as the
    timer has left to run, which is not a budget."""
    assert life["cycles_by_reason"]["pressure"] > 0
