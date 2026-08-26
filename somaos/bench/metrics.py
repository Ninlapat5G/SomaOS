"""Per-run metric computation. See plans/02_INTERFACES.md #6 and
plans/wp/WP-08-metrics-runner.md.

Everything here is pure/read-only over a (policy, trace) pair -- it never
re-decides anything the policy already decided, and it never lets a
policy see a Query's required_item_ids (only QueryView is ever passed to
policy.on_query, matching WP-06 rule 2 and enforced by test_layering.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import somaos.broker.policies  # noqa: F401  registers B0-B4, S into POLICY_REGISTRY
from somaos.bench.coverage import Coverage, raw_token_map, resolve_coverage
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


def bundle_coverage(bundle: ContextBundle, raw_tokens: Mapping[str, int]) -> Coverage:
    """Which ids this bundle can answer for, under D-14's page-fault
    rule. Resident items are free; pointer targets are faulted in, in
    the order the policy laid them out, until the unspent budget runs
    out. Takes no Query -- the answer key must not steer which pages get
    faulted (see somaos/bench/coverage.py)."""
    return resolve_coverage(
        bundle.items, budget_tokens=bundle.budget_tokens, raw_tokens=raw_tokens,
    )


def strict_recall_hit(query: Query, bundle: ContextBundle,
                      raw_tokens: Mapping[str, int]) -> bool:
    return query.required_item_ids <= bundle_coverage(bundle, raw_tokens).covered


def partial_recall_value(query: Query, bundle: ContextBundle,
                         raw_tokens: Mapping[str, int]) -> float:
    req = query.required_item_ids
    if not req:
        return 1.0
    covered = bundle_coverage(bundle, raw_tokens).covered
    return len(req & covered) / len(req)


def hit_at_k(query: Query, bundle: ContextBundle, k: int,
             raw_tokens: Mapping[str, int]) -> bool:
    """Same rule applied to the bundle's top-k prefix -- a ranking-quality
    measure, so the prefix pays for its own faults out of the budget the
    prefix leaves unspent."""
    if not query.required_item_ids:
        return True
    cov = resolve_coverage(
        bundle.items[:k], budget_tokens=bundle.budget_tokens, raw_tokens=raw_tokens,
    )
    return query.required_item_ids <= cov.covered


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
    # D-14 page-fault accounting
    page_faults: int
    """Total pointer dereferences that were paid for, across all queries."""
    page_fault_tokens: int
    """Tokens those dereferences cost. Real spend on top of total_tokens."""
    pointer_denied: int
    """Pointer-only required ids the bundle could not afford to fault in."""
    direct_covered: int
    """Required ids satisfied by an item actually resident in the bundle."""
    answered_by_fault: int
    """Required ids satisfied only because a page fault brought them back."""
    required_total: int
    """Required ids seen across all queries (denominator for the rates)."""


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

    raw_tokens = raw_token_map(trace)

    n_queries = 0
    strict_hits = 0
    partial_sum = 0.0
    hitk_hits = {k: 0 for k in HIT_AT_K_VALUES}
    total_tokens = 0
    page_faults = 0
    answered_by_fault = 0
    page_fault_tokens = 0
    pointer_denied = 0
    direct_covered = 0
    required_total = 0

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

            # One coverage resolution per query, reused by every measure
            # below -- it is the single place D-14's rule is applied.
            cov = bundle_coverage(bundle, raw_tokens)
            req = ev.query.required_item_ids
            if req and req <= cov.covered:
                strict_hits += 1
            partial_sum += (len(req & cov.covered) / len(req)) if req else 1.0

            for k in HIT_AT_K_VALUES:
                if hit_at_k(ev.query, bundle, k, raw_tokens):
                    hitk_hits[k] += 1

            total_tokens += bundle.tokens
            page_faults += len(cov.faulted)
            page_fault_tokens += cov.fault_tokens
            pointer_denied += len(cov.deferred)
            # Split the *answered* requirements by how they were served,
            # which is what makes the D-13 loophole visible in the JSONL.
            direct_covered += len(req & cov.resident)
            answered_by_fault += len(req & cov.faulted)
            required_total += len(req)

    strict_recall = strict_hits / n_queries if n_queries else 1.0
    partial_recall = partial_sum / n_queries if n_queries else 1.0
    tokens_per_query = total_tokens / n_queries if n_queries else 0.0
    hit_at_k_out = {str(k): (hitk_hits[k] / n_queries if n_queries else 1.0) for k in HIT_AT_K_VALUES}

    return RunResult(
        n_queries=n_queries, strict_recall=strict_recall, partial_recall=partial_recall,
        tokens_per_query=tokens_per_query, total_tokens=total_tokens,
        hit_at_k=hit_at_k_out, stats=policy.stats(),
        page_faults=page_faults, page_fault_tokens=page_fault_tokens,
        pointer_denied=pointer_denied, direct_covered=direct_covered,
        answered_by_fault=answered_by_fault, required_total=required_total,
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

    # D-14 page-fault accounting. answered_via_pointer_rate is the number
    # that made this amendment necessary: under D-13 policy S sat at
    # 92.6% here while paying nothing for it.
    n_q = run.n_queries
    page_fault_rate = (run.page_faults / n_q) if n_q else 0.0
    page_fault_tokens_per_query = (run.page_fault_tokens / n_q) if n_q else 0.0
    satisfied = run.direct_covered + run.answered_by_fault
    answered_via_pointer_rate = (run.answered_by_fault / satisfied) if satisfied else 0.0
    pointer_denied_rate = (
        run.pointer_denied / (run.page_faults + run.pointer_denied)
        if (run.page_faults + run.pointer_denied) else 0.0
    )
    effective_tokens_per_query = (
        (run.total_tokens + run.page_fault_tokens) / n_q if n_q else 0.0
    )

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
        "page_faults": run.page_faults,
        "page_fault_rate": page_fault_rate,
        "page_fault_tokens": run.page_fault_tokens,
        "page_fault_tokens_per_query": page_fault_tokens_per_query,
        "answered_via_pointer_rate": answered_via_pointer_rate,
        "pointer_denied_rate": pointer_denied_rate,
        "effective_tokens_per_query": effective_tokens_per_query,
        "opt_strict_recall": opt.strict_recall,
        "opt_mode": opt.mode,
        "competitive_ratio": competitive_ratio,
        "surprise_utility_spearman": surprise_utility_spearman,
        "config_hash": config_hash,
    }
