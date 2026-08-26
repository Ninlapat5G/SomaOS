import pytest

import somaos.broker.policies  # noqa: F401
from somaos.bench.metrics import (
    OracleViolation,
    bundle_covered_ids,
    build_metric_row,
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


def test_bundle_covered_ids_includes_source_item_ids():
    summary = make_item("summary_0", tokens=5, source_item_ids=("i1", "i2"))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(summary,))
    covered = bundle_covered_ids(bundle)
    assert covered == {"summary_0", "i1", "i2"}


def test_strict_recall_hit_via_source_item_ids_d13():
    summary = make_item("summary_0", tokens=5, source_item_ids=("i1",))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(summary,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i1"}))
    assert strict_recall_hit(q, bundle) is True


def test_strict_recall_hit_false_when_not_covered():
    item = make_item("i1")
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(item,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i2"}))
    assert strict_recall_hit(q, bundle) is False


def test_partial_recall_value():
    item = make_item("i1")
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=(item,))
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i1", "i2"}))
    assert partial_recall_value(q, bundle) == 0.5


def test_partial_recall_empty_required_is_one():
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=100, items=())
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset())
    assert partial_recall_value(q, bundle) == 1.0


def test_hit_at_k_respects_prefix():
    items = tuple(make_item(f"i{i}") for i in range(5))
    bundle = ContextBundle(query_id="q", tick=0, budget_tokens=1000, items=items)
    q = Query(id="q", tick=0, topics=(), entities=(), required_item_ids=frozenset({"i3"}))
    assert hit_at_k(q, bundle, 1) is False
    assert hit_at_k(q, bundle, 4) is True


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
