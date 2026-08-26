"""Per-run metric computation. See plans/02_INTERFACES.md #6 and
plans/wp/WP-08-metrics-runner.md.

Everything here is pure/read-only over a (policy, trace) pair -- it never
re-decides anything the policy already decided, and it never lets a
policy see a Query's required_item_ids (only QueryView is ever passed to
policy.on_query, matching WP-06 rule 2 and enforced by test_layering.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import somaos.broker.policies  # noqa: F401  registers B0-B4, S into POLICY_REGISTRY
from somaos.bench.trace.generator import ground_truth_utility
from somaos.broker.opt.oracle import opt_offline
from somaos.broker.policy import build_policy
from somaos.broker.types import ContextBundle, Observation, Query, Trace, to_view
from somaos.util.hashing import canonical_json, sha256_str

HIT_AT_K_VALUES = (1, 5, 10)


class OracleViolation(Exception):
    """Raised when a policy scores better than the OPT oracle -- this means
    the harness has a leak or a bug, never that the policy is unusually
    good (plans/HANDOFF_TO_SONNET.md, "signals that must stop you")."""


def bundle_covered_ids(bundle: ContextBundle) -> frozenset[str]:
    """Item ids a bundle can answer for: its own items, plus anything
    listed in their source_item_ids (D-13 -- needed for B3's summary
    items to ever satisfy a query for retained evidence)."""
    ids: set[str] = set()
    for it in bundle.items:
        ids.add(it.id)
        ids.update(it.source_item_ids)
    return frozenset(ids)


def _prefix_covered_ids(bundle: ContextBundle, k: int) -> frozenset[str]:
    ids: set[str] = set()
    for it in bundle.items[:k]:
        ids.add(it.id)
        ids.update(it.source_item_ids)
    return frozenset(ids)


def strict_recall_hit(query: Query, bundle: ContextBundle) -> bool:
    return query.required_item_ids <= bundle_covered_ids(bundle)


def partial_recall_value(query: Query, bundle: ContextBundle) -> float:
    req = query.required_item_ids
    if not req:
        return 1.0
    covered = bundle_covered_ids(bundle)
    return len(req & covered) / len(req)


def hit_at_k(query: Query, bundle: ContextBundle, k: int) -> bool:
    if not query.required_item_ids:
        return True
    return query.required_item_ids <= _prefix_covered_ids(bundle, k)


def spearman(xs: list[float], ys: list[float]) -> float:
    """Manual Spearman rank correlation (stdlib only). Returns 0.0 for
    degenerate inputs (fewer than 2 points, or zero variance)."""
    n = len(xs)
    if n < 2:
        return 0.0

    def rank(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for kk in range(i, j + 1):
                ranks[order[kk]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    var_x = sum((a - mean_rx) ** 2 for a in rx)
    var_y = sum((b - mean_ry) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x ** 0.5 * var_y ** 0.5)


@dataclass(frozen=True, slots=True)
class RunResult:
    n_queries: int
    strict_recall: float
    partial_recall: float
    tokens_per_query: float
    total_tokens: int
    hit_at_k: dict[str, float]
    stats: dict[str, float]


def run_policy_on_trace(
    policy_name: str,
    trace: Trace,
    *,
    budget_tokens: int,
    seed_root: str,
    policy_config: dict | None = None,
) -> RunResult:
    """Drive one policy through one trace event-by-event, exactly like a
    real deployment would: observe -> on_tick -> on_query, in trace
    order. Raises whatever the policy raises (e.g. BudgetExceeded via
    bundle.validate()) rather than swallowing it."""
    policy = build_policy(policy_name)
    policy.reset(budget_tokens=budget_tokens, seed_root=seed_root, config=policy_config or {})

    n_queries = 0
    strict_hits = 0
    partial_sum = 0.0
    hitk_hits = {k: 0 for k in HIT_AT_K_VALUES}
    total_tokens = 0

    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
        if ev.kind == "query":
            view = to_view(ev.query)
            bundle = policy.on_query(view)
            if not policy.ignores_budget:
                bundle.validate()
            n_queries += 1
            if strict_recall_hit(ev.query, bundle):
                strict_hits += 1
            partial_sum += partial_recall_value(ev.query, bundle)
            for k in HIT_AT_K_VALUES:
                if hit_at_k(ev.query, bundle, k):
                    hitk_hits[k] += 1
            total_tokens += bundle.tokens

    strict_recall = strict_hits / n_queries if n_queries else 1.0
    partial_recall = partial_sum / n_queries if n_queries else 1.0
    tokens_per_query = total_tokens / n_queries if n_queries else 0.0
    hit_at_k_out = {str(k): (hitk_hits[k] / n_queries if n_queries else 1.0) for k in HIT_AT_K_VALUES}

    return RunResult(
        n_queries=n_queries, strict_recall=strict_recall, partial_recall=partial_recall,
        tokens_per_query=tokens_per_query, total_tokens=total_tokens,
        hit_at_k=hit_at_k_out, stats=policy.stats(),
    )


def measure_fast_path_ms_per_tick(
    policy_name: str,
    trace: Trace,
    *,
    budget_tokens: int,
    seed_root: str,
    policy_config: dict | None = None,
) -> list[float]:
    """Wall-clock cost of the fast path only (observe + on_tick), one
    sample per tick -- deliberately excludes on_query and trace
    generation, which is what D-07's budget is actually about ("คำนวณ
    retention ทุก tick"). Iterates every tick in [0, n_ticks), including
    ticks with no observations (on_tick must still run for those -- a
    policy might reallocate on a fixed schedule). Not deterministic
    (wall clock); used only for KC3, never written to the deterministic
    results JSONL."""
    import time

    policy = build_policy(policy_name)
    policy.reset(budget_tokens=budget_tokens, seed_root=seed_root, config=policy_config or {})

    obs_by_tick: dict[int, list[Observation]] = {}
    for ev in trace.events:
        if ev.kind == "observe":
            obs_by_tick.setdefault(ev.tick, []).append(Observation(tick=ev.tick, item=ev.observation.item))

    samples: list[float] = []
    for t in range(trace.n_ticks):
        start = time.perf_counter()
        for obs in obs_by_tick.get(t, ()):
            policy.observe(obs)
        policy.on_tick(t)
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def build_metric_row(
    *,
    policy_name: str,
    regime: str,
    trace: Trace,
    budget_tokens: int,
    tau_ticks: int,
    seed_root: str,
    seed_split: str,
    opt_mode: str,
    policy_config: dict | None = None,
) -> dict:
    """Compute one full JSONL row (schema: plans/02_INTERFACES.md #6)."""
    merged_config = dict(policy_config or {})
    merged_config.setdefault("tau_ticks", tau_ticks)

    run = run_policy_on_trace(
        policy_name, trace, budget_tokens=budget_tokens, seed_root=seed_root,
        policy_config=merged_config,
    )

    opt = opt_offline(trace, budget_tokens=budget_tokens, mode=opt_mode)

    if opt.strict_recall > 0:
        competitive_ratio = run.strict_recall / opt.strict_recall
    else:
        competitive_ratio = None

    if competitive_ratio is not None and competitive_ratio > 1.0 + 1e-9:
        raise OracleViolation(
            f"{policy_name} strict_recall={run.strict_recall} exceeds OPT "
            f"({opt_mode})={opt.strict_recall} on regime={regime}, "
            f"budget={budget_tokens}, seed={seed_root}"
        )

    utility = ground_truth_utility(trace)
    items = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    surprises = [it.surprise for it in items]
    utils = [utility.get(it.id, 0.0) for it in items]
    surprise_utility_spearman = spearman(surprises, utils)

    stats = run.stats
    encoded = stats.get("encoded")
    observations_total = stats.get("observations_total")
    encode_rate = (encoded / observations_total) if (encoded is not None and observations_total) else None

    llm_calls = stats.get("llm_calls", 0.0)
    llm_call_ratio = (llm_calls / run.n_queries) if run.n_queries else 0.0

    churn_rate = stats.get("churn_rate", 0.0)
    thrash_indicator = min(1.0, churn_rate / 10.0) * (1.0 - max(0.0, min(1.0, run.strict_recall)))

    config_for_hash = {
        "policy": policy_name, "regime": regime, "budget_tokens": budget_tokens,
        "tau_ticks": tau_ticks, "seed_root": seed_root, "opt_mode": opt_mode,
        "policy_config": merged_config,
    }
    config_hash = sha256_str(canonical_json(config_for_hash))
    run_id = sha256_str(canonical_json({**config_for_hash, "trace_id": trace.trace_id}))

    return {
        "run_id": run_id,
        "policy": policy_name,
        "regime": regime,
        "seed_root": seed_root,
        "seed_split": seed_split,
        "trace_id": trace.trace_id,
        "budget_tokens": budget_tokens,
        "tau_ticks": tau_ticks,
        "n_ticks": trace.n_ticks,
        "n_queries": run.n_queries,
        "strict_recall": run.strict_recall,
        "partial_recall": run.partial_recall,
        "tokens_per_query": run.tokens_per_query,
        "total_tokens": run.total_tokens,
        "llm_calls": llm_calls,
        "llm_call_ratio": llm_call_ratio,
        "hit_at_k": run.hit_at_k,
        "context_churn_rate": churn_rate,
        "thrash_indicator": thrash_indicator,
        "encode_rate": encode_rate,
        "evictions": stats.get("evictions", 0.0),
        "opt_strict_recall": opt.strict_recall,
        "opt_mode": opt.mode,
        "competitive_ratio": competitive_ratio,
        "surprise_utility_spearman": surprise_utility_spearman,
        "config_hash": config_hash,
    }
