import random

import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.opt.oracle import (
    TraceTooLarge,
    brute_force_optimal,
    next_required_map,
    opt_offline,
)
from somaos.broker.policy import build_policy
from somaos.broker.types import MemoryItem, Observation, Query, Trace, TraceEvent, to_view


def make_tiny_trace(seed, n_ticks=30, n_items=8, token_size=10, p_query=0.4, capacity_hint=3):
    """Small single-item-per-query trace with a *fixed* item universe of
    exactly n_items distinct ids, used for exhaustive brute-force
    validation. Each id is minted once (on first pick) and every later
    pick of the same id just re-references the same MemoryItem -- this is
    what keeps the item count bounded regardless of n_ticks; a fresh id
    per observation would blow past brute_force_optimal's max_items."""
    rng = random.Random(seed)
    item_ids = [f"i{i}" for i in range(n_items)]
    events = []
    minted: dict[str, MemoryItem] = {}
    for t in range(n_ticks):
        if rng.random() < 0.5:
            iid = rng.choice(item_ids)
            if iid not in minted:
                minted[iid] = MemoryItem(id=iid, kind="episodic", tokens=token_size, created_tick=t,
                                          topics=(), entities=(), surprise=rng.random(), novelty=0.0)
                events.append(TraceEvent(tick=t, kind="observe",
                                          observation=Observation(tick=t, item=minted[iid])))
        if minted and rng.random() < p_query:
            target = rng.choice(list(minted.keys()))
            q = Query(id=f"q_{t}", tick=t, topics=(), entities=(), required_item_ids=frozenset({target}))
            events.append(TraceEvent(tick=t, kind="query", query=q))
    return Trace(trace_id=f"tiny-{seed}", events=tuple(events), n_ticks=n_ticks, meta={})


# ---------- soundness: OPT must never be beaten by any real policy ----------

@pytest.mark.parametrize("regime", ["uniform", "variable", "long_gap", "bursty", "high_noise"])
@pytest.mark.parametrize("policy_name", ["B0", "B1", "B2", "S"])
def test_opt_never_beaten_by_policy(regime, policy_name):
    trace = generate(from_regime(regime, "opt-check-01", n_ticks=1200))
    budget = 2048

    policy = build_policy(policy_name)
    policy.reset(budget_tokens=budget, seed_root="opt-check-01", config={})
    n_q, n_ok = 0, 0
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            bundle = policy.on_query(to_view(ev.query))
            bundle_ids = {it.id for it in bundle.items}
            n_q += 1
            if ev.query.required_item_ids <= bundle_ids:
                n_ok += 1
    policy_recall = n_ok / n_q if n_q else 1.0

    opt = opt_offline(trace, budget_tokens=budget, mode="upper_bound")
    assert opt.strict_recall >= policy_recall - 1e-9, (
        f"{policy_name} beat OPT upper bound on regime={regime}: "
        f"policy={policy_recall} opt={opt.strict_recall}"
    )


# ---------- boundary conditions ----------

def test_upper_bound_infinite_budget_is_one():
    trace = generate(from_regime("variable", "opt-02", n_ticks=500))
    opt = opt_offline(trace, budget_tokens=10**9, mode="upper_bound")
    assert opt.strict_recall == 1.0


def test_upper_bound_tiny_budget_is_zero():
    trace = generate(from_regime("uniform", "opt-03", n_ticks=500))
    opt = opt_offline(trace, budget_tokens=1, mode="upper_bound")
    assert opt.strict_recall == 0.0


def test_belady_infinite_budget_is_one():
    trace = generate(from_regime("uniform", "opt-04", n_ticks=500))
    opt = opt_offline(trace, budget_tokens=10**9, mode="exact_belady")
    assert opt.strict_recall == 1.0


def test_belady_tiny_budget_is_zero():
    trace = generate(from_regime("uniform", "opt-05", n_ticks=500))
    opt = opt_offline(trace, budget_tokens=1, mode="exact_belady")
    assert opt.strict_recall == 0.0


