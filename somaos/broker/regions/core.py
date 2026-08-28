"""Identity: the part of memory that is in context before anything is asked.

CORE is not a cache of frequently used memories. It is what the agent is,
and it is resident because behaviour depends on it continuously rather
than occasionally -- an agent that had to go and look up its own values
before acting would not have them.

Three sub-levels, ordered by how slowly they change (McAdams;
plans/04_HUMAN_MEMORY_BASIS.md section 6). The ordering is also the layout
order in the prompt, which is not a coincidence: the slowest-changing text
goes first so the prefix stays stable across ticks and stays cacheable.

Nothing here is ever diluted (N-06). The quota is a hard cap enforced at
admission instead: if identity does not fit, that is a configuration
error to raise, not something to solve by eroding the agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from somaos.broker.memory.node import CoreLevel, MemoryNode, Region
from somaos.broker.memory.tree import MemoryTree


class CoreQuotaExceeded(RuntimeError):
    """Admitting this would push identity past its reserved bytes."""


@dataclass(frozen=True, slots=True)
class CoreZone:
    """One layout zone of the resident block, in prompt order."""

    level: CoreLevel
    addrs: tuple[str, ...]
    tokens: int


@dataclass
class CoreSet:
    """The resident identity block and its budget.

    ``quota_bytes`` is carved out of ``store_budget_bytes`` before anything
    else competes for it, which is what makes CORE undilutable in practice
    rather than only in principle.
    """

    quota_bytes: int
    tokens_per_node: int = 32
    _levels: dict[CoreLevel, list[str]] = field(default_factory=dict)

    def admit(self, tree: MemoryTree, node: MemoryNode, level: CoreLevel) -> str:
        if node.region is not Region.CORE:
            raise ValueError(f"{node.region.name} nodes do not belong in CORE")
        projected = self.used_bytes(tree) + node.nbytes
        if projected > self.quota_bytes:
            raise CoreQuotaExceeded(
                f"identity would need {projected} bytes but the CORE quota is "
                f"{self.quota_bytes}; raise the quota rather than diluting who "
                f"the agent is (N-06)"
            )
        addr = tree.insert(node)
        self._levels.setdefault(level, []).append(addr)
        return addr

    def used_bytes(self, tree: MemoryTree) -> int:
        return sum(
            tree.get(addr).nbytes
            for addrs in self._levels.values()
            for addr in addrs
            if tree.get(addr) is not None
        )

    def zones(self, tree: MemoryTree) -> tuple[CoreZone, ...]:
        """Resident content in prompt order: slowest-changing first.

        Trait, then adaptation, then narrative. Emitting them in a stable
        order is what keeps the prompt prefix identical between ticks, so
        the model's prefix cache survives; shuffling identity every tick
        would pay for it twice, once in cache misses and once in an agent
        whose character reads differently each time.
        """
        out = []
        for level in sorted(CoreLevel):
            addrs = tuple(sorted(self._levels.get(level, ())))
            if not addrs:
                continue
            out.append(
                CoreZone(level=level, addrs=addrs, tokens=len(addrs) * self.tokens_per_node)
            )
        return tuple(out)

    def resident_tokens(self, tree: MemoryTree) -> int:
        """Context cost paid on every single tick, before any recall."""
        return sum(zone.tokens for zone in self.zones(tree))

    def addresses(self) -> tuple[str, ...]:
        return tuple(sorted(a for addrs in self._levels.values() for a in addrs))
