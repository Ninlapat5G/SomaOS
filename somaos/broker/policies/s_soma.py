"""Policy S -- SomaOS policy-driven working set. See
plans/wp/WP-06-policy-s.md, target_SomaOS.md #5.2/#5.4.

Fast path only: surprise-gated encoding (observe) + periodic
reallocation against a *rear-facing* goal (on_tick) + targeted recall
that never touches the query's answer key (on_query, uses QueryView).
No LLM, no consolidation -- that is Phase 5's slow path, not this one.
"""
from __future__ import annotations

import collections
import dataclasses
from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.retention import RetentionWeights, extract_features, retention_score
from somaos.broker.types import (
    ContextBundle,
    EncodeDecision,
    ItemStat,
    MemoryItem,
    Observation,
    QueryView,
    Tier,
)
from somaos.broker.workingset import WorkingSetAllocator

_DEFAULT_WEIGHTS = RetentionWeights(
    w_recency=1.0, w_frequency=0.5, w_relevance=1.5,
    w_surprise=1.5, w_novelty=1.0, w_pinned=3.0, w_recompute=0.2,
)


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _sim(item: MemoryItem, topics: frozenset[str], entities: frozenset[str]) -> float:
    return 0.6 * _jaccard(frozenset(item.topics), topics) + 0.4 * _jaccard(frozenset(item.entities), entities)


