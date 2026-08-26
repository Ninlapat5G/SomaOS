"""Ground-truth world model for synthetic traces. See
plans/wp/WP-02-trace-generator.md #1.

This module must never import from somaos.broker.policies -- surprise and
novelty are computed by the world, independently of any policy, so every
policy sees the same signal (fairness of comparison). That import
boundary is enforced by tests/test_layering.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from somaos.util.rng import make_rng


@dataclass(frozen=True, slots=True)
class WorldConfig:
    n_topics: int = 24
    n_entities: int = 60
    seed_root: str = "dev-01"


@dataclass
class Fact:
    entity: str
    topic: str
    value: int
    last_item_id: str | None = None
    last_item_tick: int = 0
    revision_count: int = 0


class World:
    """Tracks entity/topic affinities and the evolving set of 'facts' that
    generate query ground truth. Surprise/novelty are derived from a
    simple frequency predictor per (entity, topic) pair, not from any
    policy's internal state.
    """

    def __init__(self, cfg: WorldConfig) -> None:
        self.cfg = cfg
        rng = make_rng(cfg.seed_root, "world_init")
        self.topics = tuple(f"topic{i}" for i in range(cfg.n_topics))
        self.entities = tuple(f"entity{i}" for i in range(cfg.n_entities))
        # Each entity has an affinity subset of topics it "naturally" generates.
        self._entity_topics: dict[str, tuple[str, ...]] = {}
        for e in self.entities:
            k = rng.randint(1, min(4, cfg.n_topics))
            self._entity_topics[e] = tuple(rng.sample(self.topics, k))

        # (entity, topic) observation counters -> predictor confidence.
        self._pair_seen: dict[tuple[str, str], int] = {}
        self.facts: dict[tuple[str, str], Fact] = {}

    def entity_topics(self, entity: str) -> tuple[str, ...]:
        return self._entity_topics[entity]

    def sample_entity_topic(self, rng) -> tuple[str, str]:
        entity = rng.choice(self.entities)
        candidates = self._entity_topics[entity]
        topic = rng.choice(candidates)
        return entity, topic

    def confidence(self, entity: str, topic: str) -> float:
        """Predictor confidence for this (entity, topic) pair, in [0, ~0.95],
        saturating with repeated observation -- mirrors retention.py's
        frequency saturation but is intentionally a separate, independent
        implementation (world must not depend on broker internals)."""
        n = self._pair_seen.get((entity, topic), 0)
        # 1 - 1/(1+n) saturates towards 1 without ever reaching it.
        return min(0.95, n / (n + 2.0))

    def observe_pair(self, entity: str, topic: str) -> tuple[float, float]:
        """Record an observation of (entity, topic); returns (surprise, novelty)
        computed from state *before* this observation, then updates state."""
        key = (entity, topic)
        seen_before = key in self._pair_seen
        if not seen_before:
            novelty = 1.0
            surprise = 1.0
        else:
            novelty = 0.0
            surprise = 1.0 - self.confidence(entity, topic)
        self._pair_seen[key] = self._pair_seen.get(key, 0) + 1
        return surprise, novelty

    def upsert_fact(self, entity: str, topic: str, value: int, item_id: str, tick: int) -> Fact:
        key = (entity, topic)
        existing = self.facts.get(key)
        if existing is None:
            fact = Fact(entity=entity, topic=topic, value=value, last_item_id=item_id,
                        last_item_tick=tick, revision_count=0)
        else:
            fact = Fact(entity=entity, topic=topic, value=value, last_item_id=item_id,
                        last_item_tick=tick, revision_count=existing.revision_count + 1)
        self.facts[key] = fact
        return fact

    def current_fact(self, entity: str, topic: str) -> Fact | None:
        return self.facts.get((entity, topic))

    def all_fact_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(self.facts.keys())
