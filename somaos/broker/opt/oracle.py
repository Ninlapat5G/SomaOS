"""OPT oracle harness. See plans/wp/WP-07-opt-oracle.md and
plans/01_DECISIONS.md D-09.

Item-size caveat (read before trusting a number out of this module):
Belady's MIN is optimal for the *classical* single-item-per-request
paging problem. Our queries can in principle require several items at
once ("all-or-nothing"), which is a harder, generally NP-hard problem
(generalized caching). This generator only ever emits single-item
queries, so `exact_belady` refuses multi-item queries outright rather
than silently returning a number that only looks exact. For anything
with item sizes that differ across items, use `upper_bound` instead --
it is a sound (if loose) per-query feasibility bound, so any
`competitive_ratio` computed against it is a conservative
(understated) estimate, never an overstated one.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

from somaos.broker.types import Trace


class TraceTooLarge(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OptResult:
    mode: Literal["exact_belady", "upper_bound", "brute_force"]
    strict_recall: float
    partial_recall: float
    tokens_per_query: float
    per_query: tuple[dict, ...]


def _collect_items(trace: Trace) -> dict:
    items = {}
    for ev in trace.events:
        if ev.kind == "observe":
            items[ev.observation.item.id] = ev.observation.item
    return items


def next_required_map(trace: Trace) -> dict[str, list[int]]:
    """item_id -> sorted list of *query indices* (0-based, in trace order,
    not raw ticks) at which it is required. Query index, not tick, is the
    right unit here: it gives a clean total order even if two queries ever
    land on the same tick."""
    result: dict[str, list[int]] = {}
    qi = 0
    for ev in trace.events:
        if ev.kind == "query":
            for iid in ev.query.required_item_ids:
                result.setdefault(iid, []).append(qi)
            qi += 1
    return result


def _upper_bound(trace: Trace, budget_tokens: int) -> OptResult:
    items = _collect_items(trace)
    n_q = 0
    n_ok = 0
    partial_sum = 0.0
    total_tokens = 0
    per_query = []
    for ev in trace.events:
        if ev.kind != "query":
            continue
        n_q += 1
        req = ev.query.required_item_ids
        req_tokens = sum(items[iid].tokens for iid in req if iid in items)
        fits = req_tokens <= budget_tokens
        per_query.append({
            "query_id": ev.query.id, "strict": 1.0 if fits else 0.0,
            "partial": 1.0 if fits else 0.0, "tokens_required": req_tokens,
        })
        n_ok += 1 if fits else 0
        partial_sum += 1.0 if fits else 0.0
        total_tokens += req_tokens
    strict = n_ok / n_q if n_q else 1.0
    partial = partial_sum / n_q if n_q else 1.0
    avg_tokens = total_tokens / n_q if n_q else 0.0
    return OptResult(mode="upper_bound", strict_recall=strict, partial_recall=partial,
                      tokens_per_query=avg_tokens, per_query=tuple(per_query))


def _belady_variant(trace: Trace, budget_tokens: int, mode: str) -> OptResult:
    items = _collect_items(trace)
    token_values = {it.tokens for it in items.values()}
    if len(token_values) > 1:
        raise ValueError(
            f"{mode} requires uniform item token size (got {sorted(token_values)}); "
            "use mode='upper_bound' for variable-size items (D-09)"
        )
    for ev in trace.events:
        if ev.kind == "query" and len(ev.query.required_item_ids) > 1:
            raise ValueError(
                f"{mode} only supports single-item queries (Belady is proven "
                "optimal there); this trace has a multi-item query "
                f"({ev.query.id}). Use mode='upper_bound' instead."
            )

    token_size = next(iter(token_values)) if token_values else 1
    capacity = budget_tokens // token_size if token_size > 0 else 0

    nrm = next_required_map(trace)
    ever_required = set(nrm.keys())

    def next_use_after(iid: str, qi: int) -> float:
        lst = nrm.get(iid, [])
        idx = bisect.bisect_right(lst, qi)
        return lst[idx] if idx < len(lst) else math.inf

    resident: set[str] = set()
    qi = -1
    n_q = 0
    n_ok = 0
    partial_sum = 0.0
    total_tokens = 0
    per_query = []

    for ev in trace.events:
        if ev.kind == "observe":
            iid = ev.observation.item.id
            if iid not in ever_required or iid in resident:
                continue
            if len(resident) < capacity:
                resident.add(iid)
            # else: full -> don't pre-admit; query time handles eviction.
        elif ev.kind == "query":
            qi += 1
            n_q += 1
            req = set(ev.query.required_item_ids)
            req_tokens = sum(items[iid].tokens for iid in req if iid in items)

            if req_tokens > budget_tokens or len(req) > capacity:
                hit = len(req & resident)
                per_query.append({"query_id": ev.query.id, "strict": 0.0,
                                   "partial": hit / len(req) if req else 1.0,
                                   "tokens_required": req_tokens})
                partial_sum += per_query[-1]["partial"]
                total_tokens += req_tokens
                continue

            missing = req - resident
            for iid in missing:
                if len(resident) < capacity:
                    resident.add(iid)
                else:
                    evictable = [x for x in resident if x not in req]
                    if not evictable:
                        break
                    farthest = max(evictable, key=lambda x: (next_use_after(x, qi), x))
                    resident.discard(farthest)
                    resident.add(iid)

            satisfied = req <= resident
            hit = len(req & resident)
            per_query.append({"query_id": ev.query.id, "strict": 1.0 if satisfied else 0.0,
                               "partial": hit / len(req) if req else 1.0,
                               "tokens_required": req_tokens})
            n_ok += 1 if satisfied else 0
            partial_sum += per_query[-1]["partial"]
            total_tokens += req_tokens

    strict = n_ok / n_q if n_q else 1.0
    partial = partial_sum / n_q if n_q else 1.0
    avg_tokens = total_tokens / n_q if n_q else 0.0
    return OptResult(mode=mode, strict_recall=strict, partial_recall=partial,
                      tokens_per_query=avg_tokens, per_query=tuple(per_query))


def opt_offline(trace: Trace, *, budget_tokens: int,
                 mode: Literal["exact_belady", "upper_bound", "near_optimal"]) -> OptResult:
    if mode == "upper_bound":
        return _upper_bound(trace, budget_tokens)
    if mode in ("exact_belady", "near_optimal"):
        return _belady_variant(trace, budget_tokens, mode)
    raise ValueError(f"unknown mode {mode!r}")


def brute_force_optimal(trace: Trace, *, budget_tokens: int,
                         max_items: int = 12, max_capacity: int = 4) -> OptResult:
    """Exact DP over resident-set states. Used only to validate
    `exact_belady` on tiny traces (plans/wp/WP-07-opt-oracle.md #Mode 1) --
    not meant for real benchmark runs. Optimizes strict-satisfaction count
    only; partial_recall is reported equal to strict_recall here (this
    function is a correctness oracle for the validation test, not a
    reporting tool)."""
    items = _collect_items(trace)
    if len(items) > max_items:
        raise TraceTooLarge(f"{len(items)} items > max_items={max_items}")
    token_values = {it.tokens for it in items.values()}
    if len(token_values) > 1:
        raise ValueError("brute_force_optimal assumes uniform item token size")
    token_size = next(iter(token_values)) if token_values else 1
    capacity = budget_tokens // token_size if token_size > 0 else 0
    if capacity > max_capacity:
        raise TraceTooLarge(f"capacity {capacity} > max_capacity={max_capacity}")

    dp: dict[frozenset, int] = {frozenset(): 0}
    n_q = 0

    for ev in trace.events:
        if ev.kind == "observe":
            iid = ev.observation.item.id
            new_dp: dict[frozenset, int] = {}
            for state, score in dp.items():
                if score > new_dp.get(state, -1):
                    new_dp[state] = score
                if iid in state:
                    continue
                if len(state) < capacity:
                    ns = state | {iid}
                    if score > new_dp.get(ns, -1):
                        new_dp[ns] = score
                else:
                    for evict in state:
                        ns = (state - {evict}) | {iid}
                        if score > new_dp.get(ns, -1):
                            new_dp[ns] = score
            dp = new_dp
        elif ev.kind == "query":
            n_q += 1
            req = frozenset(ev.query.required_item_ids)
            new_dp: dict[frozenset, int] = {}
            for state, score in dp.items():
                if score > new_dp.get(state, -1):
                    new_dp[state] = score  # option: don't satisfy this one
                missing = req - state
                if not missing:
                    if score + 1 > new_dp.get(state, -1):
                        new_dp[state] = score + 1
                    continue
                if len(missing) > capacity:
                    continue
                evictable = list(state - req)
                free_slots = capacity - len(state)
                need_evict = max(0, len(missing) - free_slots)
                if need_evict > len(evictable):
                    continue
                if need_evict == 0:
                    ns = state | missing
                    if score + 1 > new_dp.get(ns, -1):
                        new_dp[ns] = score + 1
                else:
                    for combo in combinations(evictable, need_evict):
                        ns = (state - set(combo)) | missing
                        if score + 1 > new_dp.get(ns, -1):
                            new_dp[ns] = score + 1
            dp = new_dp

    best = max(dp.values()) if dp else 0
    strict = best / n_q if n_q else 1.0
    return OptResult(mode="brute_force", strict_recall=strict, partial_recall=strict,
                      tokens_per_query=0.0, per_query=())
