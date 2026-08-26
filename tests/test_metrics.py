import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.metrics import (
    OracleViolation,
    build_metric_row,
    bundle_coverage,
    hit_at_k,
    partial_recall_value,
    spearman,
    strict_recall_hit,
)
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.types import ContextBundle, MemoryItem, Query


def make_item(id_, tokens=10, source_item_ids=()):
    return MemoryItem(id=id_, kind="episodic", tokens=tokens, created_tick=0,
                       topics=(), entities=(), surprise=0.5, novelty=0.0,
                       source_item_ids=source_item_ids)


RAW = {f"i{i}": 10 for i in range(10)}


def test_pointer_coverage_requires_paying_the_page_fault_d14():
    """A summary that points at i1 can still answer for it -- but only
    by faulting i1's 10 raw tokens in out of the leftover budget."""
    summary = make_item("summary_0", tokens=5, source_item_ids=("i1", "i2"))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(summary,))
    cov = bundle_coverage(bundle, RAW)
    assert cov.resident == frozenset({"summary_0"})
    assert cov.faulted == frozenset({"i1", "i2"})
    assert cov.deferred == frozenset()
    assert cov.fault_tokens == 20
    assert cov.residual_tokens == 95


def test_pointer_coverage_denied_when_budget_is_spent():
    """Same bundle, no headroom: the pointers are worthless. This is the
    case D-13 scored as a perfect hit."""
    summary = make_item("summary_0", tokens=98, source_item_ids=("i1", "i2"))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(summary,))
    q = Query(id="q", tick=0, topics=(), entities=(),
              required_item_ids=frozenset({"i1", "i2"}))
    cov = bundle_coverage(bundle, RAW)
    assert cov.faulted == frozenset()
    assert cov.deferred == frozenset({"i1", "i2"})
    assert strict_recall_hit(q, bundle, RAW) is False


def test_pointer_hoarding_cannot_buy_recall():
    """The exploit D-14 exists to close: one tiny item carrying a receipt
    for every id in the world. Under D-13 this scored 1.0 for free."""
    hoarder = make_item("hoard", tokens=1, source_item_ids=tuple(f"i{i}" for i in range(10)))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=20, items=(hoarder,))
    q = Query(id="q", tick=0, topics=(), entities=(),
              required_item_ids=frozenset(f"i{i}" for i in range(10)))
    assert strict_recall_hit(q, bundle, RAW) is False
    # 19 tokens of headroom buys exactly one 10-token page, not ten.
    assert partial_recall_value(q, bundle, RAW) == 0.1


def test_resident_items_are_free():
    item = make_item("i1", tokens=95)
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(item,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i1"}))
    cov = bundle_coverage(bundle, RAW)
    assert cov.resident == frozenset({"i1"})
    assert cov.fault_tokens == 0
    assert strict_recall_hit(q, bundle, RAW) is True


def test_strict_recall_hit_false_when_not_covered():
    item = make_item("i1")
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(item,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i2"}))
    assert strict_recall_hit(q, bundle, RAW) is False


def test_partial_recall_value():
    item = make_item("i1")
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(item,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i1", "i2"}))
    assert partial_recall_value(q, bundle, RAW) == 0.5


def test_partial_recall_empty_required_is_one():
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=())
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset())
    assert partial_recall_value(q, bundle, RAW) == 1.0


def test_hit_at_k_respects_prefix():
    items = tuple(make_item(f"i{i}") for i in range(5))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=1000, items=items)
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i3"}))
    assert hit_at_k(q, bundle, 1, RAW) is False
    assert hit_at_k(q, bundle, 4, RAW) is True


def test_spearman_perfect_positive():
    assert abs(spearman([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-9


def test_spearman_perfect_negative():
    assert abs(spearman([1, 2, 3, 4], [4, 3, 2, 1]) - (-1.0)) < 1e-9


def test_spearman_degenerate_returns_zero():
    assert spearman([1], [1]) == 0.0
    assert spearman([1, 1, 1], [1, 2, 3]) == 0.0


def test_build_metric_row_schema_fields():
    trace = generate(from_regime("uniform", "metrics-01", n_ticks=400))
    row = build_metric_row(
        policy_name="B1", regime="uniform", trace=trace, budget_tokens=1024,
        tau_ticks=32, seed_root="metrics-01", seed_split="dev", opt_mode="exact_belady",
    )
    required_keys = {
        "run_id", "policy", "regime", "seed_root", "seed_split", "trace_id",
        "budget_tokens", "tau_ticks", "n_ticks", "n_queries", "strict_recall",
        "partial_recall", "tokens_per_query", "total_tokens", "llm_calls",
        "llm_call_ratio", "hit_at_k", "context_churn_rate", "thrash_indicator",
        "encode_rate", "evictions", "opt_strict_recall", "opt_mode",
        "competitive_ratio", "surprise_utility_spearman", "config_hash",
        "page_faults", "page_fault_rate", "page_fault_tokens",
        "page_fault_tokens_per_query", "answered_via_pointer_rate",
        "pointer_denied_rate", "effective_tokens_per_query",
    }
    assert required_keys <= set(row.keys())
    assert 0.0 <= row["strict_recall"] <= 1.0
    assert row["competitive_ratio"] is None or row["competitive_ratio"] <= 1.0 + 1e-9


def test_build_metric_row_b0_gets_perfect_recall():
    trace = generate(from_regime("bursty", "metrics-02", n_ticks=400))
    row = build_metric_row(
        policy_name="B0", regime="bursty", trace=trace, budget_tokens=1,
        tau_ticks=32, seed_root="metrics-02", seed_split="dev", opt_mode="upper_bound",
    )
    assert row["strict_recall"] == 1.0


def test_no_oracle_violation_across_policies_and_regimes():
    import somaos.broker.policies  # noqa: F401

    for regime in ["uniform", "variable", "high_noise"]:
        trace = generate(from_regime(regime, "metrics-03", n_ticks=600))
        opt_mode = "exact_belady" if regime == "uniform" else "upper_bound"
        for policy_name in ["B0", "B1", "B2", "B3", "B4", "S"]:
            row = build_metric_row(
                policy_name=policy_name, regime=regime, trace=trace, budget_tokens=2048,
                tau_ticks=32, seed_root="metrics-03", seed_split="dev", opt_mode=opt_mode,
            )
            assert row["competitive_ratio"] is None or row["competitive_ratio"] <= 1.0 + 1e-9
