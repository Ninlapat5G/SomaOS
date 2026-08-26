"""Phase 0 data contracts. See plans/02_INTERFACES.md #1 (normative).

Everything here is a frozen dataclass: memory items and traces are
immutable facts; mutable bookkeeping (access counts, tier) lives in
ItemStat instead, kept separately by whichever policy owns it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal, Mapping

from somaos.util.hashing import canonical_json, sha256_str

MemoryKind = Literal["episodic", "semantic", "procedural", "prospective"]


class Tier(IntEnum):
    WORKING = 0
    WARM = 1
    COLD = 2


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    kind: MemoryKind
    tokens: int
    created_tick: int
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    surprise: float
    novelty: float
    pinned: bool = False
    recompute_cost: float = 0.0
    source_item_ids: tuple[str, ...] = ()
    content: str = ""

    def to_jsonable(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "tokens": self.tokens,
            "created_tick": self.created_tick,
            "topics": list(self.topics),
            "entities": list(self.entities),
            "surprise": self.surprise,
            "novelty": self.novelty,
            "pinned": self.pinned,
            "recompute_cost": self.recompute_cost,
            "source_item_ids": list(self.source_item_ids),
            "content": self.content,
        }


@dataclass(slots=True)
class ItemStat:
    last_access_tick: int
    access_count: int = 0
    tier: Tier = Tier.WARM
    admitted_tick: int = 0


@dataclass(frozen=True, slots=True)
class Query:
    id: str
    tick: int
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    required_item_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class QueryView:
    """What a policy is allowed to see. No required_item_ids — that's the answer key."""
    id: str
    tick: int
    topics: tuple[str, ...]
    entities: tuple[str, ...]


def to_view(q: Query) -> QueryView:
    return QueryView(id=q.id, tick=q.tick, topics=q.topics, entities=q.entities)


@dataclass(frozen=True, slots=True)
class Observation:
    tick: int
    item: MemoryItem


@dataclass(frozen=True, slots=True)
class TraceEvent:
    tick: int
    kind: Literal["observe", "query"]
    observation: Observation | None = None
    query: Query | None = None


@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: str
    events: tuple[TraceEvent, ...]
    n_ticks: int
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EncodeDecision:
    encoded: bool
    reason: Literal["surprise_high", "novel", "low_surprise_counter", "filtered"]
    counter_delta: int = 0


@dataclass(frozen=True, slots=True)
class ContextBundle:
    query_id: str
    tick: int
    budget_tokens: int
    items: tuple[MemoryItem, ...]

    @property
    def tokens(self) -> int:
        return sum(it.tokens for it in self.items)

    @property
    def bundle_hash(self) -> str:
        payload = {
            "query_id": self.query_id,
            "tick": self.tick,
            "budget_tokens": self.budget_tokens,
            "items": [[it.id, it.tokens] for it in self.items],
        }
        return sha256_str(canonical_json(payload))

    def validate(self, *, ignores_budget: bool = False) -> None:
        if not ignores_budget and self.tokens > self.budget_tokens:
            raise BudgetExceeded(
                f"bundle for query {self.query_id} uses {self.tokens} tokens "
                f"> budget {self.budget_tokens}"
            )
