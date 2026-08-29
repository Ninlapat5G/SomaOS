"""Identity: the part of memory that is in context before anything is asked.

CORE is not a cache of frequently used memories. It is what the agent is,
and it is resident because behaviour depends on it continuously rather
than occasionally -- an agent that had to go and look up its own values
before acting would not have them.

Three sub-levels, ordered by how slowly they change (McAdams;
plans/04_HUMAN_MEMORY_BASIS.md section 6). The ordering is also the layout
order in the prompt, which is not a coincidence: the slowest-changing text
goes first so the prefix stays stable across ticks and stays cacheable.

Identity arrives two ways, and both are supported because both happen to
people. Some of it is given -- an agent is created with a persona, the way
a character starts with a temperament -- and ``seed`` puts that in place
before the agent has lived through anything. The rest is earned:
consolidation promotes a pattern the agent has actually repeated over a
long stretch, through ``emerge``. Which of the two a given trait came from
is recorded, because it matters: a seeded trait is a premise and stays put,
while an emerged one is a claim the evidence has to keep supporting.

Nothing here is ever diluted (N-06). The quota is a hard cap enforced at
admission instead: if identity does not fit, that is a configuration
error to raise, not something to solve by eroding the agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from somaos.broker.memory.node import CoreLevel, MemoryNode, Region
from somaos.broker.memory.tree import MemoryTree


class CoreQuotaExceeded(RuntimeError):
    """Admitting this would push identity past its reserved bytes."""


class Origin(Enum):
    SEEDED = "seeded"    # given at creation -- a premise
    EMERGED = "emerged"  # earned by repetition -- a claim


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
    _origin: dict[str, Origin] = field(default_factory=dict)

    def seed(self, tree: MemoryTree, node: MemoryNode, level: CoreLevel) -> str:
        """Give the agent a trait it did not have to earn.

        The persona an agent is created with. Seeded traits are premises:
        they are not subject to demotion, because there is no evidence for
        them to lose.
        """
        return self._admit(tree, node, level, Origin.SEEDED)

    def emerge(self, tree: MemoryTree, node: MemoryNode, level: CoreLevel) -> str:
        """Promote something the agent has actually repeated into identity.

        Called by consolidation, never by hand. An emerged trait is a claim
        about a pattern, so unlike a seeded one it can be demoted if the
        pattern stops holding.
        """
        return self._admit(tree, node, level, Origin.EMERGED)

    #: Kept as the old name so existing callers still work; new code should
    #: say which kind of identity it is adding.
    admit = seed

    def _admit(
        self, tree: MemoryTree, node: MemoryNode, level: CoreLevel, origin: Origin
    ) -> str:
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
        if addr not in self._origin:
            self._levels.setdefault(level, []).append(addr)
            self._origin[addr] = origin
        return addr

    def snapshot(self) -> dict:
        """Which addresses are identity, at what level, and how they got there.

        The nodes themselves live in the tree and are saved with it; this
        is only the membership. Origin is kept because it decides what may
        later be demoted: a seeded trait is a premise, an emerged one is a
        claim about a pattern, and reloading must not turn one into the
        other.
        """
        return {
            "quota_bytes": self.quota_bytes,
            "tokens_per_node": self.tokens_per_node,
            "members": [
                {"addr": addr, "level": int(level), "origin": self._origin[addr].value}
                for level, addrs in self._levels.items()
                for addr in addrs
                if addr in self._origin
            ],
        }

    @classmethod
    def restore(cls, snap: dict) -> CoreSet:
        core = cls(quota_bytes=int(snap["quota_bytes"]),
                   tokens_per_node=int(snap.get("tokens_per_node", 32)))
        for row in snap.get("members", ()):
            level = CoreLevel(int(row["level"]))
            core._levels.setdefault(level, []).append(row["addr"])
            core._origin[row["addr"]] = Origin(row["origin"])
        return core

    def origin_of(self, addr: str) -> Origin | None:
        return self._origin.get(addr)

    def emerged(self) -> tuple[str, ...]:
        return tuple(sorted(a for a, o in self._origin.items() if o is Origin.EMERGED))

    def seeded(self) -> tuple[str, ...]:
        return tuple(sorted(a for a, o in self._origin.items() if o is Origin.SEEDED))

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
