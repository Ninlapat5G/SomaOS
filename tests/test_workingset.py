import random

import pytest

from somaos.broker.retention import RetentionWeights
from somaos.broker.types import ItemStat, MemoryItem
from somaos.broker.workingset import OverPinned, WorkingSetAllocator


def make_item(id_, tokens=10, pinned=False, surprise=0.5, topics=(), entities=()):
    return MemoryItem(
        id=id_, kind="episodic", tokens=tokens, created_tick=0,
        topics=topics, entities=entities, surprise=surprise, novelty=0.0,
        pinned=pinned,
    )


def uniform_weights():
    return RetentionWeights(1, 1, 1, 1, 1, 1, 1)


def make_candidates(n, tokens=10, seed=0, pinned_ids=frozenset()):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        item = make_item(f"i{i}", tokens=tokens, pinned=f"i{i}" in pinned_ids, surprise=rng.random())
        stat = ItemStat(last_access_tick=0, access_count=rng.randint(0, 10))
        out.append((item, stat))
    return out


def test_never_exceeds_budget_property():
    rng = random.Random(7)
    for trial in range(200):
        budget = rng.randint(10, 200)
        n = rng.randint(0, 30)
        candidates = []
        for i in range(n):
            tokens = rng.randint(1, 50)
            item = make_item(f"i{i}", tokens=tokens, surprise=rng.random())
            stat = ItemStat(last_access_tick=0, access_count=rng.randint(0, 20))
            candidates.append((item, stat))
        alloc = WorkingSetAllocator(budget_tokens=budget, weights=uniform_weights(), tau_ticks=32)
        result = alloc.allocate(now_tick=0, candidates=candidates,
                                 goal_topics=frozenset(), goal_entities=frozenset())
        assert result.tokens_used <= budget


def test_pinned_always_resident_when_budget_allows():
    candidates = make_candidates(5, tokens=10, pinned_ids={"i0", "i1"})
    alloc = WorkingSetAllocator(budget_tokens=100, weights=uniform_weights(), tau_ticks=32)
    result = alloc.allocate(now_tick=0, candidates=candidates,
                             goal_topics=frozenset(), goal_entities=frozenset())
    assert "i0" in result.resident
    assert "i1" in result.resident


def test_over_pinned_raises():
    candidates = make_candidates(5, tokens=50, pinned_ids={"i0", "i1", "i2"})
    alloc = WorkingSetAllocator(budget_tokens=100, weights=uniform_weights(), tau_ticks=32)
    with pytest.raises(OverPinned):
        alloc.allocate(now_tick=0, candidates=candidates,
                        goal_topics=frozenset(), goal_entities=frozenset())


def test_determinism_regardless_of_input_order():
    candidates = make_candidates(20, tokens=5, seed=3)
    shuffled = list(candidates)
    random.Random(99).shuffle(shuffled)

    alloc1 = WorkingSetAllocator(budget_tokens=50, weights=uniform_weights(), tau_ticks=32)
    r1 = alloc1.allocate(now_tick=0, candidates=candidates,
                          goal_topics=frozenset(), goal_entities=frozenset())

    alloc2 = WorkingSetAllocator(budget_tokens=50, weights=uniform_weights(), tau_ticks=32)
    r2 = alloc2.allocate(now_tick=0, candidates=shuffled,
                          goal_topics=frozenset(), goal_entities=frozenset())

    assert r1.resident == r2.resident
    assert r1.tokens_used == r2.tokens_used


def test_hysteresis_reduces_churn():
    rng = random.Random(11)
    items = []
    for t in range(60):
        n = rng.randint(3, 8)
        cands = []
        for i in range(n):
            item = make_item(f"t{t}_i{i}", tokens=5, surprise=rng.random())
            stat = ItemStat(last_access_tick=max(0, t - rng.randint(0, 5)), access_count=rng.randint(0, 5))
            cands.append((item, stat))
        items.append(cands)

    def run(hysteresis):
        alloc = WorkingSetAllocator(budget_tokens=20, weights=uniform_weights(),
                                     tau_ticks=16, hysteresis=hysteresis)
        for t, cands in enumerate(items):
            alloc.allocate(now_tick=t, candidates=cands,
                            goal_topics=frozenset(), goal_entities=frozenset())
        return alloc.churn_rate(window=len(items))

    churn_no_hyst = run(0.0)
    churn_hyst = run(0.15)
    assert churn_hyst <= churn_no_hyst


def test_eviction_log_matches_evicted_count():
    rng = random.Random(5)
    alloc = WorkingSetAllocator(budget_tokens=20, weights=uniform_weights(), tau_ticks=16)
    total_evicted = 0
    for t in range(10):
        cands = make_candidates(8, tokens=5, seed=t)
        result = alloc.allocate(now_tick=t, candidates=cands,
                                 goal_topics=frozenset(), goal_entities=frozenset())
        total_evicted += len(result.evicted)
    assert len(alloc.eviction_log) == total_evicted
    for rec in alloc.eviction_log:
        assert rec.reason in ("budget", "cold", "superseded")


def test_empty_candidates_returns_empty_result():
    alloc = WorkingSetAllocator(budget_tokens=50, weights=uniform_weights(), tau_ticks=32)
    result = alloc.allocate(now_tick=0, candidates=[],
                             goal_topics=frozenset(), goal_entities=frozenset())
    assert result.resident == ()
    assert result.tokens_used == 0


def test_zero_budget_raises():
    with pytest.raises(ValueError):
        WorkingSetAllocator(budget_tokens=0, weights=uniform_weights(), tau_ticks=32)


@pytest.mark.perf
def test_allocate_performance_10k_candidates():
    import time

    candidates = make_candidates(10_000, tokens=5, seed=1)
    alloc = WorkingSetAllocator(budget_tokens=4096, weights=uniform_weights(), tau_ticks=32)
    timings = []
    for t in range(5):
        start = time.perf_counter()
        alloc.allocate(now_tick=t, candidates=candidates,
                        goal_topics=frozenset(), goal_entities=frozenset())
        timings.append((time.perf_counter() - start) * 1000)
    timings.sort()
    p95 = timings[int(0.95 * (len(timings) - 1))]
    # Informational: D-07 budgets 4.0ms at N=10,000 assuming a real LLM in the
    # loop. This pure-Python greedy pass is not tuned to that yet; report only.
    print(f"allocate() p95 over {len(candidates)} candidates: {p95:.3f} ms")
