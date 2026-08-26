"""B0 - full context. Keeps everything, ignores the budget. Upper bound on
quality and cost (see plans/00_PHASE0_MASTER_PLAN.md #3, table in
target_SomaOS.md #7.2). If B0 does not get strict_recall == 1.0 on some
trace, the trace's ground truth is broken, not B0 (WP-02 #5.4)."""
from __future__ import annotations

from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, EncodeDecision, MemoryItem, Observation, QueryView


@register_policy("B0")
class FullContextPolicy:
    name = "B0"
    ignores_budget = True

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
        items = tuple(self._items[iid] for iid in self._order)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        return dict(self._stats)
