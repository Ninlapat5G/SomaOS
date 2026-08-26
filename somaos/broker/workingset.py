"""Working set allocator. See plans/02_INTERFACES.md #4 and
plans/wp/WP-04-workingset.md.

Greedy knapsack by score/tokens density, with hysteresis to damp churn at
the budget boundary. This is deliberately not optimal -- we compare
against the OPT oracle (somaos.broker.opt.oracle) to quantify the gap,
we do not try to close it here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from somaos.broker.retention import (
    RetentionWeights,
    extract_features,
    retention_score,
)
from somaos.broker.types import ItemStat, MemoryItem, Tier


class OverPinned(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EvictionRecord:
    tick: int
    item_id: str
    score: float
    displaced_by: str | None
    reason: Literal["budget", "cold", "superseded"]


@dataclass(frozen=True, slots=True)
class AllocationResult:
    admitted: tuple[str, ...]
    evicted: tuple[str, ...]
    resident: tuple[str, ...]
    tokens_used: int
    churn: int


class WorkingSetAllocator:
    def __init__(
        self,
        *,
        budget_tokens: int,
        weights: RetentionWeights,
        tau_ticks: int,
        hysteresis: float = 0.0,
        cold_after_ticks: int = 10_000_000,
        churn_window: int = 32,
    ) -> None:
        if budget_tokens <= 0:
            raise ValueError("budget_tokens must be > 0")
        self.budget_tokens = budget_tokens
        self.weights = weights
        self.tau_ticks = tau_ticks
        self.hysteresis = hysteresis
        self.cold_after_ticks = cold_after_ticks
        self.churn_window = churn_window

        self._resident: set[str] = set()
        self._churn_history: list[int] = []
        self._eviction_log: list[EvictionRecord] = []

    def _score(
        self,
        item: MemoryItem,
        stat: ItemStat,
        *,
        now_tick: int,
        goal_topics: frozenset[str],
        goal_entities: frozenset[str],
        max_access_count: int,
    ) -> float:
        feats = extract_features(
            item, stat, now_tick=now_tick, tau_ticks=self.tau_ticks,
            goal_topics=goal_topics, goal_entities=goal_entities,
            max_access_count=max_access_count,
        )
        return retention_score(feats, self.weights)

    def allocate(
        self,
        *,
        now_tick: int,
        candidates: Sequence[tuple[MemoryItem, ItemStat]],
        goal_topics: frozenset[str],
        goal_entities: frozenset[str],
    ) -> AllocationResult:
        if not candidates:
            self._churn_history.append(0)
            self._resident = set()
            return AllocationResult(admitted=(), evicted=(), resident=(), tokens_used=0, churn=0)

        max_access_count = max((s.access_count for _, s in candidates), default=0)

        scored: list[tuple[float, MemoryItem, ItemStat]] = []
        for item, stat in candidates:
            score = self._score(
                item, stat, now_tick=now_tick,
                goal_topics=goal_topics, goal_entities=goal_entities,
                max_access_count=max_access_count,
            )
            scored.append((score, item, stat))

        pinned = [(s, it, st) for s, it, st in scored if it.pinned]
        unpinned = [(s, it, st) for s, it, st in scored if not it.pinned]

        pinned_tokens = sum(it.tokens for _, it, _ in pinned)
        if pinned_tokens > self.budget_tokens:
            raise OverPinned(
                f"pinned items require {pinned_tokens} tokens > budget {self.budget_tokens}"
            )

        def density_key(entry):
            score, item, _ = entry
            d = score / item.tokens if item.tokens > 0 else score
            return (-d, item.id)

        unpinned_sorted = sorted(unpinned, key=density_key)

        prev_resident = set(self._resident)
        prev_score_by_id = {it.id: s for s, it, _ in scored if it.id in prev_resident}

        new_resident: dict[str, tuple[float, MemoryItem]] = {}
        tokens_used = 0
        this_tick_evictions: list[EvictionRecord] = []

        for s, item, _ in pinned:
            new_resident[item.id] = (s, item)
            tokens_used += item.tokens

        for score, item, _ in unpinned_sorted:
            if item.id in new_resident:
                continue
            if tokens_used + item.tokens <= self.budget_tokens:
                new_resident[item.id] = (score, item)
                tokens_used += item.tokens
            else:
                if item.id in prev_resident:
                    continue
                incumbents = sorted(
                    (
                        (s2, iid) for iid, (s2, it2) in new_resident.items()
                        if not it2.pinned and it2.tokens >= item.tokens
                    ),
                    key=lambda t: (t[0], t[1]),
                )
                if not incumbents:
                    continue
                weakest_score, weakest_id = incumbents[0]
                if score > weakest_score + self.hysteresis:
                    weakest_item = new_resident.pop(weakest_id)[1]
                    tokens_used -= weakest_item.tokens
                    this_tick_evictions.append(
                        EvictionRecord(
                            tick=now_tick, item_id=weakest_id, score=weakest_score,
                            displaced_by=item.id, reason="superseded",
                        )
                    )
                    new_resident[item.id] = (score, item)
                    tokens_used += item.tokens

        resident_ids = set(new_resident.keys())
        admitted = sorted(resident_ids - prev_resident)
        evicted_by_budget = sorted(prev_resident - resident_ids - {r.item_id for r in this_tick_evictions})

        for iid in evicted_by_budget:
            score = prev_score_by_id.get(iid, 0.0)
            this_tick_evictions.append(
                EvictionRecord(tick=now_tick, item_id=iid, score=score,
                               displaced_by=None, reason="budget")
            )

        self._eviction_log.extend(this_tick_evictions)
        self._resident = resident_ids
        churn = len(admitted) + len(this_tick_evictions)
        self._churn_history.append(churn)

        resident_sorted = tuple(
            sorted(resident_ids, key=lambda iid: (-new_resident[iid][0], iid))
        )
        all_evicted = tuple(sorted(r.item_id for r in this_tick_evictions))

        return AllocationResult(
            admitted=tuple(admitted),
            evicted=all_evicted,
            resident=resident_sorted,
            tokens_used=tokens_used,
            churn=churn,
        )

    def churn_rate(self, window: int | None = None) -> float:
        w = window or self.churn_window
        recent = self._churn_history[-w:]
        if not recent:
            return 0.0
        return sum(recent) / len(recent)

    def thrash_indicator(self, progress_rate: float) -> float:
        rate = self.churn_rate()
        churn_norm = min(1.0, rate / 10.0)
        return churn_norm * (1.0 - max(0.0, min(1.0, progress_rate)))

    @property
    def eviction_log(self) -> tuple[EvictionRecord, ...]:
        return tuple(self._eviction_log)

    @property
    def resident_ids(self) -> frozenset[str]:
        return frozenset(self._resident)
