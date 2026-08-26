"""B1 - sliding window, last-K by token budget. Ignores query content
entirely. Simplest baseline (target_SomaOS.md #7.2)."""
from __future__ import annotations

from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, EncodeDecision, MemoryItem, Observation, QueryView


@register_policy("B1")
class SlidingWindowPolicy:
    name = "B1"
    ignores_budget = False

    def __init__(self) -> None:
        self._budget_tokens = 0
        self._items: dict[str, MemoryItem] = {}
        self._order: list[str] = []
        self._pos: dict[str, int] = {}
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0}

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._budget_tokens = budget_tokens
        self._items = {}
        self._order = []
        self._pos = {}
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0}

    def observe(self, obs: Observation) -> EncodeDecision:
        item = obs.item
        self._items[item.id] = item
        self._pos[item.id] = len(self._order)
        self._order.append(item.id)
        self._stats["encoded"] += 1
        return EncodeDecision(encoded=True, reason="surprise_high")

    def on_tick(self, tick: int) -> None:
        pass

    def on_query(self, q: QueryView) -> ContextBundle:
        chosen: list[str] = []
        total = 0
        for iid in reversed(self._order):
            tokens = self._items[iid].tokens
            if total + tokens > self._budget_tokens:
                continue
            chosen.append(iid)
            total += tokens
        chosen.sort(key=lambda iid: self._pos[iid])
        items = tuple(self._items[iid] for iid in chosen)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        return dict(self._stats)
