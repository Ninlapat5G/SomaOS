"""Content addressing and the alias table (N-01, N-07).

A node's address is a hash of what the node *is*, so two nodes holding the
same thing share one address and dedupe for free, and a parent's address
changes whenever any child changes, which makes the whole tree checkable
the way a Merkle tree is.

That creates the problem this module exists to solve. Diluting a node
changes its vector, which changes its address -- but N-01 says an address
that ever existed must resolve forever. So every re-encoding writes an
alias from the old address to the new one, and ``resolve`` walks that
chain. This is a page table entry pointing at a compressed frame: whoever
still holds the old address gets an answer, just a coarser one.

The table is append-only. Nothing is ever removed from it, including
during compaction, because removal is precisely the failure mode N-01
forbids.
"""
from __future__ import annotations

import hashlib

from somaos.broker.memory.vector import Grade, vector_digest
from somaos.util.hashing import canonical_json

ADDR_PREFIX = "addr:"


class AliasCycle(RuntimeError):
    """Raised if the alias chain loops -- a corrupt table, never a normal state."""


def content_address(
    *,
    vec,
    grade: Grade,
    level: int,
    region: str,
    children: tuple[str, ...] = (),
) -> str:
    """Address of a node with this content.

    Children are sorted so that the address depends on the *set* of
    children, not on the order a caller happened to pass them; sibling
    order is a presentation detail and must not fork the address space.
    """
    payload = canonical_json({
        "vec": vector_digest(vec, grade),
        "grade": int(grade),
        "level": int(level),
        "region": str(region),
        "children": sorted(children),
    })
    return ADDR_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AliasTable:
    """Append-only forwarding table: old address -> address that replaced it.

    ``resolve`` follows the chain to its end. Results are memoised, but the
    raw links stay in ``links`` so the history of what became what is
    auditable -- "log which memory faded into which" is a requirement
    (target_SomaOS.md section 5.6), not a debugging nicety.
    """

    __slots__ = ("_links", "_resolved")

    def __init__(self) -> None:
        self._links: dict[str, str] = {}
        self._resolved: dict[str, str] = {}

    @property
    def links(self) -> dict[str, str]:
        """Raw forwarding links, oldest to newest. Read-only view by copy."""
        return dict(self._links)

    def __len__(self) -> int:
        return len(self._links)

    def add(self, old: str, new: str) -> None:
        """Record that ``old`` has been replaced by ``new``.

        Re-pointing an address that already forwards somewhere else is
        rejected: it would rewrite history, and there is no legitimate
        caller for it. Re-adding the same link is fine (idempotent), and a
        self-link is a no-op rather than a cycle.
        """
        if old == new:
            return
        existing = self._links.get(old)
        if existing is not None and existing != new:
            raise ValueError(
                f"alias for {old} already points at {existing}; refusing to repoint to {new}"
            )
        self._links[old] = new
        # Any memoised answer that ended at `old` is now stale.
        if self._resolved:
            self._resolved = {k: v for k, v in self._resolved.items() if v != old}

    def resolve(self, addr: str) -> str:
        """Follow the chain from ``addr`` to the address that stands today.

        Never returns None: an address with no alias resolves to itself,
        which is what an address that has not been diluted should do.
        """
        cached = self._resolved.get(addr)
        if cached is not None:
            return cached
        seen: set[str] = set()
        cur = addr
        while True:
            nxt = self._links.get(cur)
            if nxt is None:
                break
            if cur in seen:
                raise AliasCycle(f"alias chain from {addr} loops at {cur}")
            seen.add(cur)
            cur = nxt
        for a in seen:
            self._resolved[a] = cur
        self._resolved[addr] = cur
        return cur

    def chain(self, addr: str) -> tuple[str, ...]:
        """The full path from ``addr`` to its current address, inclusive.

        This is the audit trail for a single memory's fading.
        """
        out = [addr]
        seen = {addr}
        cur = addr
        while (nxt := self._links.get(cur)) is not None:
            if nxt in seen:
                raise AliasCycle(f"alias chain from {addr} loops at {nxt}")
            out.append(nxt)
            seen.add(nxt)
            cur = nxt
        return tuple(out)
