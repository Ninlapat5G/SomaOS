import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.policy import build_policy
from somaos.broker.types import Observation, Query, QueryView, to_view


def run_policy_on_trace(policy, trace, budget_tokens, seed_root):
    policy.reset(budget_tokens=budget_tokens, seed_root=seed_root, config={})
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            policy.on_query(to_view(ev.query))
    return policy


def test_leak_guard_on_query_never_touches_answer_key():
    """S's on_query must work with a QueryView (no required_item_ids field
    at all) without raising AttributeError -- this is the structural
    guarantee from WP-06 rule 2, not just a convention."""
    policy = build_policy("S")
    policy.reset(budget_tokens=500, seed_root="leak-test", config={})
    q = Query(id="q1", tick=0, topics=("t1",), entities=("e1",), required_item_ids=frozenset({"x"}))
    view = to_view(q)
    assert not hasattr(view, "required_item_ids")
    bundle = policy.on_query(view)  # must not raise
    assert bundle.query_id == "q1"


def test_encode_rate_low_in_high_noise_regime():
    trace = generate(from_regime("high_noise", "s-test-01", n_ticks=3000))
    policy = build_policy("S")
    run_policy_on_trace(policy, trace, budget_tokens=2048, seed_root="s-test-01")
    stats = policy.stats()
    encode_rate = stats["encoded"] / stats["observations_total"]
    assert encode_rate < 0.35, f"encode_rate={encode_rate}"


def test_encode_rate_not_collapsed_in_adversarial_flat():
    """Surprise gating shouldn't do anything pathological when surprise
    carries no signal -- encode_rate should stay in a normal range, not
    collapse to ~0 or blow up to ~1."""
    trace = generate(from_regime("adversarial_flat", "s-test-01", n_ticks=3000))
    policy = build_policy("S")
    run_policy_on_trace(policy, trace, budget_tokens=2048, seed_root="s-test-01")
    stats = policy.stats()
    encode_rate = stats["encoded"] / stats["observations_total"]
    assert 0.05 < encode_rate < 0.95, f"encode_rate={encode_rate}"


def test_determinism_bundle_hash_sequence():
    trace = generate(from_regime("uniform", "s-test-02", n_ticks=1000))

    def collect_hashes(seed):
        policy = build_policy("S")
        policy.reset(budget_tokens=1024, seed_root=seed, config={})
        hashes = []
        for ev in trace.events:
            if ev.kind == "observe":
                policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
            policy.on_tick(ev.tick)
            if ev.kind == "query":
                bundle = policy.on_query(to_view(ev.query))
                hashes.append(bundle.bundle_hash)
        return hashes

    h1 = collect_hashes("fixed-seed")
    h2 = collect_hashes("fixed-seed")
    assert h1 == h2
    assert len(h1) > 0


def test_bundle_respects_budget_on_real_trace():
    trace = generate(from_regime("bursty", "s-test-03", n_ticks=1500))
    policy = build_policy("S")
    policy.reset(budget_tokens=1024, seed_root="s-test-03", config={})
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            bundle = policy.on_query(to_view(ev.query))
            bundle.validate()  # raises if over budget


# --------------------------------------------------------------------------
# WP-12: recall_budget_fraction
# --------------------------------------------------------------------------


def _drive(policy, trace, budget, config):
    from somaos.broker.types import Observation, to_view

    policy.reset(budget_tokens=budget, seed_root="wp12", config=config)
    bundles = []
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            bundles.append((ev.query, policy.on_query(to_view(ev.query))))
    return bundles


def test_recall_budget_fraction_is_validated():
    p = build_policy("S")
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError):
            p.reset(budget_tokens=1024, seed_root="x", config={"recall_budget_fraction": bad})


def test_bundle_never_exceeds_budget_at_any_fraction():
    """Reserving a quota must not become an extra allowance."""
    trace = generate(from_regime("uniform", "wp12-01", n_ticks=600))
    for frac in (0.0, 0.25, 0.5, 1.0):
        p = build_policy("S")
        for _, bundle in _drive(p, trace, 1024, {"tau_ticks": 32, "recall_budget_fraction": frac}):
            bundle.validate()


def test_working_set_cannot_crowd_out_the_recall_quota():
    """The failure WP-12 exists to fix: with fraction=0 the working set
    took essentially the whole bundle. With a quota reserved, items the
    query is actually similar to must occupy at least part of it."""
    trace = generate(from_regime("uniform", "wp12-02", n_ticks=1500))
    budget = 4096
    quota = int(budget * 0.5)

    p = build_policy("S")
    pairs = _drive(p, trace, budget, {"tau_ticks": 32, "recall_budget_fraction": 0.5})
    assert pairs, "trace produced no queries"

    from somaos.broker.policies.s_soma import _sim

    checked = 0
    for query, bundle in pairs:
        q_t, q_e = frozenset(query.topics), frozenset(query.entities)
        similar_tokens = sum(it.tokens for it in bundle.items if _sim(it, q_t, q_e) > 0.0)
        store_has_similar = any(
            _sim(it, q_t, q_e) > 0.0 for it in p._items.values()
        )
        if not store_has_similar:
            continue
        checked += 1
        # Either recall filled its quota, or it ran out of similar items
        # to put there -- never "the working set got there first".
        assert similar_tokens > 0, (
            f"query {query.id}: store holds items similar to the query but none "
            "reached the bundle -- the recall quota is being crowded out"
        )
    assert checked > 0, "no query in this trace had a similar item in the store"


def test_higher_fraction_shifts_tokens_toward_query_relevant_items():
    trace = generate(from_regime("uniform", "wp12-03", n_ticks=1500))
    from somaos.broker.policies.s_soma import _sim

    def relevant_share(frac):
        p = build_policy("S")
        pairs = _drive(p, trace, 4096, {"tau_ticks": 32, "recall_budget_fraction": frac})
        rel = tot = 0
        for query, bundle in pairs:
            q_t, q_e = frozenset(query.topics), frozenset(query.entities)
            for it in bundle.items:
                tot += it.tokens
                if _sim(it, q_t, q_e) > 0.0:
                    rel += it.tokens
        return rel / tot if tot else 0.0

    assert relevant_share(0.5) > relevant_share(0.0)
