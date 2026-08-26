import copy
import math
import random

import pytest

from somaos.broker.retention import (
    RetentionFeatures,
    RetentionWeights,
    extract_features,
    retention_score,
)
from somaos.broker.types import ItemStat, MemoryItem


def make_item(**kw):
    defaults = dict(
        id="i1", kind="episodic", tokens=10, created_tick=0,
        topics=("t1", "t2"), entities=("e1",), surprise=0.5, novelty=0.0,
        pinned=False, recompute_cost=0.0,
    )
    defaults.update(kw)
    return MemoryItem(**defaults)


def uniform_weights(val=1.0):
    return RetentionWeights(
        w_recency=val, w_frequency=val, w_relevance=val,
        w_surprise=val, w_novelty=val, w_pinned=val, w_recompute=val,
    )


# ---------- 1. bounded ----------

def test_score_bounded_property():
    rng = random.Random(42)
    for _ in range(2000):
        f = RetentionFeatures(
            recency=rng.random(), frequency=rng.random(), relevance=rng.random(),
            surprise=rng.random(), novelty=rng.random(), pinned=rng.random(),
            recompute=rng.random(),
        )
        w = RetentionWeights(
            w_recency=rng.uniform(0, 5), w_frequency=rng.uniform(0, 5),
            w_relevance=rng.uniform(0, 5), w_surprise=rng.uniform(0, 5),
            w_novelty=rng.uniform(0, 5), w_pinned=rng.uniform(0, 5),
            w_recompute=rng.uniform(0, 5),
        )
        s = retention_score(f, w)
        assert 0.0 <= s <= 1.0


# ---------- 2. monotone ----------

@pytest.mark.parametrize("field", [
    "recency", "frequency", "relevance", "surprise", "novelty", "pinned", "recompute",
])
def test_monotone_in_each_feature(field):
    w = uniform_weights(1.0)
    base = dict(recency=0.3, frequency=0.3, relevance=0.3, surprise=0.3,
                novelty=0.3, pinned=0.3, recompute=0.3)
    low = dict(base); low[field] = 0.2
    high = dict(base); high[field] = 0.8
    s_low = retention_score(RetentionFeatures(**low), w)
    s_high = retention_score(RetentionFeatures(**high), w)
    assert s_high >= s_low


# ---------- 3. zero-weight isolation ----------

def test_zero_weight_isolates_feature():
    w = RetentionWeights(
        w_recency=1.0, w_frequency=1.0, w_relevance=1.0,
        w_surprise=0.0, w_novelty=1.0, w_pinned=1.0, w_recompute=1.0,
    )
    base = dict(recency=0.4, frequency=0.4, relevance=0.4, novelty=0.4,
                pinned=0.4, recompute=0.4)
    f_low_surprise = RetentionFeatures(surprise=0.0, **base)
    f_high_surprise = RetentionFeatures(surprise=1.0, **base)
    assert retention_score(f_low_surprise, w) == retention_score(f_high_surprise, w)


