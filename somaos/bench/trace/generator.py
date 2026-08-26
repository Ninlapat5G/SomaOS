"""Synthetic long-horizon trace generator. See
plans/wp/WP-02-trace-generator.md.

Must never import from somaos.broker.policies -- enforced by
tests/test_layering.py. Surprise/novelty are computed purely from the
World's own frequency predictor (somaos.bench.trace.world), independent
of any policy, so every policy is compared on the same ground truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from somaos.bench.trace.world import World, WorldConfig
from somaos.broker.types import MemoryItem, Observation, Query, Trace, TraceEvent
from somaos.util.hashing import canonical_json, sha256_str
from somaos.util.rng import make_rng

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    regime: str
    seed_root: str
    n_ticks: int = 5000
    n_topics: int = 24
    n_entities: int = 60
    obs_per_tick_lambda: float = 3.0
    p_query: float = 0.08
    token_mean: int = 120
    token_mode: Literal["fixed", "lognormal"] = "fixed"
    token_lognormal_sigma: float = 0.5
    fact_revision_rate: float = 0.3
    hot_set_size: int = 6
    hot_set_prob: float = 0.0
    query_bias: Literal["recent", "stale", "uniform"] = "uniform"
    query_bias_fraction: float = 0.3
    burst_period: int = 0
    burst_on_fraction: float = 0.5
    burst_high_lambda: float = 8.0
    burst_low_lambda: float = 0.2
    drift_period: int = 0
    drift_active_topics: int = 4
    adversarial: bool = False
    revision_requires_surprise: bool = False
    """WP-13 diagnostic switch, OFF by default so every pre-registered
    regime is byte-identical to before. When on, a fact's belief is only
    revised by an observation the predictor did not expect -- see the
    `surprise_driven` regime and plans/CHANGELOG_REGIMES.md."""
    revision_surprise_floor: float = 0.5

    def as_dict(self) -> dict:
        return {
            "regime": self.regime, "seed_root": self.seed_root, "n_ticks": self.n_ticks,
            "n_topics": self.n_topics, "n_entities": self.n_entities,
            "obs_per_tick_lambda": self.obs_per_tick_lambda, "p_query": self.p_query,
            "token_mean": self.token_mean, "token_mode": self.token_mode,
            "token_lognormal_sigma": self.token_lognormal_sigma,
            "fact_revision_rate": self.fact_revision_rate,
            "hot_set_size": self.hot_set_size, "hot_set_prob": self.hot_set_prob,
            "query_bias": self.query_bias, "query_bias_fraction": self.query_bias_fraction,
            "burst_period": self.burst_period, "burst_on_fraction": self.burst_on_fraction,
            "burst_high_lambda": self.burst_high_lambda, "burst_low_lambda": self.burst_low_lambda,
            "drift_period": self.drift_period, "drift_active_topics": self.drift_active_topics,
            "adversarial": self.adversarial,
            "revision_requires_surprise": self.revision_requires_surprise,
            "revision_surprise_floor": self.revision_surprise_floor,
        }


def load_regime_overrides(path: Path | None = None) -> dict:
    p = path or (_CONFIG_DIR / "regimes.json")
    import json
    return json.loads(p.read_text())


def from_regime(regime: str, seed_root: str, *, n_ticks: int = 5000,
                 overrides_path: Path | None = None) -> GeneratorConfig:
    overrides = load_regime_overrides(overrides_path)
    if regime not in overrides:
        raise KeyError(f"unknown regime {regime!r}; known: {sorted(overrides)}")
    base = GeneratorConfig(regime=regime, seed_root=seed_root, n_ticks=n_ticks)
    return replace(base, **overrides[regime])


def _poisson(rng, lam: float) -> int:
    """Knuth's algorithm. Deterministic given a seeded random.Random."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _current_lambda(cfg: GeneratorConfig, t: int) -> float:
    if cfg.burst_period <= 0:
        return cfg.obs_per_tick_lambda
    phase = t % cfg.burst_period
    on_len = cfg.burst_period * cfg.burst_on_fraction
    return cfg.burst_high_lambda if phase < on_len else cfg.burst_low_lambda


