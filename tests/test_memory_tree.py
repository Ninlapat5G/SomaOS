"""WP-A3 -- tree structure and the depth axis.

The invariants under test are I1 (an address always resolves), I6 (recall
cost does not scale with how much has been experienced) and the N-03 rule
that the depth axis and the fidelity axis move independently.
"""
from __future__ import annotations

import numpy as np
import pytest

from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import (
    MIN_RETRIEVAL_STRENGTH,
    MemoryTree,
    UnknownAddress,
)
from somaos.broker.memory.vector import Grade, embed, encode


def _node(keys, level=ArchiveLevel.SPECIFIC_EVENT, region=Region.ARCHIVE, **kw):
    return make_node(region=region, level=int(level), vec=embed(tuple(keys)),
                     keys=tuple(keys), **kw)


def _tree_with_children(n=6, beam=4):
    tree = MemoryTree(beam=beam)
    root = _node(("work",), ArchiveLevel.GENERAL_EVENT)
    tree.insert(root)
    kids = [
        tree.insert(_node(("work", f"day{i}")), parent=root.addr, tick=i)
        for i in range(n)
    ]
    return tree, root.addr, kids


# ---------------------------------------------------------------- structure


def test_inserting_identical_content_twice_does_not_duplicate_it():
    tree = MemoryTree()
    first = tree.insert(_node(("cat",)))
    second = tree.insert(_node(("cat",)))
    assert first == second
    assert len(tree) == 1


def test_children_and_parents_are_linked_both_ways():
    tree, root, kids = _tree_with_children(3)
    assert set(tree.children_of(root)) == set(kids)
    for kid in kids:
        assert tree.get(kid).parent == root


def test_inserting_under_an_unknown_parent_is_refused():
    tree = MemoryTree()
    with pytest.raises(UnknownAddress):
        tree.insert(_node(("cat",)), parent="addr:nope")


def test_resolve_rejects_an_address_the_tree_never_issued():
    with pytest.raises(UnknownAddress):
        MemoryTree().resolve("addr:nope")


def test_exact_key_lookup_is_not_similarity():
    """SKILL and TRIGGER need 'this exact cue', not 'something like it'."""
    tree = MemoryTree()
    addr = tree.insert(_node(("morning", "coffee"), region=Region.SKILL))
    tree.insert(_node(("evening", "tea"), region=Region.SKILL))
    assert tree.by_key("coffee") == (addr,)
    assert tree.by_key("espresso") == ()


def test_walk_starts_at_the_general_event_level():
    tree = MemoryTree()
    general = tree.insert(_node(("trip",), ArchiveLevel.GENERAL_EVENT))
    tree.insert(_node(("trip", "detail"), ArchiveLevel.VERBATIM), parent=general)
    tree.insert(_node(("chapter",), ArchiveLevel.LIFETIME_PERIOD))
    assert tree.entry_points() == (general,)


# ---------------------------------------------------------------- depth axis (N-03)


def test_using_a_memory_makes_it_easier_to_reach():
    tree, root, kids = _tree_with_children(3)
    before = tree.retrieval_strength(kids[0], tick=500)
    tree.touch(kids[0], tick=500)
    assert tree.retrieval_strength(kids[0], tick=500) > before


def test_disuse_makes_a_memory_harder_to_reach_but_never_unreachable():
    tree, root, kids = _tree_with_children(2)
    cold = tree.retrieval_strength(kids[0], tick=100_000)
    assert cold == pytest.approx(MIN_RETRIEVAL_STRENGTH)
    assert cold > 0.0  # deep is allowed; gone is not (N-01)
    assert tree.resolve(kids[0]).node.addr == kids[0]


def test_a_cold_memory_falls_below_a_warm_one_of_equal_relevance():
    """This is what 'harder to recall' means mechanically."""
    tree = MemoryTree(beam=1)
    root = _node(("topic",), ArchiveLevel.GENERAL_EVENT)
    tree.insert(root)
    # Identical keys, so identical similarity to the cue: only strength differs.
    warm = tree.insert(_node(("topic", "a")), parent=root.addr)
    cold = tree.insert(_node(("topic", "b")), parent=root.addr)
    tree.touch(warm, tick=900)
    ranked = tree.rank_children(root.addr, embed(("topic",)), tick=900)
    assert [addr for addr, _ in ranked] == [warm]
    # But widening the beam -- spending more ops -- still finds it.
    wider = tree.rank_children(root.addr, embed(("topic",)), tick=900, beam=8)
    assert cold in {addr for addr, _ in wider}


