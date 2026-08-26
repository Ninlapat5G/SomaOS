"""B0 (full context, unbounded budget) must answer every query correctly
on every regime -- if it doesn't, the trace generator's ground truth is
broken, not B0 (plans/wp/WP-02-trace-generator.md #5.4,
plans/wp/WP-05-policies.md acceptance #2). This is the load-bearing
integration check between WP-02 and WP-05."""
import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.policy import build_policy
from somaos.broker.types import Observation, to_view

REGIMES = ["uniform", "variable", "long_gap", "bursty", "high_noise", "adversarial_flat", "topic_drift"]


@pytest.mark.parametrize("regime", REGIMES)
def test_b0_strict_recall_is_one(regime):
    trace = generate(from_regime(regime, "sanity-01", n_ticks=1000))
    policy = build_policy("B0")
    policy.reset(budget_tokens=1, seed_root="sanity-01", config={})  # budget ignored by B0

    n_queries = 0
    n_correct = 0
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            bundle = policy.on_query(to_view(ev.query))
            bundle_ids = {it.id for it in bundle.items}
            n_queries += 1
            if ev.query.required_item_ids <= bundle_ids:
                n_correct += 1

    assert n_queries > 0, "no queries in trace"
    assert n_correct == n_queries, f"B0 missed {n_queries - n_correct}/{n_queries} queries in regime={regime}"