def test_belady_rejects_variable_token_sizes():
    trace = generate(from_regime("variable", "opt-06", n_ticks=500))
    with pytest.raises(ValueError):
        opt_offline(trace, budget_tokens=2048, mode="exact_belady")


def test_upper_bound_at_least_belady():
    trace = generate(from_regime("uniform", "opt-07", n_ticks=1500))
    budget = 1500
    ub = opt_offline(trace, budget_tokens=budget, mode="upper_bound")
    belady = opt_offline(trace, budget_tokens=budget, mode="exact_belady")
    assert ub.strict_recall >= belady.strict_recall - 1e-9


# ---------- exact_belady vs brute force on tiny traces (the safety-critical check) ----------

@pytest.mark.parametrize("seed", list(range(30)))
def test_belady_matches_brute_force_on_tiny_traces(seed):
    trace = make_tiny_trace(seed, n_ticks=25, n_items=6, token_size=10, p_query=0.5)
    budget = 30  # capacity = 3
    belady = opt_offline(trace, budget_tokens=budget, mode="exact_belady")
    brute = brute_force_optimal(trace, budget_tokens=budget)
    assert abs(belady.strict_recall - brute.strict_recall) < 1e-9, (
        f"seed={seed}: belady={belady.strict_recall} brute={brute.strict_recall} "
        "-- exact_belady is not actually exact for this trace shape; per WP-07, "
        "rename the mode rather than adjusting brute force to match."
    )


def test_brute_force_too_large_raises():
    trace = make_tiny_trace(0, n_ticks=10, n_items=20, token_size=1)
    with pytest.raises(TraceTooLarge):
        brute_force_optimal(trace, budget_tokens=25, max_items=12)


def test_belady_rejects_multi_item_queries():
    item = MemoryItem(id="a", kind="episodic", tokens=10, created_tick=0,
                       topics=(), entities=(), surprise=0.5, novelty=0.0)
    item2 = MemoryItem(id="b", kind="episodic", tokens=10, created_tick=0,
                        topics=(), entities=(), surprise=0.5, novelty=0.0)
    q = Query(id="q1", tick=1, topics=(), entities=(), required_item_ids=frozenset({"a", "b"}))
    trace = Trace(trace_id="multi", events=(
        TraceEvent(tick=0, kind="observe", observation=Observation(tick=0, item=item)),
        TraceEvent(tick=0, kind="observe", observation=Observation(tick=0, item=item2)),
        TraceEvent(tick=1, kind="query", query=q),
    ), n_ticks=2, meta={})
    with pytest.raises(ValueError):
        opt_offline(trace, budget_tokens=100, mode="exact_belady")


def test_next_required_map_basic():
    item = MemoryItem(id="a", kind="episodic", tokens=10, created_tick=0,
                       topics=(), entities=(), surprise=0.5, novelty=0.0)
    q1 = Query(id="q1", tick=1, topics=(), entities=(), required_item_ids=frozenset({"a"}))
    q2 = Query(id="q2", tick=2, topics=(), entities=(), required_item_ids=frozenset({"a"}))
    trace = Trace(trace_id="t", events=(
        TraceEvent(tick=0, kind="observe", observation=Observation(tick=0, item=item)),
        TraceEvent(tick=1, kind="query", query=q1),
        TraceEvent(tick=2, kind="query", query=q2),
    ), n_ticks=3, meta={})
    nrm = next_required_map(trace)
    assert nrm["a"] == [0, 1]


def test_opt_deterministic():
    trace = generate(from_regime("uniform", "opt-08", n_ticks=500))
    r1 = opt_offline(trace, budget_tokens=1024, mode="exact_belady")
    r2 = opt_offline(trace, budget_tokens=1024, mode="exact_belady")
    assert r1.strict_recall == r2.strict_recall
    assert r1.per_query == r2.per_query
