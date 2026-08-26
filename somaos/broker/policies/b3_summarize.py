"""B3 - summarize every N ticks. See plans/01_DECISIONS.md D-12 and
plans/wp/WP-05-policies.md.

This is a *deliberate* simulation of a baseline that violates
target_SomaOS.md #4.3 rule 3 ("summary must keep a pointer back to the
raw event"): only the top `retain_fraction` of the old batch (ranked by
surprise) survives as `source_item_ids` on the synthetic summary item:
everything else is lost with no way back. That lossiness is the point
-- it is what SomaOS's surprise-gated encoding claims to avoid doing.
"""
from __future__ import annotations

import math
from typing import Mapping

from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, EncodeDecision, MemoryItem, Observation, QueryView


@register_policy("B3")
class SummarizeEveryNPolicy:
    name = "B3"
    ignores_budget = False

    def __init__(
        self,
        *,
        summarize_every: int = 200,
        keep_recent: int = 100,
        retain_fraction: float = 0.2,
        compression_ratio: float = 0.15,
        chunk_raw_tokens: int = 2000,
    ) -> None:
        self.summarize_every = summarize_every
        self.keep_recent = keep_recent
        self.retain_fraction = retain_fraction
        self.compression_ratio = compression_ratio
        self.chunk_raw_tokens = chunk_raw_tokens

        self._budget_tokens = 0
        self._items: dict[str, MemoryItem] = {}
        self._order: list[str] = []
        self._summary_counter = 0
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0, "summarizations": 0.0}

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._budget_tokens = budget_tokens
        self._items = {}
        self._order = []
        self._summary_counter = 0
        self._stats = {"llm_calls": 0.0, "evictions": 0.0, "encoded": 0.0, "summarizations": 0.0}
        self.summarize_every = config.get("summarize_every", self.summarize_every)
        self.keep_recent = config.get("keep_recent", self.keep_recent)
        self.retain_fraction = config.get("retain_fraction", self.retain_fraction)
        self.compression_ratio = config.get("compression_ratio", self.compression_ratio)
        self.chunk_raw_tokens = config.get("chunk_raw_tokens", self.chunk_raw_tokens)

    def observe(self, obs: Observation) -> EncodeDecision:
        item = obs.item
        self._items[item.id] = item
        self._order.append(item.id)
        self._stats["encoded"] += 1
        return EncodeDecision(encoded=True, reason="surprise_high")

    def on_tick(self, tick: int) -> None:
        if self.summarize_every <= 0 or tick == 0 or tick % self.summarize_every != 0:
            return
        cutoff = tick - self.keep_recent
        old_ids = [iid for iid in self._order if self._items[iid].created_tick < cutoff]
        if len(old_ids) < 2:
            return

        # Summarize in bounded chunks rather than folding the entire
        # backlog into one item. The original single-blob version grew a
        # 10,131-token summary by tick 2000 -- larger than the largest
        # budget in any config -- so it could never enter a bundle, and
        # B3 scored identically to B1 on every run. That made B3 a dead
        # baseline and made D-13, which existed specifically so B3's
        # summaries could answer for retained evidence, never fire once.
        # Chunking keeps D-12's semantics (k items -> 1 lossy summary
        # holding the top-surprise ids) while leaving the summary small
        # enough to actually be used. This strengthens a baseline S is
        # measured against; it cannot flatter S.
        new_summaries: list[MemoryItem] = []
        chunk: list[str] = []
        chunk_tokens = 0

        def _flush_chunk() -> None:
            nonlocal chunk, chunk_tokens
            if len(chunk) < 2:
                # Too small to be worth compressing; leave as raw.
                for iid in chunk:
                    keep_raw.add(iid)
                chunk, chunk_tokens = [], 0
                return
            ranked = sorted(chunk, key=lambda iid: (-self._items[iid].surprise, iid))
            n_retain = max(0, math.ceil(len(ranked) * self.retain_fraction))
            summary_id = f"summary_{self._summary_counter}"
            self._summary_counter += 1
            new_summaries.append(MemoryItem(
                id=summary_id, kind="semantic",
                tokens=max(1, math.ceil(chunk_tokens * self.compression_ratio)),
                created_tick=tick, topics=(), entities=(),
                surprise=0.0, novelty=0.0,
                source_item_ids=tuple(ranked[:n_retain]),
            ))
            chunk, chunk_tokens = [], 0

        keep_raw: set[str] = set()
        for iid in old_ids:  # already in trace order
            chunk.append(iid)
            chunk_tokens += self._items[iid].tokens
            if chunk_tokens >= self.chunk_raw_tokens:
                _flush_chunk()
        _flush_chunk()

        dropped = [iid for iid in old_ids if iid not in keep_raw]
        old_set = set(dropped)
        self._order = [iid for iid in self._order if iid not in old_set]
        for iid in dropped:
            del self._items[iid]

        for summary_item in new_summaries:
            self._items[summary_item.id] = summary_item
            self._order.append(summary_item.id)

        self._stats["llm_calls"] += len(new_summaries)
        self._stats["summarizations"] += len(new_summaries)
        self._stats["evictions"] += len(dropped)

    def on_query(self, q: QueryView) -> ContextBundle:
        chosen: list[str] = []
        total = 0
        for iid in reversed(self._order):
            tokens = self._items[iid].tokens
            if total + tokens > self._budget_tokens:
                continue
            chosen.append(iid)
            total += tokens
        pos = {iid: i for i, iid in enumerate(self._order)}
        chosen.sort(key=lambda iid: pos[iid])
        items = tuple(self._items[iid] for iid in chosen)
        return ContextBundle(query_id=q.id, tick=q.tick, budget_tokens=self._budget_tokens, items=items)

    def stats(self) -> dict[str, float]:
        return dict(self._stats)