def _active_topics(cfg: GeneratorConfig, world: World, t: int) -> frozenset[str] | None:
    if cfg.drift_period <= 0:
        return None
    n_windows = max(1, cfg.n_topics // max(1, cfg.drift_active_topics))
    window_idx = (t // cfg.drift_period) % n_windows
    start = window_idx * cfg.drift_active_topics
    window = world.topics[start:start + cfg.drift_active_topics]
    return frozenset(window) if window else None


def _sample_tokens(cfg: GeneratorConfig, rng) -> int:
    if cfg.token_mode == "fixed":
        return cfg.token_mean
    val = rng.lognormvariate(math.log(cfg.token_mean), cfg.token_lognormal_sigma)
    return max(1, int(round(val)))


def _pick_fact_key(cfg: GeneratorConfig, world: World, t: int, rng):
    keys = list(world.all_fact_keys())
    active = _active_topics(cfg, world, t)
    if active is not None:
        filtered = [k for k in keys if k[1] in active]
        if filtered:
            keys = filtered
    if not keys:
        return None
    if cfg.query_bias == "uniform" or cfg.query_bias_fraction <= 0:
        return rng.choice(keys)
    aged = sorted(((t - world.facts[k].last_item_tick, k) for k in keys), key=lambda x: x[0])
    n = len(aged)
    pool_size = max(1, int(n * cfg.query_bias_fraction))
    pool = aged[:pool_size] if cfg.query_bias == "recent" else aged[-pool_size:]
    _, chosen = rng.choice(pool)
    return chosen


def generate(cfg: GeneratorConfig) -> Trace:
    world = World(WorldConfig(n_topics=cfg.n_topics, n_entities=cfg.n_entities, seed_root=cfg.seed_root))

    rng_count = make_rng(cfg.seed_root, "obs_count")
    rng_pick = make_rng(cfg.seed_root, "obs_pick")
    rng_tokens = make_rng(cfg.seed_root, "tokens")
    rng_query_gate = make_rng(cfg.seed_root, "query_gate")
    rng_query_pick = make_rng(cfg.seed_root, "query_pick")
    rng_revise = make_rng(cfg.seed_root, "revise")
    rng_adv = make_rng(cfg.seed_root, "adversarial")
    rng_hotset = make_rng(cfg.seed_root, "hotset_init")

    hot_set: list[tuple[str, str]] = []
    if cfg.hot_set_prob > 0 and cfg.hot_set_size > 0:
        for _ in range(cfg.hot_set_size):
            hot_set.append(world.sample_entity_topic(rng_hotset))

    events: list[TraceEvent] = []
    item_counter = 0
    query_counter = 0

    for t in range(cfg.n_ticks):
        # --- query first: only sees facts established strictly before tick t ---
        if rng_query_gate.random() < cfg.p_query:
            key = _pick_fact_key(cfg, world, t, rng_query_pick)
            if key is not None:
                fact = world.current_fact(*key)
                entity, topic = key
                q = Query(
                    id=f"q_{t}_{query_counter}", tick=t,
                    topics=(topic,), entities=(entity,),
                    required_item_ids=frozenset({fact.last_item_id}),
                )
                events.append(TraceEvent(tick=t, kind="query", query=q))
                query_counter += 1

        # --- then observations for this tick ---
        lam = _current_lambda(cfg, t)
        n_obs = _poisson(rng_count, lam)
        for _ in range(n_obs):
            if hot_set and rng_pick.random() < cfg.hot_set_prob:
                entity, topic = rng_pick.choice(hot_set)
            else:
                entity, topic = world.sample_entity_topic(rng_pick)

            surprise, novelty = world.observe_pair(entity, topic)
            if cfg.adversarial:
                surprise = rng_adv.random()

            tokens = _sample_tokens(cfg, rng_tokens)
            item_id = f"item_{item_counter}"
            item_counter += 1

            item = MemoryItem(
                id=item_id, kind="episodic", tokens=tokens, created_tick=t,
                topics=(topic,), entities=(entity,), surprise=surprise, novelty=novelty,
            )
            events.append(TraceEvent(tick=t, kind="observe", observation=Observation(tick=t, item=item)))

            key = (entity, topic)
            is_first_sight = key not in world.facts
            if is_first_sight:
                revise = True
            elif cfg.revision_requires_surprise:
                # WP-13. In the default regimes, whether an observation
                # becomes a fact's current evidence is an independent
                # coin flip -- so a redundant confirmation with surprise
                # 0.02 can become the answer key while nothing about the
                # world actually changed. Under D-04's own definition
                # (surprise = 1 - confidence(predictor)), a belief that
                # changes is by construction one the predictor got
                # wrong. This branch makes the generator say that.
                draw = rng_revise.random()
                revise = surprise >= cfg.revision_surprise_floor and draw < cfg.fact_revision_rate
            else:
                revise = rng_revise.random() < cfg.fact_revision_rate
            if revise:
                world.upsert_fact(entity, topic, value=item_counter, item_id=item_id, tick=t)

    trace_id = sha256_str(canonical_json(cfg.as_dict()))
    meta = {
        **cfg.as_dict(),
        "n_items": item_counter,
        "n_queries": query_counter,
    }
    return Trace(trace_id=trace_id, events=tuple(events), n_ticks=cfg.n_ticks, meta=meta)


def ground_truth_utility(trace: Trace) -> dict[str, float]:
    """item id -> number of future queries for which it is required evidence."""
    counts: dict[str, float] = {}
    for ev in trace.events:
        if ev.kind == "observe":
            counts.setdefault(ev.observation.item.id, 0.0)
    for ev in trace.events:
        if ev.kind == "query":
            for iid in ev.query.required_item_ids:
                counts[iid] = counts.get(iid, 0.0) + 1.0
    return counts