def test_recall_does_not_restore_detail_that_dilution_removed():
    """Retrieval practice lifts accessibility only (Bjork); fidelity is
    the other axis and must not move."""
    tree, root, kids = _tree_with_children(2)
    original = tree.get(kids[0]).vec
    faded = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=encode(original, Grade.D2_BINARY), grade=Grade.D2_BINARY,
        original=original, parent=root, keys=tree.get(kids[0]).keys,
    )
    new_addr = tree.replace_node(kids[0], faded)
    before = tree.resolve(new_addr).fidelity
    tree.touch(new_addr, tick=50)
    assert tree.resolve(new_addr).fidelity == before


def test_usage_statistics_survive_dilution():
    """A faded memory should not look freshly minted."""
    tree, root, kids = _tree_with_children(2)
    tree.touch(kids[0], tick=1)
    tree.touch(kids[0], tick=2)
    original = tree.get(kids[0]).vec
    faded = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=encode(original, Grade.D1_INT8), grade=Grade.D1_INT8,
        original=original, parent=root, keys=tree.get(kids[0]).keys,
    )
    new_addr = tree.replace_node(kids[0], faded)
    assert tree.stat(new_addr).use_count == 2


# ---------------------------------------------------------------- bounded walk (I6)


def test_ranking_never_returns_more_than_the_beam():
    tree, root, _ = _tree_with_children(50, beam=4)
    assert len(tree.rank_children(root, embed(("work",)), tick=0)) == 4


def test_ranking_is_deterministic_including_ties():
    tree, root, _ = _tree_with_children(20)
    cue = embed(("work",))
    first = tree.rank_children(root, cue, tick=7)
    second = tree.rank_children(root, cue, tick=7)
    assert first == second


def test_ranking_cost_does_not_grow_with_unrelated_memories():
    """I6: the walk touches the beam, not the store."""
    small, root_s, _ = _tree_with_children(5)
    big, root_b, _ = _tree_with_children(5)
    for i in range(500):  # unrelated experience piled up elsewhere
        big.insert(_node(("noise", f"n{i}"), ArchiveLevel.GENERAL_EVENT))
    cue = embed(("work",))
    assert len(big.rank_children(root_b, cue, tick=0)) == len(
        small.rank_children(root_s, cue, tick=0)
    )


def test_relevance_still_dominates_among_equally_warm_siblings():
    tree = MemoryTree(beam=1)
    root = _node(("topic",), ArchiveLevel.GENERAL_EVENT)
    tree.insert(root)
    match = tree.insert(_node(("topic", "rain")), parent=root.addr)
    tree.insert(_node(("topic", "sunshine")), parent=root.addr)
    ranked = tree.rank_children(root.addr, embed(("topic", "rain")), tick=0)
    assert ranked[0][0] == match


# ---------------------------------------------------------------- rewriting (I1, I7)


def test_a_diluted_address_still_answers():
    tree, root, kids = _tree_with_children(2)
    original = tree.get(kids[0]).vec
    faded = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=encode(original, Grade.D2_BINARY), grade=Grade.D2_BINARY,
        original=original, parent=root, keys=tree.get(kids[0]).keys,
    )
    tree.replace_node(kids[0], faded)
    resolved = tree.resolve(kids[0])
    assert resolved.hops == 1
    assert not resolved.is_original
    assert 0.7 < resolved.fidelity < 0.9


def test_replacement_that_would_raise_fidelity_is_refused():
    """I7: the ladder is one-way."""
    tree, root, kids = _tree_with_children(2)
    original = tree.get(kids[0]).vec
    faded = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=encode(original, Grade.D2_BINARY), grade=Grade.D2_BINARY,
        original=original, parent=root, keys=tree.get(kids[0]).keys,
    )
    addr = tree.replace_node(kids[0], faded)
    restored = make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=original, parent=root, keys=tree.get(addr).keys,
    )
    with pytest.raises(ValueError):
        tree.replace_node(addr, restored)


def test_dilution_shrinks_the_store():
    tree, root, kids = _tree_with_children(4)
    before = tree.store_bytes()
    for kid in kids:
        v = tree.get(kid).vec
        tree.replace_node(kid, make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=encode(v, Grade.D1_INT8), grade=Grade.D1_INT8, original=v,
            parent=root, keys=tree.get(kid).keys,
        ))
    assert tree.store_bytes() == pytest.approx(before - 4 * 768, abs=8)


def test_children_follow_a_replaced_parent():
    tree = MemoryTree()
    parent = tree.insert(_node(("trip",), ArchiveLevel.GENERAL_EVENT))
    child = tree.insert(_node(("trip", "day1")), parent=parent)
    v = tree.get(parent).vec
    new_parent = tree.replace_node(parent, make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=encode(v, Grade.D1_INT8), grade=Grade.D1_INT8, original=v,
        keys=("trip",),
    ))
    assert tree.get(child).parent == new_parent
    assert child in tree.children_of(new_parent)


# ---------------------------------------------------------------- dissolving


