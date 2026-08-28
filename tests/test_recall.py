"""WP-A5 -- the recall walk.

The claims under test: the walk is bounded however large the store gets
(I6), every result carries the path that produced it (I8), the shadow text
has no influence on where the walk goes (I3/V1), and the agent cannot
steer the walk anywhere the engine has not sanctioned (N-09).
"""
from __future__ import annotations

import pytest

from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import embed
from somaos.broker.recall import Move, RecallMachine, RecallState
from somaos.broker.recall.machine import IllegalMove, structural_tokens


def _node(keys, level=ArchiveLevel.SPECIFIC_EVENT, **kw):
    return make_node(region=Region.ARCHIVE, level=int(level),
                     vec=embed(tuple(keys)), keys=tuple(keys), **kw)


def _forest(groups=3, per_group=5, *, with_text=True, beam=4):
    tree = MemoryTree(beam=beam)
    for g in range(groups):
        topic = f"topic{g}"
        root = make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
            vec=embed((topic,)), keys=(topic,),
            text_ref=f"the {topic} period" if with_text else "",
        )
        tree.insert(root)
        for i in range(per_group):
            tree.insert(make_node(
                region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
                vec=embed((topic, f"e{g}{i}")), keys=(topic, f"e{g}{i}"),
                text_ref=f"on day {i} of {topic} something happened" if with_text else "",
            ), parent=root.addr, tick=i)
    return tree


# ---------------------------------------------------------------- entry


def test_a_walk_starts_at_the_general_event_level():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic1",), tick=1)
    assert tree.get(machine.position).level == int(ArchiveLevel.GENERAL_EVENT)


def test_choosing_where_to_start_costs_a_step():
    """'Which chapter was that in?' is work; pretending otherwise
    understates every walk."""
    machine = RecallMachine(_forest())
    machine.begin(topics=("topic1",), tick=1)
    assert machine.path.ops_used == 1


def test_the_most_relevant_entry_point_is_chosen():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic2",), tick=1)
    assert "topic2" in tree.get(machine.position).keys


def test_an_empty_store_yields_an_empty_walk_not_a_crash():
    machine = RecallMachine(MemoryTree())
    machine.begin(topics=("anything",), tick=1)
    result = machine.finish()
    assert result.nodes == ()
    assert result.path.ops_used == 0


# ---------------------------------------------------------------- resident (N-06)


def test_resident_memories_cost_no_ops():
    """Identity was never searched for, so it is not charged as a search."""
    tree = _forest()
    core = tree.insert(make_node(
        region=Region.CORE, level=0, vec=embed(("careful",)), keys=("trait",),
    ))
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=1, resident=(core,))
    assert machine.path.ops_used == 1  # the entry choice only
    result = machine.finish()
    assert result.resident_tokens > 0
    assert core in {n.addr for n in result.nodes}


def test_resident_tokens_count_against_the_context_budget():
    tree = _forest()
    core = tree.insert(make_node(region=Region.CORE, level=0, vec=embed(("me",))))
    machine = RecallMachine(tree, context_budget_tokens=structural_tokens(
        tree.get(core)
    ))
    machine.begin(topics=("topic0",), tick=1, resident=(core,))
    machine.step(Move.MATERIALIZE)
    result = machine.finish()
    assert result.total_tokens <= machine.context_budget_tokens
    assert result.path.stopped_by == "context_budget_exhausted"


# ---------------------------------------------------------------- bounds (I6)


def test_the_walk_never_exceeds_its_op_budget():
    tree = _forest(groups=6, per_group=8)
    machine = RecallMachine(tree, ops_budget=5)
    machine.begin(topics=("topic1",), tick=1)
    for _ in range(50):
        if machine.state is not RecallState.NAVIGATE:
            break
        moves = machine.offer()
        machine.step(Move.DESCEND if Move.DESCEND in moves else moves[0])
    result = machine.finish()
    assert result.path.ops_used <= 5


def test_running_out_of_ops_stops_the_walk_cleanly():
    machine = RecallMachine(_forest(), ops_budget=2)
    machine.begin(topics=("topic0",), tick=1)
    machine.step(Move.DESCEND)
    machine.step(Move.DESCEND)
    result = machine.finish()
    assert result.path.stopped_by == "ops_exhausted"


def test_walk_cost_does_not_grow_with_unrelated_experience():
    """I6: recall is bounded by the beam, not by a lifetime of memories."""
    def ops_for(groups):
        tree = _forest(groups=groups, per_group=5)
        machine = RecallMachine(tree, ops_budget=16)
        machine.begin(topics=("topic0",), tick=1)
        machine.step(Move.DESCEND)
        machine.step(Move.MATERIALIZE)
        return machine.finish().path.ops_used

    assert ops_for(2) == ops_for(40)


