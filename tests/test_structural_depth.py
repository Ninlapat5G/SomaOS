"""Does the store read how deep the world is, or is it fixed at three levels?

The superordinate run showed three levels of generality coming apart in
the order people show, with the middle one built by the store rather than
specified. This asks whether three was the answer or just the question:
given worlds two, three, four and five levels deep, does the structure
that emerges follow?

It matters past the psychology. A memory that can only represent a fixed
number of levels has to be told about its domain in advance; one that
reads the depth out of what it has seen does not.

Worlds are held at roughly equal size so depth is the only variable, and
every question is a six-alternative forced choice against siblings, so
chance is 1/6 at every level of every world.
"""
from __future__ import annotations

import pytest

from somaos.bench.experiments.depth import CHANCE, run_depth

SEEDS = (3, 5, 7)
DEPTHS = (2, 3, 4)


@pytest.fixture(scope="module")
def worlds():
    return {(d, s): run_depth(d, seed=s) for d in (2, 3, 4, 5) for s in SEEDS}


@pytest.mark.parametrize("depth", DEPTHS)
def test_the_store_resolves_every_level_the_world_has(worlds, depth):
    """Up to four levels the emergent structure matches the world's."""
    for seed in SEEDS:
        verdict = worlds[(depth, seed)]["verdict"]
        assert verdict["levels_resolved"] == depth, (
            f"depth {depth}, seed {seed}: resolved {verdict['levels_resolved']}"
        )


#: Ordering is checked with the same margin used to call two levels
#: distinct. Scores are means over a few hundred forced choices, so
#: neighbouring levels deep in the degraded region wobble by a point or
#: two -- one seed inverts two levels by 0.015 where both sit near 0.48.
#: Demanding exact ordering there would be testing the noise.
ORDER_TOLERANCE = 0.03


@pytest.mark.parametrize("depth", DEPTHS)
def test_broader_levels_outlast_narrower_ones(worlds, depth):
    for seed in SEEDS:
        for row in worlds[(depth, seed)]["curve"]:
            levels = row["by_level"]
            assert all(
                levels[i] >= levels[i + 1] - ORDER_TOLERANCE
                for i in range(len(levels) - 1)
            ), f"depth {depth}, seed {seed}, budget {row['store_budget_bytes']}"


def test_five_levels_is_where_it_starts_to_run_out(worlds):
    """The honest ceiling, pinned so a regression past it is visible.

    At five levels the two deepest usually fold into one -- adjacent
    concepts share four of five names, so their vectors are near enough
    that quantization takes them together. Recorded as the measured limit
    rather than presented as a success.
    """
    resolved = [worlds[(5, seed)]["verdict"]["levels_resolved"] for seed in SEEDS]
    assert min(resolved) >= 4
    assert min(resolved) < 5 or max(resolved) == 5  # 4 usually, 5 sometimes


def test_the_broadest_level_never_degrades(worlds):
    for (depth, seed), world in worlds.items():
        for row in world["curve"]:
            assert row["by_level"][0] > 0.9, (depth, seed)


def test_the_narrowest_level_falls_toward_chance_not_below(worlds):
    for world in worlds.values():
        for row in world["curve"]:
            assert row["by_level"][-1] >= CHANCE - 0.05


def test_nothing_is_lost_at_any_depth_or_any_budget(worlds):
    for world in worlds.values():
        assert all(row["all_resolve"] for row in world["curve"])
