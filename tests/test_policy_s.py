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