def test_a_memory_that_does_not_fit_is_refused_not_truncated():
    """Half a memory in context is worse than none: it reads as complete."""
    tree = _forest()
    machine = RecallMachine(tree, context_budget_tokens=1)
    machine.begin(topics=("topic0",), tick=1)
    machine.step(Move.MATERIALIZE)
    result = machine.finish()
    assert result.nodes == ()
    assert result.tokens_used == 0
    assert result.path.stopped_by == "context_budget_exhausted"


# ---------------------------------------------------------------- the closed move set (N-09)


def test_only_legal_moves_are_offered():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=1)
    # At a general event with children and a cue: no parent, so no ascend.
    assert Move.ASCEND not in machine.offer()
    assert Move.DESCEND in machine.offer()
    machine.step(Move.DESCEND)
    assert Move.ASCEND in machine.offer()


def test_an_illegal_move_is_refused_rather_than_reinterpreted():
    machine = RecallMachine(_forest())
    machine.begin(topics=("topic0",), tick=1)
    with pytest.raises(IllegalMove):
        machine.step(Move.ASCEND)  # nothing above a general event here


def test_stepping_without_a_walk_in_progress_is_refused():
    machine = RecallMachine(_forest())
    with pytest.raises(IllegalMove):
        machine.step(Move.DESCEND)


def test_when_ops_run_out_only_stop_is_offered():
    machine = RecallMachine(_forest(), ops_budget=1)
    machine.begin(topics=("topic0",), tick=1)
    assert machine.offer() == (Move.STOP,)


def test_materializing_the_same_memory_twice_is_free_and_harmless():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=1)
    machine.step(Move.MATERIALIZE)
    machine.step(Move.MATERIALIZE)
    result = machine.finish()
    assert len(result.nodes) == 1


def test_lateral_moves_to_a_neighbour_on_the_frontier():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=1)
    machine.step(Move.DESCEND)
    first = machine.position
    machine.step(Move.LATERAL)
    assert machine.position != first


# ---------------------------------------------------------------- text is a shadow (I3 / V1)


def test_stripping_every_text_ref_leaves_the_walk_bit_identical():
    """The invariant that makes vectors, not prose, the system's knowledge.

    If this fails, some decision has started reading the human-readable
    shadow, and the store is no longer language-independent.
    """
    def walk(with_text):
        tree = _forest(groups=4, per_group=6, with_text=with_text)
        machine = RecallMachine(tree, ops_budget=12)
        machine.begin(topics=("topic2",), entities=("e21",), tick=3)
        for move in (Move.DESCEND, Move.MATERIALIZE, Move.LATERAL,
                     Move.MATERIALIZE, Move.ASCEND):
            if move in machine.offer():
                machine.step(move)
        result = machine.finish()
        return [
            (s.move, s.addr, round(s.score, 12), s.ops_after) for s in result.path.steps
        ]

    assert walk(True) == walk(False)


def test_token_cost_does_not_depend_on_the_shadow_text():
    """Cost must come from structure, or V1 would be false by the back door."""
    long_text = make_node(
        region=Region.ARCHIVE, level=1, vec=embed(("a",)), text_ref="x" * 5000,
    )
    no_text = make_node(region=Region.ARCHIVE, level=1, vec=embed(("a",)))
    assert structural_tokens(long_text) == structural_tokens(no_text)


# ---------------------------------------------------------------- explain (I8)


def test_every_result_carries_the_path_that_produced_it():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic1",), tick=1)
    machine.step(Move.DESCEND)
    machine.step(Move.MATERIALIZE)
    result = machine.finish()
    assert result.path.steps
    assert result.path.materialized == [n.addr for n in result.nodes]
    assert result.path.stopped_by


def test_the_path_serialises_for_the_record():
    machine = RecallMachine(_forest())
    machine.begin(topics=("topic1",), tick=1)
    machine.step(Move.MATERIALIZE)
    blob = machine.finish().path.to_jsonable()
    assert blob["materialized"] and blob["steps"]
    assert set(blob["steps"][0]) == {"move", "addr", "score", "ops_after", "note"}


def test_ops_in_the_path_are_monotonic():
    machine = RecallMachine(_forest(), ops_budget=10)
    machine.begin(topics=("topic0",), tick=1)
    for move in (Move.DESCEND, Move.MATERIALIZE, Move.LATERAL):
        machine.step(move)
    counts = [s.ops_after for s in machine.finish().path.steps]
    assert counts == sorted(counts)


# ---------------------------------------------------------------- settle


def test_side_effects_land_only_at_the_end():
    """Nothing mutates mid-walk, which is what makes a walk replayable."""
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=10)
    machine.step(Move.DESCEND)
    machine.step(Move.MATERIALIZE)
    reached = machine.position
    assert tree.stat(reached).use_count == 0
    machine.finish()
    assert tree.stat(reached).use_count == 1