@register_policy("S")
class SomaPolicy:
    name = "S"
    ignores_budget = False

    def __init__(
        self,
        *,
        tau_high: float = 0.6,
        merge_threshold: float = 0.5,
        realloc_every: int = 8,
        goal_window: int = 64,
        tau_ticks: int = 32,
        hysteresis: float = 0.05,
        recall_budget_fraction: float = 0.25,
        weights: RetentionWeights | None = None,
    ) -> None:
        self.tau_high = tau_high
        self.merge_threshold = merge_threshold
        self.realloc_every = realloc_every
        self.goal_window = goal_window
        self.tau_ticks = tau_ticks
        self.hysteresis = hysteresis
        self.recall_budget_fraction = recall_budget_fraction
        self.weights = weights or _DEFAULT_WEIGHTS

        self._budget_tokens = 0
        self._items: dict[str, MemoryItem] = {}
        self._stats_by_id: dict[str, ItemStat] = {}
        self._topic_index: dict[str, set[str]] = collections.defaultdict(set)
        self._entity_index: dict[str, set[str]] = collections.defaultdict(set)
        self._recent_topics: collections.deque = collections.deque()
        self._recent_entities: collections.deque = collections.deque()
        self._allocator: WorkingSetAllocator | None = None
        self._current_tick = 0
        self._counters = {
            "encoded": 0.0, "counter_merged": 0.0, "dropped": 0.0,
            "observations_total": 0.0, "llm_calls": 0.0,
        }

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._budget_tokens = budget_tokens
        self._items = {}
        self._stats_by_id = {}
        self._topic_index = collections.defaultdict(set)
        self._entity_index = collections.defaultdict(set)
        self._recent_topics = collections.deque()
        self._recent_entities = collections.deque()
        self._current_tick = 0
        self._counters = {
            "encoded": 0.0, "counter_merged": 0.0, "dropped": 0.0,
            "observations_total": 0.0, "llm_calls": 0.0,
        }

        weights = config.get("weights") if isinstance(config, Mapping) else None
        weights_obj = RetentionWeights.from_json(weights) if weights else self.weights
        self.tau_high = config.get("tau_high", self.tau_high)
        self.merge_threshold = config.get("merge_threshold", self.merge_threshold)
        self.realloc_every = config.get("realloc_every", self.realloc_every)
        self.goal_window = config.get("goal_window", self.goal_window)
        self.tau_ticks = config.get("tau_ticks", self.tau_ticks)
        self.hysteresis = config.get("hysteresis", self.hysteresis)
        self.recall_budget_fraction = config.get(
            "recall_budget_fraction", self.recall_budget_fraction
        )
        if not 0.0 <= self.recall_budget_fraction <= 1.0:
            raise ValueError(
                f"recall_budget_fraction must be in [0, 1], got {self.recall_budget_fraction}"
            )

        self._allocator = WorkingSetAllocator(
            budget_tokens=budget_tokens, weights=weights_obj,
            tau_ticks=self.tau_ticks, hysteresis=self.hysteresis,
        )

    def _nearest_candidate(self, topics: tuple[str, ...], entities: tuple[str, ...]) -> str | None:
        candidate_ids: set[str] = set()
        for t in topics:
            candidate_ids |= self._topic_index.get(t, set())
        for e in entities:
            candidate_ids |= self._entity_index.get(e, set())
        if not candidate_ids:
            return None
        q_topics = frozenset(topics)
        q_entities = frozenset(entities)
        best_id, best_sim = None, -1.0
        for cid in sorted(candidate_ids):
            s = _sim(self._items[cid], q_topics, q_entities)
            if s > best_sim:
                best_id, best_sim = cid, s
        if best_id is not None and best_sim >= self.merge_threshold:
            return best_id
        return None

    def observe(self, obs: Observation) -> EncodeDecision:
        item = obs.item
        self._current_tick = max(self._current_tick, obs.tick)
        self._counters["observations_total"] += 1

        # Track recent topics/entities for the rear-facing "current goal" (on_tick),
        # regardless of whether this observation gets fully encoded.
        self._recent_topics.append((obs.tick, item.topics))
        self._recent_entities.append((obs.tick, item.entities))
        self._prune_recent(obs.tick)

        if item.surprise >= self.tau_high or item.novelty >= 1.0:
            self._items[item.id] = item
            self._stats_by_id[item.id] = ItemStat(last_access_tick=obs.tick, access_count=1,
                                                    tier=Tier.WARM, admitted_tick=obs.tick)
            for t in item.topics:
                self._topic_index[t].add(item.id)
            for e in item.entities:
                self._entity_index[e].add(item.id)
            self._counters["encoded"] += 1
            reason = "novel" if item.novelty >= 1.0 else "surprise_high"
            return EncodeDecision(encoded=True, reason=reason, counter_delta=0)

        nearest = self._nearest_candidate(item.topics, item.entities)
        if nearest is not None:
            stat = self._stats_by_id[nearest]
            stat.access_count += 1
            stat.last_access_tick = obs.tick
            # This observation isn't stored as its own item (that's the point
            # of the counter -- confirmation doesn't need a new episode), but
            # it must still be *recoverable*: fold its id into the absorbing
            # item's source_item_ids so a query asking for exactly this id
            # can still be satisfied by the item that now represents it.
            #
            # Under D-14 that recovery is no longer free: reading through
            # this pointer is a page fault charged at the raw item's token
            # size, and the queue is served in the order the ids were folded
            # in (arrival order). S declares no priority over them, so in
            # practice it recovers almost nothing this way -- which is the
            # honest accounting. It threw the content away.
            #
            # MemoryItem is frozen, so this is a replace-in-place by id, not
            # a mutation of the object a caller might be holding a reference to.
            absorbing = self._items[nearest]
            self._items[nearest] = dataclasses.replace(
                absorbing, source_item_ids=absorbing.source_item_ids + (item.id,)
            )
            self._counters["counter_merged"] += 1
            return EncodeDecision(encoded=False, reason="low_surprise_counter", counter_delta=1)

        self._counters["dropped"] += 1
        return EncodeDecision(encoded=False, reason="filtered", counter_delta=0)

    def _prune_recent(self, now_tick: int) -> None:
        cutoff = now_tick - self.goal_window
        while self._recent_topics and self._recent_topics[0][0] < cutoff:
            self._recent_topics.popleft()
        while self._recent_entities and self._recent_entities[0][0] < cutoff:
            self._recent_entities.popleft()

    def _current_goal(self) -> tuple[frozenset[str], frozenset[str]]:
        topic_counts: dict[str, int] = collections.defaultdict(int)
        entity_counts: dict[str, int] = collections.defaultdict(int)
        for _, topics in self._recent_topics:
            for t in topics:
                topic_counts[t] += 1
        for _, entities in self._recent_entities:
            for e in entities:
                entity_counts[e] += 1
        if not topic_counts and not entity_counts:
            return frozenset(), frozenset()
        top_topics = sorted(topic_counts, key=lambda t: (-topic_counts[t], t))[:5]
        top_entities = sorted(entity_counts, key=lambda e: (-entity_counts[e], e))[:5]
        return frozenset(top_topics), frozenset(top_entities)

    def on_tick(self, tick: int) -> None:
        self._current_tick = tick
        if self.realloc_every <= 0 or tick % self.realloc_every != 0:
            return
        if not self._items:
            return
        goal_topics, goal_entities = self._current_goal()
        candidates = [(item, self._stats_by_id[iid]) for iid, item in self._items.items()]
        self._allocator.allocate(
            now_tick=tick, candidates=candidates,
            goal_topics=goal_topics, goal_entities=goal_entities,
        )

    def on_query(self, q: QueryView) -> ContextBundle:
        """Build the bundle in three passes (WP-12).

        The original single-pass version filled from the working set
        first and gave query-targeted recall whatever was left, which in
        practice was nothing: diagnostics on uniform/dev-01 showed the
        working set consuming 99.9% of bundle tokens while only 51% of
        the items a query needed ever reached the bundle -- despite 100%
        of them being in the store. The working set is chosen against a
        *rear-facing* goal (topics seen recently, on_tick), so it is not
        even trying to answer the question being asked.

        So targeted recall now gets first claim on `recall_budget_fraction`
        of the budget, the working set fills the rest, and anything still
        unspent goes back to recall. Reserving budget cannot make the
        bundle exceed it -- the quota is a floor on recall's share, not
        an extra allowance.
        """
        q_topics = frozenset(q.topics)
        q_entities = frozenset(q.entities)

        resident_ids = self._allocator.resident_ids if self._allocator else frozenset()
        resident_ids = resident_ids & self._items.keys()

        by_similarity = sorted(
            self._items,
            key=lambda iid: (-_sim(self._items[iid], q_topics, q_entities), iid),
        )

        chosen: list[str] = []
        taken: set[str] = set()
        total = 0

        def _admit(iid: str, ceiling: int) -> bool:
            nonlocal total
            if iid in taken:
                return False
            tokens = self._items[iid].tokens
            if total + tokens > ceiling:
                return False
            chosen.append(iid)
            taken.add(iid)
            total += tokens
            return True

        # Pass 1 -- targeted recall, inside its reserved quota. Only items
        # with some similarity to the query qualify; a zero-similarity
        # item is not "recall", it is just filler, and the working set
        # pass below is the honest place for that.
        quota = int(self._budget_tokens * self.recall_budget_fraction)
        if quota > 0:
            for iid in by_similarity:
                if _sim(self._items[iid], q_topics, q_entities) <= 0.0:
                    break
                _admit(iid, quota)

        # Pass 2 -- working set fills whatever the quota left over.
        for iid in sorted(resident_ids):
            _admit(iid, self._budget_tokens)

        # Pass 3 -- recall again for any budget still unspent.
        for iid in by_similarity:
            _admit(iid, self._budget_tokens)

        for iid in chosen:
            self._stats_by_id[iid].last_access_tick = q.tick
            self._stats_by_id[iid].access_count += 1

        ordered = sorted(chosen, key=lambda iid: (self._items[iid].kind != "semantic", self._items[iid].created_tick, iid))
        items = tuple(self._items[iid] for iid in ordered)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        out = dict(self._counters)
        # Store size, not working-set size: eviction only moves items out
        # of the context bundle, never out of the store. KC3's D-07
        # condition is stated in terms of N_items, so it needs this.
        out["store_items"] = float(len(self._items))
        if self._allocator is not None:
            out["evictions"] = float(len(self._allocator.eviction_log))
            out["churn_rate"] = self._allocator.churn_rate()
        return out
