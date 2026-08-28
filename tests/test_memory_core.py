"""WP-A1 -- memory core invariants (plans/03_MEMORY_ARCHITECTURE.md section 7).

Covers I1 (an address always resolves), I5 (dilution is reproducible),
I7 (fidelity only falls, grade only advances) and the N-06 rule that
identity and intent are never diluted.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from somaos.broker.memory.address import AliasCycle, AliasTable, content_address
from somaos.broker.memory.node import (
    MAX_GRADE,
    UNDILUTABLE,
    ArchiveLevel,
    CoreLevel,
    Region,
    address_of,
    make_node,
)
from somaos.broker.memory.vector import (
    DEFAULT_DIM,
    Grade,
    GradeError,
    cue_vector,
    embed,
    encode,
    fidelity_of,
    nbytes,
    similarity,
)


# ---------------------------------------------------------------- embedding


def test_embedding_is_deterministic_across_calls():
    assert np.array_equal(embed(("cat", "pet")), embed(("cat", "pet")))


def test_embedding_ignores_key_order_and_duplicates():
    a = embed(("cat", "pet"))
    assert np.array_equal(a, embed(("pet", "cat")))
    assert np.array_equal(a, embed(("cat", "pet", "cat")))


def test_shared_keys_are_closer_than_disjoint_ones():
    shared = similarity(embed(("cat", "pet")), embed(("cat", "dog")))
    disjoint = similarity(embed(("cat", "pet")), embed(("rocket", "orbit")))
    assert shared > disjoint


def test_empty_key_set_gives_a_zero_vector_with_zero_similarity():
    empty = embed(())
    assert not empty.any()
    # A cue with no content must not accidentally match everything.
    assert similarity(empty, embed(("cat",))) == 0.0


def test_cue_vector_matches_embedding_of_the_same_keys():
    assert np.array_equal(
        cue_vector(("weather",), ("bangkok",)), embed(("weather", "bangkok"))
    )


# ---------------------------------------------------------------- grades


def test_bytes_fall_by_the_expected_factors():
    v = embed(("a", "b"))
    assert nbytes(v, Grade.D0_EXACT) == DEFAULT_DIM * 4
    assert nbytes(v, Grade.D1_INT8) == DEFAULT_DIM
    assert nbytes(v, Grade.D2_BINARY) == DEFAULT_DIM // 8
    # Merged and counted nodes carry no vector, so they cost the budget nothing.
    assert nbytes(v, Grade.D3_MERGED) == 0
    assert nbytes(v, Grade.D4_COUNTER) == 0


def test_int8_is_near_lossless_but_binary_is_not():
    """The whole ladder rests on these two behaving differently."""
    v = embed(("cat", "pet", "home"))
    assert fidelity_of(v, encode(v, Grade.D1_INT8)) > 0.999
    assert 0.7 < fidelity_of(v, encode(v, Grade.D2_BINARY)) < 0.9


def test_binary_fidelity_matches_the_analytic_value():
    """For an isotropic vector, E[cos(v, sign(v))] = sqrt(2/pi).

    Checking against the closed form rather than a recorded number means
    this test fails if the sign encoding is wrong, not merely if it changed.
    """
    fids = [
        fidelity_of(v, encode(v, Grade.D2_BINARY))
        for v in (embed((f"k{i}", f"j{i}")) for i in range(60))
    ]
    assert float(np.mean(fids)) == pytest.approx(math.sqrt(2 / math.pi), abs=0.02)


def test_binary_encoding_has_no_third_symbol():
    """Zeros must land on +1; a 1-bit code cannot afford a third value."""
    v = np.zeros(16, dtype=np.float32)
    v[:8] = -0.5
    encoded = encode(v, Grade.D2_BINARY)
    assert set(np.unique(encoded).tolist()) == {-1, 1}


def test_binary_keeps_the_category_even_though_it_loses_the_instance():
    """The reason binary is the rung where an item becomes a category.

    Full measurements live in
    somaos/bench/experiments/quantization_fidelity.py; this pins the
    qualitative claim the architecture depends on.
    """
    query = embed(("cat", "pet"))
    near = embed(("cat", "pet", "home"))
    far = embed(("rocket", "orbit"))
    qb, nb_, fb = (encode(x, Grade.D2_BINARY) for x in (query, near, far))
    assert similarity(qb, nb_) > similarity(qb, fb)


@pytest.mark.parametrize("grade", [Grade.D3_MERGED, Grade.D4_COUNTER])
def test_gradeless_levels_refuse_to_encode(grade):
    with pytest.raises(GradeError):
        encode(embed(("a",)), grade)


def test_fidelity_never_goes_negative():
    v = embed(("a", "b"))
    assert fidelity_of(v, -v) == 0.0


# ---------------------------------------------------------------- addressing


def test_same_content_gives_the_same_address():
    v = embed(("cat",))
    kw = dict(vec=v, grade=Grade.D0_EXACT, level=1, region="ARCHIVE")
    assert content_address(**kw) == content_address(**kw)


def test_child_order_does_not_fork_the_address_space():
    v = embed(("cat",))
    kw = dict(vec=v, grade=Grade.D0_EXACT, level=2, region="ARCHIVE")
    assert content_address(children=("a", "b"), **kw) == content_address(
        children=("b", "a"), **kw
    )


def test_changing_a_child_changes_the_parent_address():
    """The Merkle property: tampering anywhere is visible at the root."""
    v = embed(("cat",))
    kw = dict(vec=v, grade=Grade.D0_EXACT, level=2, region="ARCHIVE")
    assert content_address(children=("a", "b"), **kw) != content_address(
        children=("a", "c"), **kw
    )


def test_grade_is_part_of_the_address():
    v = embed(("cat",))
    exact = content_address(vec=v, grade=Grade.D0_EXACT, level=0, region="ARCHIVE")
    binary = content_address(
        vec=encode(v, Grade.D2_BINARY), grade=Grade.D2_BINARY, level=0, region="ARCHIVE"
    )
    assert exact != binary


def test_node_address_matches_its_content():
    node = make_node(region=Region.ARCHIVE, level=1, vec=embed(("cat",)))
    assert node.addr == address_of(node)


# ---------------------------------------------------------------- alias table (I1)


def test_an_address_with_no_alias_resolves_to_itself():
    assert AliasTable().resolve("addr:deadbeef") == "addr:deadbeef"


def test_resolve_follows_a_chain_to_the_end():
    table = AliasTable()
    table.add("a", "b")
    table.add("b", "c")
    table.add("c", "d")
    assert table.resolve("a") == "d"
    assert table.chain("a") == ("a", "b", "c", "d")


def test_every_address_ever_written_still_resolves_after_many_dilutions():
    """I1: the invariant the whole design rests on."""
    table = AliasTable()
    history = ["addr:0"]
    for step in range(1, 200):
        nxt = f"addr:{step}"
        table.add(history[-1], nxt)
        history.append(nxt)
    for addr in history:
        assert table.resolve(addr) == history[-1]


def test_resolve_is_stable_when_the_chain_grows_underneath_it():
    """Memoisation must not pin an answer that dilution has moved on from."""
    table = AliasTable()
    table.add("a", "b")
    assert table.resolve("a") == "b"
    table.add("b", "c")
    assert table.resolve("a") == "c"


def test_repointing_an_existing_alias_is_refused():
    table = AliasTable()
    table.add("a", "b")
    table.add("a", "b")  # idempotent
    with pytest.raises(ValueError):
        table.add("a", "c")


def test_self_alias_is_a_noop_not_a_cycle():
    table = AliasTable()
    table.add("a", "a")
    assert table.resolve("a") == "a"
    assert len(table) == 0


def test_a_corrupt_cycle_is_reported_not_hung():
    table = AliasTable()
    table.add("a", "b")
    table._links["b"] = "a"  # only reachable by corruption; add() would refuse
    with pytest.raises(AliasCycle):
        table.resolve("a")


def test_links_are_a_copy_so_callers_cannot_mutate_history():
    table = AliasTable()
    table.add("a", "b")
    table.links["a"] = "z"
    assert table.resolve("a") == "b"


# ---------------------------------------------------------------- region rules (N-06)


@pytest.mark.parametrize("region", sorted(UNDILUTABLE))
def test_identity_and_intent_are_never_dilutable(region):
    node = make_node(region=region, level=0, vec=embed(("who-i-am",)))
    for grade in (Grade.D1_INT8, Grade.D2_BINARY, Grade.D3_MERGED, Grade.D4_COUNTER):
        assert not node.may_dilute_to(grade)


def test_dilution_only_moves_forward(): 
    """I7: grade advances, never retreats."""
    v = embed(("cat",))
    node = make_node(
        region=Region.ARCHIVE, level=1, vec=encode(v, Grade.D1_INT8),
        grade=Grade.D1_INT8, original=v,
    )
    assert node.may_dilute_to(Grade.D2_BINARY)
    assert not node.may_dilute_to(Grade.D1_INT8)
    assert not node.may_dilute_to(Grade.D0_EXACT)


def test_each_region_stops_at_its_own_floor():
    v = embed(("cat",))
    skill = make_node(region=Region.SKILL, level=0, vec=v)
    archive = make_node(region=Region.ARCHIVE, level=0, vec=v)
    assert skill.may_dilute_to(MAX_GRADE[Region.SKILL])
    assert not skill.may_dilute_to(Grade.D4_COUNTER)
    assert archive.may_dilute_to(Grade.D4_COUNTER)


def test_walk_entry_is_the_general_event_level():
    """Conway: generative retrieval starts mid-hierarchy, not at the root."""
    from somaos.broker.memory.node import WALK_ENTRY_LEVEL

    assert WALK_ENTRY_LEVEL == int(ArchiveLevel.GENERAL_EVENT)
    assert ArchiveLevel.VERBATIM < ArchiveLevel.GENERAL_EVENT < ArchiveLevel.NARRATIVE


def test_core_levels_are_ordered_by_rate_of_change():
    assert CoreLevel.TRAIT < CoreLevel.ADAPTATION < CoreLevel.NARRATIVE


# ---------------------------------------------------------------- reproducibility (I5)


def test_dilution_is_reproducible():
    v = embed(("cat", "pet"))
    first = make_node(
        region=Region.ARCHIVE, level=1, vec=encode(v, Grade.D2_BINARY),
        grade=Grade.D2_BINARY, original=v, keys=("cat", "pet"),
    )
    second = make_node(
        region=Region.ARCHIVE, level=1, vec=encode(v, Grade.D2_BINARY),
        grade=Grade.D2_BINARY, original=v, keys=("cat", "pet"),
    )
    assert first.addr == second.addr
    assert first.fidelity == second.fidelity


def test_recording_a_read_does_not_change_the_node_address():
    """Reading a memory must not rewrite it."""
    from somaos.broker.memory.node import NodeStat

    node = make_node(region=Region.ARCHIVE, level=1, vec=embed(("cat",)))
    before = node.addr
    stat = NodeStat()
    stat.record_use(tick=5)
    stat.record_use(tick=9)
    assert node.addr == before
    assert stat.use_count == 2 and stat.last_used_tick == 9