def test_a_recalled_memory_becomes_easier_to_reach_next_time():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=900)
    machine.step(Move.DESCEND)
    reached = machine.position
    before = tree.retrieval_strength(reached, tick=900)
    machine.step(Move.MATERIALIZE)
    machine.finish()
    assert tree.retrieval_strength(reached, tick=900) > before


def test_a_machine_returns_to_idle_and_can_be_reused():
    tree = _forest()
    machine = RecallMachine(tree)
    machine.begin(topics=("topic0",), tick=1)
    machine.step(Move.MATERIALIZE)
    machine.finish()
    assert machine.state is RecallState.IDLE
    machine.begin(topics=("topic1",), tick=2)
    second = machine.finish()
    assert second.path.materialized == []  # nothing leaked from the first walk


# ---------------------------------------------------------------- fast path


def test_the_fast_path_needs_no_agent_and_no_model():
    """What runs when nobody is asked, and when the model bus is down."""
    tree = _forest()
    machine = RecallMachine(tree, ops_budget=12)
    machine.begin(topics=("topic1",), entities=("e11",), tick=2)
    result = machine.run_fast_path()
    assert result.nodes
    assert result.path.ops_used <= 12
    assert result.path.stopped_by


def test_the_fast_path_respects_both_budgets():
    tree = _forest(groups=4, per_group=8)
    machine = RecallMachine(tree, ops_budget=6, context_budget_tokens=40)
    machine.begin(topics=("topic0",), tick=2)
    result = machine.run_fast_path(max_materialized=99)
    assert result.path.ops_used <= 6
    assert result.total_tokens <= 40


def test_the_fast_path_is_deterministic():
    def run():
        tree = _forest(groups=4, per_group=6)
        machine = RecallMachine(tree, ops_budget=12)
        machine.begin(topics=("topic2",), entities=("e20",), tick=4)
        result = machine.run_fast_path()
        return [n.addr for n in result.nodes], result.path.ops_used

    assert run() == run()


def test_the_fast_path_is_a_real_searcher_not_a_placeholder():
    """It has to be, or agent-directed recall would only ever be compared
    against something broken.

    An earlier greedy version could not recover from a wrong turn: measured
    on a full run it brought back four neighbours of the wanted memory
    while the memory itself sat five levels down at full precision,
    unreached, with most of its step budget unspent.
    """
    tree = _forest(groups=6, per_group=8)
    # entry_points() is address-ordered, not topic-ordered.
    home = next(a for a in tree.entry_points() if "topic4" in tree.get(a).keys)
    wanted = tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=embed(("topic4", "the needle")), keys=("topic4", "the needle"),
    ), parent=home)

    machine = RecallMachine(tree, ops_budget=16, context_budget_tokens=512)
    machine.begin(topics=("topic4",), entities=("the needle",), tick=1)
    found = machine.run_fast_path()
    assert wanted in {n.addr for n in found.nodes}


def test_the_search_can_recover_from_a_wrong_first_turn():
    """One frontier for the whole walk, not the best child of each node in
    turn. "No, not that trip, the other one" is backtracking."""
    tree = MemoryTree(beam=2)
    root_a = tree.insert(_node(("trip", "alps"), ArchiveLevel.GENERAL_EVENT))
    root_b = tree.insert(_node(("trip", "coast"), ArchiveLevel.GENERAL_EVENT))
    for i in range(6):
        tree.insert(_node(("trip", "alps", f"a{i}")), parent=root_a)
    wanted = tree.insert(_node(("trip", "coast", "the storm")), parent=root_b)

    machine = RecallMachine(tree, ops_budget=12, context_budget_tokens=512)
    machine.begin(topics=("trip",), entities=("the storm",), tick=1)
    result = machine.run_fast_path()
    assert wanted in {n.addr for n in result.nodes}


def test_an_agent_steering_reaches_a_target_in_fewer_steps():
    """What the agent's turn is for: not finding what the fast path cannot,
    but getting there without exploring everything else first."""
    tree = _forest(groups=6, per_group=8)

    fast = RecallMachine(tree, ops_budget=16, context_budget_tokens=512)
    fast.begin(topics=("topic3",), entities=("e31",), tick=1)
    fast_ops = fast.run_fast_path().path.ops_used

    guided = RecallMachine(tree, ops_budget=16, context_budget_tokens=512)
    guided.begin(topics=("topic3",), entities=("e31",), tick=1)
    guided.step(Move.DESCEND)
    guided.step(Move.MATERIALIZE)
    result = guided.finish()

    assert any("e31" in n.keys for n in result.nodes)
    assert result.path.ops_used < fast_ops
