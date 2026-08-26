"""Cross-cutting invariants that must hold for every policy on every
regime, not just the specific cases exercised elsewhere. See
plans/wp/WP-10-determinism-ci.md #2."""
import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.metrics import build_metric_row
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.retention import RetentionWeights, extract_features, retention_score
from somaos.broker.types import ItemStat, MemoryItem

REGIMES = ["uniform", "variable", "long_gap", "bursty", "high_noise", "adversarial_flat", "topic_drift"]
POLICIES = ["B0", "B1", "B2", "B3", "B4", "S"]


@pytest.mark.parametrize("regime", REGIMES)
@pytest.mark.parametrize("policy", POLICIES)
def test_partial_recall_never_below_strict_recall(policy, regime):
    trace = generate(from_regime(regime, "inv-01", n_ticks=500))
    opt_mode = "exact_belady" if regime == "uniform" else "upper_bound"
    row = build_metric_row(
        policy_name=policy, regime=regime, trace=trace, budget_tokens=2048,
        tau_ticks=32, seed_root="inv-01", seed_split="dev", opt_mode=opt_mode,
    )
    assert row["partial_recall"] >= row["strict_recall"] - 1e-9


@pytest.mark.parametrize("regime", REGIMES)
@pytest.mark.parametrize("policy", POLICIES)
def test_competitive_ratio_never_exceeds_one(policy, regime):
    trace = generate(from_regime(regime, "inv-02", n_ticks=500))
    opt_mode = "exact_belady" if regime == "uniform" else "upper_bound"
    row = build_metric_row(
        policy_name=policy, regime=regime, trace=trace, budget_tokens=2048,
        tau_ticks=32, seed_root="inv-02", seed_split="dev", opt_mode=opt_mode,
    )
    assert row["competitive_ratio"] is None or row["competitive_ratio"] <= 1.0 + 1e-9


def test_retention_score_always_in_unit_interval_property():
    import random

    rng = random.Random(123)
    for _ in range(500):
        item = MemoryItem(
            id="i", kind="episodic", tokens=rng.randint(1, 200), created_tick=0,
            topics=tuple(f"t{i}" for i in range(rng.randint(0, 3))),
            entities=tuple(f"e{i}" for i in range(rng.randint(0, 3))),
            surprise=rng.random(), novelty=rng.choice([0.0, 1.0]),
            pinned=rng.choice([True, False]), recompute_cost=rng.random(),
        )
        stat = ItemStat(last_access_tick=0, access_count=rng.randint(0, 100))
        w = RetentionWeights(*(rng.uniform(0, 3) for _ in range(7)))
        feats = extract_features(
            item, stat, now_tick=rng.randint(0, 1000), tau_ticks=rng.randint(1, 200),
            goal_topics=frozenset({f"t{rng.randint(0,3)}"}), goal_entities=frozenset(),
            max_access_count=rng.randint(1, 200),
        )
        score = retention_score(feats, w)
        assert 0.0 <= score <= 1.0


@pytest.mark.parametrize("regime", REGIMES)
def test_no_query_ever_requires_a_future_item(regime):
    trace = generate(from_regime(regime, "inv-03", n_ticks=500))
    created_tick = {}
    for ev in trace.events:
        if ev.kind == "observe":
            created_tick[ev.observation.item.id] = ev.observation.item.created_tick
    for ev in trace.events:
        if ev.kind == "query":
            for iid in ev.query.required_item_ids:
                assert created_tick[iid] < ev.query.tick
