"""Policy protocol + registry. See plans/02_INTERFACES.md #2.

Every memory-management strategy (baselines B0-B4 and our own policy S)
implements this same interface, so they can be swapped by config and
benchmarked head to head without touching the runner.
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from somaos.broker.types import ContextBundle, EncodeDecision, Observation, QueryView


@runtime_checkable
class MemoryPolicy(Protocol):
    name: str
    ignores_budget: bool

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None: ...

    def observe(self, obs: Observation) -> EncodeDecision: ...

    def on_tick(self, tick: int) -> None: ...

    def on_query(self, q: QueryView) -> ContextBundle: ...

    def stats(self) -> dict[str, float]: ...


POLICY_REGISTRY: dict[str, type] = {}


def register_policy(name: str):
    def deco(cls):
        POLICY_REGISTRY[name] = cls
        return cls
    return deco


def build_policy(name: str, **kwargs) -> MemoryPolicy:
    if name not in POLICY_REGISTRY:
        raise KeyError(f"unknown policy {name!r}; known: {sorted(POLICY_REGISTRY)}")
    return POLICY_REGISTRY[name](**kwargs)