def test_all_zero_weight_raises():
    w = uniform_weights(0.0)
    f = RetentionFeatures(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    with pytest.raises(ValueError):
        retention_score(f, w)


# ---------- 4. recency decay ----------

def test_recency_decay_at_tau_is_one_over_e():
    item = make_item()
    stat = ItemStat(last_access_tick=0, access_count=0)
    feats = extract_features(
        item, stat, now_tick=32, tau_ticks=32,
        goal_topics=frozenset(), goal_entities=frozenset(), max_access_count=10,
    )
    assert abs(feats.recency - math.exp(-1)) < 1e-9


def test_recency_decreases_with_age():
    item = make_item()
    stat = ItemStat(last_access_tick=0, access_count=0)
    f_young = extract_features(item, stat, now_tick=1, tau_ticks=32,
                                goal_topics=frozenset(), goal_entities=frozenset(),
                                max_access_count=10)
    f_old = extract_features(item, stat, now_tick=100, tau_ticks=32,
                              goal_topics=frozenset(), goal_entities=frozenset(),
                              max_access_count=10)
    assert f_young.recency > f_old.recency


def test_negative_age_raises():
    item = make_item()
    stat = ItemStat(last_access_tick=50, access_count=0)
    with pytest.raises(ValueError):
        extract_features(item, stat, now_tick=10, tau_ticks=32,
                          goal_topics=frozenset(), goal_entities=frozenset(),
                          max_access_count=10)


# ---------- 5. frequency saturation ----------

def test_frequency_saturates_logarithmically():
    """Equal-sized (+1) increments should matter less at high counts than at
    low counts — that's what saturation means. (Doubling access_count, e.g.
    1->2 vs 1000->2000, is the wrong comparison: ln(2x)-ln(x) = ln(2) is
    constant for any x, so it wouldn't distinguish saturation at all.)"""
    item = make_item()
    stat_1 = ItemStat(last_access_tick=0, access_count=1)
    stat_2 = ItemStat(last_access_tick=0, access_count=2)
    stat_1000 = ItemStat(last_access_tick=0, access_count=1000)
    stat_1001 = ItemStat(last_access_tick=0, access_count=1001)

    def freq(stat):
        return extract_features(
            item, stat, now_tick=0, tau_ticks=32,
            goal_topics=frozenset(), goal_entities=frozenset(), max_access_count=5000,
        ).frequency

    delta_low = freq(stat_2) - freq(stat_1)
    delta_high = freq(stat_1001) - freq(stat_1000)
    assert delta_high < delta_low


# ---------- 6. determinism ----------

def test_determinism_bit_identical():
    item = make_item()
    stat = ItemStat(last_access_tick=5, access_count=3)
    w = uniform_weights(1.0)
    results = set()
    for _ in range(1000):
        feats = extract_features(item, stat, now_tick=40, tau_ticks=32,
                                  goal_topics=frozenset({"t1"}), goal_entities=frozenset(),
                                  max_access_count=10)
        results.add(retention_score(feats, w))
    assert len(results) == 1


# ---------- 7. no side effects ----------

def test_no_mutation_of_inputs():
    item = make_item()
    stat = ItemStat(last_access_tick=5, access_count=3)
    item_before = copy.deepcopy(item)
    stat_before = copy.deepcopy(stat)
    extract_features(item, stat, now_tick=40, tau_ticks=32,
                      goal_topics=frozenset({"t1"}), goal_entities=frozenset(),
                      max_access_count=10)
    assert item == item_before
    assert stat.last_access_tick == stat_before.last_access_tick
    assert stat.access_count == stat_before.access_count
    assert stat.tier == stat_before.tier
    assert stat.admitted_tick == stat_before.admitted_tick


# ---------- 8. golden ----------

def test_golden_values():
    import json
    from pathlib import Path

    golden_path = Path(__file__).parent / "golden" / "retention.json"
    cases = json.loads(golden_path.read_text())
    for case in cases:
        item = make_item(
            topics=tuple(case["item_topics"]), entities=tuple(case["item_entities"]),
            surprise=case["surprise"], novelty=case["novelty"],
            pinned=case["pinned"], recompute_cost=case["recompute_cost"],
        )
        stat = ItemStat(last_access_tick=case["last_access_tick"], access_count=case["access_count"])
        w = RetentionWeights.from_json(case["weights"])
        feats = extract_features(
            item, stat, now_tick=case["now_tick"], tau_ticks=case["tau_ticks"],
            goal_topics=frozenset(case["goal_topics"]), goal_entities=frozenset(case["goal_entities"]),
            max_access_count=case["max_access_count"],
        )
        score = retention_score(feats, w)
        assert abs(score - case["expected_score"]) < 1e-9, case["name"]


# ---------- empty jaccard ----------

def test_relevance_zero_when_goal_empty():
    item = make_item()
    stat = ItemStat(last_access_tick=0, access_count=0)
    feats = extract_features(item, stat, now_tick=0, tau_ticks=32,
                              goal_topics=frozenset(), goal_entities=frozenset(),
                              max_access_count=10)
    assert feats.relevance == 0.0


# ---------- additional coverage: from_json / normalized / guard clauses ----------

def test_weights_from_json():
    w = RetentionWeights.from_json(dict(
        w_recency=1, w_frequency=2, w_relevance=3, w_surprise=4,
        w_novelty=5, w_pinned=6, w_recompute=7,
    ))
    assert w.w_recency == 1.0
    assert w.w_recompute == 7.0


def test_weights_normalized_sums_to_one():
    w = RetentionWeights(1, 1, 1, 1, 1, 1, 1)
    n = w.normalized()
    total = (n.w_recency + n.w_frequency + n.w_relevance + n.w_surprise
             + n.w_novelty + n.w_pinned + n.w_recompute)
    assert abs(total - 1.0) < 1e-12


def test_weights_normalized_all_zero_raises():
    w = RetentionWeights(0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        w.normalized()


def test_tau_ticks_zero_raises():
    item = make_item()
    stat = ItemStat(last_access_tick=0, access_count=0)
    with pytest.raises(ValueError):
        extract_features(item, stat, now_tick=0, tau_ticks=0,
                          goal_topics=frozenset(), goal_entities=frozenset(),
                          max_access_count=10)


def test_max_access_count_zero_gives_zero_frequency():
    item = make_item()
    stat = ItemStat(last_access_tick=0, access_count=5)
    feats = extract_features(item, stat, now_tick=0, tau_ticks=32,
                              goal_topics=frozenset(), goal_entities=frozenset(),
                              max_access_count=0)
    assert feats.frequency == 0.0


# --------------------------------------------------------------------------
# WP-14: fused scoring path must be bit-identical to the reference path
# --------------------------------------------------------------------------


def test_score_item_matches_the_reference_path_exactly():
    """score_item exists only to be faster. If it ever disagrees with
    extract_features + retention_score by a single bit, the optimization
    has changed behaviour and must be reverted, not tolerated."""
    import random

    from somaos.broker.retention import score_item

    rng = random.Random(20260826)
    for _ in range(3000):
        item = MemoryItem(
            id=f"i{rng.randrange(1000)}", kind="episodic",
            tokens=rng.randint(1, 500), created_tick=0,
            topics=tuple(f"t{rng.randrange(6)}" for _ in range(rng.randint(0, 3))),
            entities=tuple(f"e{rng.randrange(6)}" for _ in range(rng.randint(0, 3))),
            surprise=rng.random(), novelty=rng.choice([0.0, 1.0]),
            pinned=rng.choice([True, False]), recompute_cost=rng.random(),
        )
        stat = ItemStat(last_access_tick=rng.randrange(0, 500),
                        access_count=rng.randrange(0, 200))
        now = stat.last_access_tick + rng.randrange(0, 500)
        tau = rng.randint(1, 200)
        goal_t = frozenset(f"t{rng.randrange(6)}" for _ in range(rng.randint(0, 3)))
        goal_e = frozenset(f"e{rng.randrange(6)}" for _ in range(rng.randint(0, 3)))
        max_ac = rng.randrange(0, 250)
        w = RetentionWeights(*(rng.uniform(0.01, 3) for _ in range(7)))

        reference = retention_score(
            extract_features(item, stat, now_tick=now, tau_ticks=tau,
                             goal_topics=goal_t, goal_entities=goal_e,
                             max_access_count=max_ac),
            w,
        )
        fused = score_item(item, stat, now_tick=now, tau_ticks=tau,
                           goal_topics=goal_t, goal_entities=goal_e,
                           max_access_count=max_ac, weights=w)
        assert fused == reference, (item, stat, now, tau, goal_t, goal_e, max_ac, w)


def test_score_item_rejects_the_same_inputs_as_the_reference():
    from somaos.broker.retention import score_item

    item = MemoryItem(id="i", kind="episodic", tokens=10, created_tick=0,
                       topics=(), entities=(), surprise=0.0, novelty=0.0)
    w = RetentionWeights(1, 1, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError):
        score_item(item, ItemStat(last_access_tick=0), now_tick=0, tau_ticks=0,
                   goal_topics=frozenset(), goal_entities=frozenset(),
                   max_access_count=1, weights=w)
    with pytest.raises(ValueError):
        score_item(item, ItemStat(last_access_tick=10), now_tick=0, tau_ticks=32,
                   goal_topics=frozenset(), goal_entities=frozenset(),
                   max_access_count=1, weights=w)
