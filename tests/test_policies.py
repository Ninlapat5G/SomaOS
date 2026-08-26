import pytest

import somaos.broker.policies  # noqa: F401  registers B0-B4
from somaos.broker.policy import POLICY_REGISTRY, build_policy
from somaos.broker.types import MemoryItem, Observation, Query, to_view

ALL_BASELINES = ["B0", "B1", "B2", "B3", "B4", "S"]


def make_item(id_, tick, tokens=10, topics=(), entities=(), surprise=0.5, novelty=0.0):
    return MemoryItem(
        id=id_, kind="episodic", tokens=tokens, created_tick=tick,
        topics=topics, entities=entities, surprise=surprise, novelty=novelty,
    )


def run_trace(policy, items_by_tick, queries_by_tick, budget_tokens, seed_root="t1", config=None):
    policy.reset(budget_tokens=budget_tokens, seed_root=seed_root, config=config or {})
    bundles = []
    max_tick = max(list(items_by_tick.keys()) + list(queries_by_tick.keys()) + [0])
    for t in range(max_tick + 1):
        for item in items_by_tick.get(t, []):
            policy.observe(Observation(tick=t, item=item))
        policy.on_tick(t)
        for q in queries_by_tick.get(t, []):
            bundle = policy.on_query(to_view(q))
            bundles.append((q, bundle))
    return bundles


def build_simple_scenario(n_items=30, tokens=10):
    items_by_tick = {}
    for i in range(n_items):
        items_by_tick.setdefault(i, []).append(
            make_item(f"i{i}", i, tokens=tokens, topics=(f"t{i % 5}",), entities=(f"e{i % 3}",))
        )
    queries_by_tick = {
        n_items: [
            Query(id="q1", tick=n_items, topics=("t0",), entities=("e0",),
                  required_item_ids=frozenset({"i0"})),
        ]
    }
    return items_by_tick, queries_by_tick


@pytest.mark.parametrize("name", ALL_BASELINES)
def test_conformance_bundle_within_budget(name):
    policy = build_policy(name)
    items_by_tick, queries_by_tick = build_simple_scenario()
    bundles = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=100)
    for q, bundle in bundles:
        assert bundle.query_id == q.id
        assert bundle.tick == q.tick
        if not policy.ignores_budget:
            bundle.validate()


@pytest.mark.parametrize("name", ALL_BASELINES)
def test_conformance_reset_clears_state(name):
    policy = build_policy(name)
    items_by_tick, queries_by_tick = build_simple_scenario()
    bundles1 = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=100)
    bundles2 = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=100)
    hashes1 = [b.bundle_hash for _, b in bundles1]
    hashes2 = [b.bundle_hash for _, b in bundles2]
    assert hashes1 == hashes2


@pytest.mark.parametrize("name", ALL_BASELINES)
def test_conformance_deterministic_across_runs(name):
    policy1 = build_policy(name)
    policy2 = build_policy(name)
    items_by_tick, queries_by_tick = build_simple_scenario(n_items=50)
    b1 = run_trace(policy1, items_by_tick, queries_by_tick, budget_tokens=200, seed_root="seedA")
    b2 = run_trace(policy2, items_by_tick, queries_by_tick, budget_tokens=200, seed_root="seedA")
    assert [b.bundle_hash for _, b in b1] == [b.bundle_hash for _, b in b2]


def test_b0_full_recall_is_perfect():
    policy = build_policy("B0")
    items_by_tick, queries_by_tick = build_simple_scenario(n_items=40)
    bundles = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=10)  # tiny budget, ignored
    q, bundle = bundles[0]
    bundle_ids = {it.id for it in bundle.items}
    assert q.required_item_ids <= bundle_ids


def test_b1_window_keeps_most_recent():
    policy = build_policy("B1")
    items_by_tick = {i: [make_item(f"i{i}", i, tokens=10)] for i in range(20)}
    queries_by_tick = {20: [Query(id="q1", tick=20, topics=(), entities=(), required_item_ids=frozenset())]}
    bundles = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=50)
    _, bundle = bundles[0]
    ids = {it.id for it in bundle.items}
    # budget 50 / tokens 10 => 5 most recent items: i15..i19
    assert ids == {"i15", "i16", "i17", "i18", "i19"}


def test_b2_rag_includes_top_similarity_item_when_budget_allows():
    policy = build_policy("B2")
    items_by_tick = {
        0: [make_item("match", 0, tokens=10, topics=("target",))],
        1: [make_item("nomatch1", 1, tokens=10, topics=("other",))],
        2: [make_item("nomatch2", 2, tokens=10, topics=("other2",))],
    }
    queries_by_tick = {3: [Query(id="q1", tick=3, topics=("target",), entities=(),
                                  required_item_ids=frozenset({"match"}))]}
    bundles = run_trace(policy, items_by_tick, queries_by_tick, budget_tokens=100)
    _, bundle = bundles[0]
    ids = {it.id for it in bundle.items}
    assert "match" in ids


def test_b3_summarize_drops_non_retained_old_items():
    policy = build_policy("B3")
    items_by_tick = {i: [make_item(f"i{i}", i, tokens=5, surprise=(i % 10) / 10.0)] for i in range(300)}
    queries_by_tick = {}
    policy.reset(budget_tokens=100000, seed_root="s1",
                 config={"summarize_every": 100, "keep_recent": 50, "retain_fraction": 0.1})
    for t in range(300):
        for item in items_by_tick.get(t, []):
            policy.observe(Observation(tick=t, item=item))
        policy.on_tick(t)
    view = to_view(Query(id="qf", tick=300, topics=(), entities=(), required_item_ids=frozenset()))
    bundle = policy.on_query(view)
    ids = {it.id for it in bundle.items}
    raw_old_ids = {f"i{i}" for i in range(50)}  # ticks 0..49, well before first cutoff
    assert not (raw_old_ids & ids), "old raw items should have been summarized away"
    assert any(it.id.startswith("summary_") for it in bundle.items)
    assert policy.stats()["llm_calls"] >= 1


def test_b4_paging_counts_llm_calls_and_surcharge():
    policy = build_policy("B4")
    items_by_tick = {i: [make_item(f"i{i}", i, tokens=5)] for i in range(250)}
    queries_by_tick = {}
    policy.reset(budget_tokens=100, seed_root="s1", config={"paging_interval": 50})
    for t in range(250):
        for item in items_by_tick.get(t, []):
            policy.observe(Observation(tick=t, item=item))
        policy.on_tick(t)
    stats = policy.stats()
    assert stats["llm_calls"] > 0
    assert stats["paging_token_surcharge_total"] > 0


def test_registry_has_all_baselines():
    for name in ALL_BASELINES:
        assert name in POLICY_REGISTRY


def test_build_unknown_policy_raises():
    with pytest.raises(KeyError):
        build_policy("NOPE")
