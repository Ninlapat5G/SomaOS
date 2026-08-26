"""B2 - naive RAG, top-k by similarity to the query. See
plans/wp/WP-05-policies.md.

Similarity here uses ground-truth tags (topics/entities) directly rather
than a real embedding model, because Phase 0 has no LLM/embedding stack
(D-01). This makes B2 *more accurate* than a real vector-RAG baseline
would be -- if policy S still beats it, that is a stronger result, not a
weaker one. Real embedding-based RAG is deferred to Phase 0.5 / L2."""
from __future__ import annotations

from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, EncodeDecision, MemoryItem, Observation, QueryView


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _similarity(item: MemoryItem, q_topics: frozenset[str], q_entities: frozenset[str]) -> float:
    topic_sim = _jaccard(frozenset(item.topics), q_topics)
    entity_sim = _jaccard(frozenset(item.entities), q_entities)
    return 0.6 * topic_sim + 0.4 * entity_sim


@register_policy("B2")
class NaiveRagPolicy:
    name = "B2"
    ignores_budget = False

    def __init__(self) -> None:
        self._budget_tokens = 0
        self._items: dict[str, MemoryItem] = {}
        self._order: list[str] = []
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0}

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._budget_tokens = budget_tokens
        self._items = {}
        self._order = []
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0}

    def observe(self, obs: Observation) -> EncodeDecision:
        item = obs.item
        self._items[item.id] = item
        self._order.append(item.id)
        self._stats["encoded"] += 1
        return EncodeDecision(encoded=True, reason="surprise_high")

    def on_tick(self, tick: int) -> None:
        pass

    def on_query(self, q: QueryView) -> ContextBundle:
        q_topics = frozenset(q.topics)
        q_entities = frozenset(q.entities)
        ranked = sorted(
            self._order,
            key=lambda iid: (-_similarity(self._items[iid], q_topics, q_entities), iid),
        )
        chosen: list[str] = []
        total = 0
        for iid in ranked:
            tokens = self._items[iid].tokens
            if total + tokens > self._budget_tokens:
                break
            chosen.append(iid)
            total += tokens
        items = tuple(self._items[iid] for iid in chosen)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        return dict(self._stats)
