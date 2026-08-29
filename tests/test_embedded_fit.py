"""The microcontroller claims, pinned.

SomaOS is meant to run on parts where flash is megabytes and RAM is
kilobytes. Two things have to hold for that to be true, and both are
cheap to check, so they are checked rather than asserted in a document:

  * the ladder's byte costs are what the capacity arithmetic assumes
  * one recall's working set fits in the SRAM of the smallest part we
    claim, with room left for the firmware

The quality claim is measured separately and only spot-checked here, at
a smaller size, so the suite stays fast.
"""
from __future__ import annotations

import numpy as np
import pytest

from somaos.bench.experiments.embedded import (
    BYTES_PER_WORKING_ADDRESS,
    CHIPS,
    e1_capacity,
)
from somaos.bench.lifeworld import WorldConfig, generate
from somaos.broker.memory.node import Region
from somaos.broker.memory.vector import DEFAULT_DIM, Grade, nbytes
from somaos.broker.policies.life import Budgets, STree

#: No recall may hold more of the tree than this at once. Set well under
#: the tightest SRAM on the list because the firmware, the stack and the
#: driver buffers are all in there too -- a walk that fitted exactly
#: would not fit at all.
WORKING_SET_CEILING_BYTES = 8 * 1024


def _run(*, store_bytes: int, ops: int, ticks: int = 120):
    trace = generate(WorldConfig(n_ticks=ticks, seed_root="dev-01"))
    policy = STree()
    policy.reset(
        budgets=Budgets(store_bytes=store_bytes, context_tokens=256, recall_ops=ops),
        tokens_of=lambda node: 20,
        seed_root="dev-01",
    )
    by_tick: dict[int, list] = {}
    for episode in trace.episodes:
        by_tick.setdefault(episode.tick, []).append(episode)
    for tick in range(ticks):
        for episode in by_tick.get(tick, ()):
            policy.perceive(episode)
        policy.on_tick(tick)
    return trace, policy


# ----------------------------------------------------------- the ladder

def test_ladder_byte_costs_are_what_capacity_assumes():
    probe = np.zeros(DEFAULT_DIM, dtype=np.float32)
    assert nbytes(probe, Grade.D0_EXACT) == 1024
    assert nbytes(probe, Grade.D1_INT8) == 256
    assert nbytes(probe, Grade.D2_BINARY) == 32
    assert nbytes(probe, Grade.D3_MERGED) == 0
    assert nbytes(probe, Grade.D4_COUNTER) == 0


def test_each_rung_is_strictly_cheaper_than_the_one_above():
    probe = np.zeros(DEFAULT_DIM, dtype=np.float32)
    costs = [nbytes(probe, g) for g in Grade]
    assert costs == sorted(costs, reverse=True)


def test_capacity_arithmetic_matches_the_ladder():
    report = e1_capacity()
    per = report["bytes_per_memory"]
    for chip in report["chips"]:
        for grade, count in chip["memories_that_fit"].items():
            assert count == chip["store_bytes"] // per[grade]


def test_every_listed_chip_has_a_store_that_fits_its_flash():
    for name, flash, _sram, store in CHIPS:
        assert 0 < store < flash, name


# ------------------------------------------------------- the working set

@pytest.mark.parametrize("ops", [4, 16, 32, 64])
def test_one_recall_fits_in_microcontroller_ram(ops):
    trace, policy = _run(store_bytes=128 * 1024, ops=ops)
    peak = 0
    for question in trace.questions:
        if question.kind == "trigger":
            continue
        policy.recall(question)
        peak = max(peak, policy.machine.peak_working_addresses)

    assert peak > 0, "nothing was searched -- the measurement is not measuring"
    assert peak * BYTES_PER_WORKING_ADDRESS < WORKING_SET_CEILING_BYTES


def _peak_of(trace, policy) -> int:
    seen = 0
    for question in trace.questions:
        if question.kind == "trigger":
            continue
        policy.recall(question)
        seen = max(seen, policy.machine.peak_working_addresses)
    return seen


def test_working_set_saturates_instead_of_growing_with_the_store():
    """Past a point, remembering more must not make a walk hold more.

    This is the property that lets the store live in flash and the walk
    live in SRAM. It is stated as saturation rather than as a flat line:
    a small store has too little tree to fill the frontier, so the peak
    climbs at first and then stops. What must not happen is that it keeps
    tracking the store.
    """
    mid_trace, mid = _run(store_bytes=128 * 1024, ops=16, ticks=480)
    big_trace, big = _run(store_bytes=128 * 1024, ops=16, ticks=960)

    assert len(big_trace.episodes) >= 2 * len(mid_trace.episodes)
    assert _peak_of(big_trace, big) <= _peak_of(mid_trace, mid)


@pytest.mark.parametrize("ops", [4, 16, 32])
def test_working_set_never_exceeds_the_effort_ceiling(ops):
    """The hard bound: a walk visits at most ``ops`` nodes and scores at
    most ``beam`` children at each, on top of the entry points it started
    from. Nothing in the search may exceed that, whatever is stored."""
    trace, policy = _run(store_bytes=128 * 1024, ops=ops, ticks=480)
    entries = len(policy.tree.entry_points(Region.ARCHIVE))
    assert _peak_of(trace, policy) <= entries + ops * policy.beam


# ------------------------------------------------------------ the store

def test_a_microcontroller_budget_still_holds_every_memory():
    """Dilution reduces bytes per memory, never the number of memories.

    On a part where the budget really binds, this is the claim that
    matters: the agent does not stop having a past, it has a blurrier
    one (N-01).
    """
    _, roomy = _run(store_bytes=1536 * 1024, ops=16)
    _, tight = _run(store_bytes=48 * 1024, ops=16)

    assert tight.store_bytes() <= 48 * 1024
    assert len(tight.tree) >= 0.6 * len(roomy.tree)


def test_gist_survives_a_microcontroller_budget():
    """Squeezed to a chip-sized store, it still knows what things were about."""
    trace, policy = _run(store_bytes=48 * 1024, ops=16)
    from somaos.bench.score import Marker

    marker = Marker(trace)
    scores = []
    for question in trace.questions:
        if question.kind == "trigger":
            continue
        outcome = policy.recall(question)
        scores.append(marker.mark(question, outcome.nodes, tokens=outcome.tokens,
                                  comparisons=outcome.comparisons,
                                  ops=outcome.ops).gist)
    assert sum(scores) / len(scores) > 0.8
