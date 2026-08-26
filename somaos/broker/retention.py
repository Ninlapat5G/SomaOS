"""Retention scoring engine (pure). See plans/02_INTERFACES.md #3 and
plans/wp/WP-03-retention.md.

Every function here must be a pure function: no I/O, no global state, no
randomness, no mutation of its arguments. This is what lets us test it
exhaustively and reuse it identically across every policy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from somaos.broker.types import ItemStat, MemoryItem


@dataclass(frozen=True, slots=True)
class RetentionWeights:
    w_recency: float
    w_frequency: float
    w_relevance: float
    w_surprise: float
    w_novelty: float
    w_pinned: float
    w_recompute: float

    @classmethod
    def from_json(cls, obj: dict) -> "RetentionWeights":
        return cls(
            w_recency=float(obj["w_recency"]),
            w_frequency=float(obj["w_frequency"]),
            w_relevance=float(obj["w_relevance"]),
            w_surprise=float(obj["w_surprise"]),
            w_novelty=float(obj["w_novelty"]),
            w_pinned=float(obj["w_pinned"]),
            w_recompute=float(obj["w_recompute"]),
        )

    def total(self) -> float:
        return (
            self.w_recency + self.w_frequency + self.w_relevance
            + self.w_surprise + self.w_novelty + self.w_pinned + self.w_recompute
        )

    def normalized(self) -> "RetentionWeights":
        total = (
            self.w_recency + self.w_frequency + self.w_relevance
            + self.w_surprise + self.w_novelty + self.w_pinned + self.w_recompute
        )
        if total <= 0:
            raise ValueError("sum of weights must be > 0")
        return RetentionWeights(
            w_recency=self.w_recency / total,
            w_frequency=self.w_frequency / total,
            w_relevance=self.w_relevance / total,
            w_surprise=self.w_surprise / total,
            w_novelty=self.w_novelty / total,
            w_pinned=self.w_pinned / total,
            w_recompute=self.w_recompute / total,
        )


@dataclass(frozen=True, slots=True)
class RetentionFeatures:
    recency: float
    frequency: float
    relevance: float
    surprise: float
    novelty: float
    pinned: float
    recompute: float


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def extract_features(
    item: MemoryItem,
    stat: ItemStat,
    *,
    now_tick: int,
    tau_ticks: int,
    goal_topics: frozenset[str],
    goal_entities: frozenset[str],
    max_access_count: int,
) -> RetentionFeatures:
    if tau_ticks <= 0:
        raise ValueError("tau_ticks must be > 0")
    age = now_tick - stat.last_access_tick
    if age < 0:
        raise ValueError(
            f"item {item.id}: last_access_tick ({stat.last_access_tick}) "
            f"is in the future relative to now_tick ({now_tick})"
        )
    recency = math.exp(-age / tau_ticks)

    if max_access_count <= 0:
        frequency = 0.0
    else:
        frequency = math.log1p(stat.access_count) / math.log1p(max_access_count)

    if not goal_topics and not goal_entities:
        relevance = 0.0
    else:
        topic_sim = _jaccard(frozenset(item.topics), goal_topics)
        entity_sim = _jaccard(frozenset(item.entities), goal_entities)
        relevance = 0.6 * topic_sim + 0.4 * entity_sim

    return RetentionFeatures(
        recency=recency,
        frequency=frequency,
        relevance=relevance,
        surprise=item.surprise,
        novelty=item.novelty,
        pinned=1.0 if item.pinned else 0.0,
        recompute=item.recompute_cost,
    )


def retention_score(f: RetentionFeatures, w: RetentionWeights) -> float:
    total_w = (
        w.w_recency + w.w_frequency + w.w_relevance
        + w.w_surprise + w.w_novelty + w.w_pinned + w.w_recompute
    )
    if total_w <= 0:
        raise ValueError("sum of weights must be > 0")

    raw = (
        w.w_recency * f.recency
        + w.w_frequency * f.frequency
        + w.w_relevance * f.relevance
        + w.w_surprise * f.surprise
        + w.w_novelty * f.novelty
        + w.w_pinned * f.pinned
        + w.w_recompute * f.recompute
    )
    score = raw / total_w
    # Clamp for float error only; features/weights are already validated to [0,1]/[0,inf).
    return min(1.0, max(0.0, score))


def _overlap(values: tuple[str, ...], goal: frozenset[str]) -> tuple[int, int]:
    """(|a & b|, |a | b|) without building a set for `values`.

    Items carry one or two tags in every Phase 0 regime, so
    frozenset(values) was pure overhead in the hot loop (WP-14 profile).
    The counts are identical to the set version, so every float derived
    from them is bit-identical -- verified by
    test_score_item_matches_the_reference_path_exactly.
    """
    n_goal = len(goal)
    if not values:
        return 0, n_goal
    if len(values) == 1:
        v = values[0]
        if v in goal:
            return 1, n_goal
        return 0, n_goal + 1
    seen = set(values)
    inter = len(seen & goal)
    return inter, len(seen) + n_goal - inter


def score_item(
    item: MemoryItem,
    stat: ItemStat,
    *,
    now_tick: int,
    tau_ticks: int,
    goal_topics: frozenset[str],
    goal_entities: frozenset[str],
    max_access_count: int,
    weights: RetentionWeights,
    total_weight: float | None = None,
) -> float:
    """extract_features + retention_score fused into one pass.

    Purely a performance shape: same arithmetic, same order, same result
    to the last bit. allocate() calls this 10,000 times per reallocation
    and the intermediate RetentionFeatures object, the two frozensets and
    the re-summed weight total were most of its cost (WP-14).

    extract_features/retention_score remain the normative definition;
    this is checked against them, never the other way round.
    """
    if tau_ticks <= 0:
        raise ValueError("tau_ticks must be > 0")
    age = now_tick - stat.last_access_tick
    if age < 0:
        raise ValueError(
            f"item {item.id}: last_access_tick ({stat.last_access_tick}) "
            f"is in the future relative to now_tick ({now_tick})"
        )

    total_w = weights.total() if total_weight is None else total_weight
    if total_w <= 0:
        raise ValueError("sum of weights must be > 0")

    recency = math.exp(-age / tau_ticks)

    if max_access_count <= 0:
        frequency = 0.0
    else:
        frequency = math.log1p(stat.access_count) / math.log1p(max_access_count)

    if not goal_topics and not goal_entities:
        relevance = 0.0
    else:
        t_inter, t_union = _overlap(item.topics, goal_topics)
        e_inter, e_union = _overlap(item.entities, goal_entities)
        topic_sim = (t_inter / t_union) if t_union else 0.0
        entity_sim = (e_inter / e_union) if e_union else 0.0
        relevance = 0.6 * topic_sim + 0.4 * entity_sim

    raw = (
        weights.w_recency * recency
        + weights.w_frequency * frequency
        + weights.w_relevance * relevance
        + weights.w_surprise * item.surprise
        + weights.w_novelty * item.novelty
        + weights.w_pinned * (1.0 if item.pinned else 0.0)
        + weights.w_recompute * item.recompute_cost
    )
    return min(1.0, max(0.0, raw / total_w))
