"""Does the store fail the way a damaged semantic memory fails?

Every other test asks whether the design works. This one asks whether it
goes wrong the way people go wrong, which is a much harder thing to pass
and the only kind of evidence that connects any of this to human data.

The pattern being reproduced is the superordinate advantage in semantic
dementia: as the disease progresses, patients lose specific names before
general ones -- "animal" survives long after "zebra" is gone -- with a
graded ordering across three levels, not a two-way split.

On circularity, since it is the obvious objection: the dilution ladder was
built so that sign bits keep category and lose instance, so "general beats
specific" is designed in and proves nothing by itself. The part that is
not designed in is the middle. Nothing in the ladder knows about three
levels, and the tree is given only a top tier and the exemplars -- the
intermediate tier is built by consolidation, which splits nodes wider than
the beam can see past and groups the children by similarity. Whether those
emergent buckets support a basic-level answer that degrades at an
intermediate rate is what these tests check.
"""
from __future__ import annotations

import pytest

from somaos.bench.experiments.superordinate import CHANCE, run, verdict

SEEDS = (1, 2, 3, 4, 5)


@pytest.fixture(scope="module")
def curves():
    return {seed: run(seed=seed) for seed in SEEDS}


def test_the_general_survives_the_specific(curves):
    """The ordering itself, at every store size and every seed."""
    for seed, rows in curves.items():
        for row in rows:
            assert row["superordinate"] >= row["basic"] >= row["subordinate"] - 1e-9, (
                f"ordering broken at seed {seed}, budget {row['store_budget_bytes']}"
            )


def test_the_middle_level_really_is_in_the_middle(curves):
    """The part that is not designed in.

    A two-level split -- exact, then everything but the category gone --
    would be the ladder reproducing itself. Basic sitting strictly between
    the other two, across several store sizes, is a property of how the
    tree degrades.
    """
    for seed, rows in curves.items():
        strictly_between = [
            r for r in rows
            if r["superordinate"] > r["basic"] > r["subordinate"]
        ]
        assert len(strictly_between) >= 3, f"seed {seed} shows a cliff, not a gradient"


def test_category_knowledge_holds_when_the_specifics_are_at_chance(curves):
    """The clinical signature: still names the family, cannot name the member."""
    for rows in curves.values():
        worst = min(rows, key=lambda r: r["store_budget_bytes"])
        assert worst["superordinate"] > 0.9
        assert worst["subordinate"] < 3 * CHANCE


def test_the_specific_level_decays_toward_chance_not_below_it(curves):
    """Falling below chance would mean the representation had inverted,
    which is corruption rather than forgetting."""
    for rows in curves.values():
        for row in rows:
            assert row["subordinate"] >= CHANCE - 0.05


def test_the_verdict_agrees_on_every_seed(curves):
    for seed, rows in curves.items():
        result = verdict(rows)
        assert result["ordering_holds_everywhere"], seed
        assert result["graded_not_a_cliff"], seed


def test_nothing_is_lost_at_any_point_on_the_curve(curves):
    """N-01 still holds while all this is happening."""
    for rows in curves.values():
        assert all(row["all_resolve"] for row in rows)
