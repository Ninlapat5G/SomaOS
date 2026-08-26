"""B4 - LLM-managed paging, cost-model proxy (MemGPT-style). See
plans/00_PHASE0_MASTER_PLAN.md #3.4.

*** This is NOT a reimplementation of MemGPT/Letta. *** Phase 0 has no
LLM (D-01), so "the LLM decides what to page" is replaced by a fixed
recency heuristic, with a modeled per-paging-event cost (llm_calls,
token surcharge) standing in for the real prompt MemGPT would send.
Its purpose here is to compare *cost and determinism*, not quality,
against a real LLM-paging system -- that comparison happens at Phase
0.5 / L2 once an actual LLM is in the loop. Any report referencing B4
must repeat this caveat (plans/wp/WP-09-report-gate.md).
"""
from __future__ import annotations

from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, EncodeDecision, MemoryItem, Observation, QueryView


@register_policy("B4")
class LlmPagingProxyPolicy:
    name = "B4"
    ignores_budget = False

    def __init__(
        self,
        *,
        paging_interval: int = 100,
        paging_token_surcharge: float = 50.0,
    ) -> None:
        self.paging_interval = paging_interval
        self.paging_token_surcharge = paging_token_surcharge

        self._budget_tokens = 0
        self._items: dict[str, MemoryItem] = {}
        self._last_access: dict[str, int] = {}
        self._resident: set[str] = set()
        self._stats = {
            "llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0,
            "paging_token_surcharge_total": 0.0,
        }

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._budget_tokens = budget_tokens
        self._items = {}
        self._last_access = {}
        self._resident = set()
        self._stats = {
            "llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0,
            "paging_token_surcharge_total": 0.0,
        }
        self.paging_interval = config.get("paging_interval", self.paging_interval)
        self.paging_token_surcharge = config.get("paging_token_surcharge", self.paging_token_surcharge)

    def observe(self, obs: Observation) -> EncodeDecision:
        item = obs.item
        self._items[item.id] = item
        self._last_access[item.id] = item.created_tick
        self._resident.add(item.id)
        self._stats["encoded"] += 1
        return EncodeDecision(encoded=True, reason="surprise_high")

    def _resident_tokens(self) -> int:
        return sum(self._items[iid].tokens for iid in self._resident)

    def _page(self, tick: int) -> None:
        self._stats["llm_calls"] += 1
        self._stats["paging_token_surcharge_total"] += self.paging_token_surcharge

        def recency_score(iid: str) -> float:
            age = tick - self._last_access.get(iid, self._items[iid].created_tick)
            return 1.0 / (1.0 + max(0, age))

        ranked = sorted(self._resident, key=lambda iid: (-recency_score(iid), iid))
        kept: set[str] = set()
        total = 0
        for iid in ranked:
            tokens = self._items[iid].tokens
            if total + tokens > self._budget_tokens:
                continue
            kept.add(iid)
            total += tokens
        evicted = self._resident - kept
        self._stats["evictions"] += len(evicted)
        self._resident = kept

    def on_tick(self, tick: int) -> None:
        due = self.paging_interval > 0 and tick > 0 and tick % self.paging_interval == 0
        over_budget = self._resident_tokens() > self._budget_tokens
        if due or over_budget:
            self._page(tick)

    def on_query(self, q: QueryView) -> ContextBundle:
        for iid in self._resident:
            self._last_access[iid] = q.tick
        chosen: list[str] = []
        total = 0
        for iid in sorted(self._resident, key=lambda i: (-self._items[i].created_tick, i)):
            tokens = self._items[iid].tokens
            if total + tokens > self._budget_tokens:
                continue
            chosen.append(iid)
            total += tokens
        chosen.sort(key=lambda iid: self._items[iid].created_tick)
        items = tuple(self._items[iid] for iid in chosen)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        return dict(self._stats)