def test_a_dissolved_memory_answers_through_its_parent():
    tree, root, kids = _tree_with_children(3)
    parent = tree.dissolve_into_parent(kids[0])
    # Absorbing the child moved the parent, so the parent has a new address;
    # the old one still forwards there, which is the point.
    assert tree.resolve(kids[0]).node.addr == parent
    assert tree.alias.resolve(root) == parent


def test_counting_away_a_memory_leaves_a_tally_not_a_hole():
    """D4 is the floor: 'things like this happened here, n times'."""
    tree, root, kids = _tree_with_children(3)
    tree.dissolve_into_parent(kids[0], counted=True)
    tree.dissolve_into_parent(kids[1], counted=True)
    assert tree.counters[root] == 2
    assert tree.resolve(kids[1]).node.addr == root


def test_grandchildren_are_re_parented_never_orphaned():
    tree = MemoryTree()
    root = tree.insert(_node(("life",), ArchiveLevel.LIFETIME_PERIOD))
    mid = tree.insert(_node(("trip",), ArchiveLevel.GENERAL_EVENT), parent=root)
    leaf = tree.insert(_node(("trip", "photo"), ArchiveLevel.VERBATIM), parent=mid)
    surviving_root = tree.dissolve_into_parent(mid)
    assert tree.get(leaf).parent == surviving_root
    assert leaf in tree.children_of(surviving_root)
    assert tree.resolve(leaf).node.addr == leaf  # untouched, still itself


def test_a_rootless_node_cannot_be_dissolved():
    """Nothing may be dissolved into nowhere."""
    tree = MemoryTree()
    lone = tree.insert(_node(("orphan",), ArchiveLevel.GENERAL_EVENT))
    with pytest.raises(ValueError):
        tree.dissolve_into_parent(lone)


@pytest.mark.parametrize("region", [Region.CORE, Region.TRIGGER])
def test_identity_and_intent_cannot_be_dissolved(region):
    tree = MemoryTree()
    root = tree.insert(_node(("self",), ArchiveLevel.NARRATIVE, region=region))
    child = tree.insert(_node(("self", "trait"), region=region), parent=root)
    with pytest.raises(ValueError):
        tree.dissolve_into_parent(child)


def test_every_address_ever_issued_still_resolves_after_heavy_churn():
    """I1 end to end: dilute and dissolve repeatedly, then check them all."""
    tree, root, kids = _tree_with_children(12)
    issued = list(kids)
    for kid in kids[:6]:
        v = tree.get(kid).vec
        issued.append(tree.replace_node(kid, make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=encode(v, Grade.D2_BINARY), grade=Grade.D2_BINARY, original=v,
            parent=root, keys=tree.get(kid).keys,
        )))
    for addr in issued[:3]:
        if addr in tree:
            tree.dissolve_into_parent(addr, counted=True)
    for addr in issued:
        assert tree.resolve(addr) is not None


# ---------------------------------------------------------------- accounting


def test_region_bytes_sum_to_the_store_total():
    tree = MemoryTree()
    tree.insert(_node(("who",), region=Region.CORE))
    tree.insert(_node(("when",), region=Region.TRIGGER))
    tree.insert(_node(("how",), region=Region.SKILL))
    tree.insert(_node(("what",), region=Region.ARCHIVE))
    assert sum(tree.region_bytes(r) for r in Region) == tree.store_bytes()


def test_grade_histogram_tracks_the_ladder():
    tree, root, kids = _tree_with_children(3)
    assert tree.grade_histogram()["D0_EXACT"] == 4
    v = tree.get(kids[0]).vec
    tree.replace_node(kids[0], make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=encode(v, Grade.D1_INT8), grade=Grade.D1_INT8, original=v,
        parent=root, keys=tree.get(kids[0]).keys,
    ))
    hist = tree.grade_histogram()
    assert hist["D0_EXACT"] == 3 and hist["D1_INT8"] == 1


def test_absorbing_a_child_moves_the_parent_and_forwards_its_address():
    """The Merkle consequence: a parent that now stands for more is a
    different parent, and everyone holding the old address is forwarded."""
    tree, root, kids = _tree_with_children(3)
    parent = tree.dissolve_into_parent(kids[0])
    assert parent != root
    assert tree.alias.resolve(root) == parent
    assert tree.get(parent).n_merged == 2
    for kid in kids[1:]:
        assert tree.get(kid).parent == parent


def test_a_collapsed_fidelity_bound_does_not_mean_the_memory_is_gone():
    """The bound is a worst case; reachability is not negotiable (N-01)."""
    tree, root, kids = _tree_with_children(6)
    for kid in kids[:5]:
        if kid in tree:
            tree.dissolve_into_parent(kid)
    for kid in kids[:5]:
        resolved = tree.resolve(kid)
        assert resolved.node is not None
        assert 0.0 <= resolved.fidelity <= 1.0
