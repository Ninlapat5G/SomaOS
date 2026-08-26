"""Page-fault-aware coverage resolution (D-14, amending D-13).

Background: D-13 let a bundle answer for any id listed in its items'
``source_item_ids`` at **zero token cost**. That turned out to be a
scoreboard loophole, not a modelling choice -- diagnostics on
uniform/dev-01 showed 92.6% of policy S's correct answers came from a
pointer to content it had already thrown away, with one 100-token item
carrying 108 covered ids (~0.93 tokens per id). A policy could discard
everything, keep the receipts, and still score 1.000.

D-14 closes it by taking target_SomaOS.md #4.1 literally: "page fault ->
retrieval miss". A pointer is a page that is not resident. Reaching it
means faulting the raw event back in, which costs that item's real token
size out of whatever budget the bundle did not already spend. Items
physically present in the bundle are resident and free -- their tokens
were paid when they were admitted.

**The resolver is deliberately blind to the answer key.** It never sees
``required_item_ids``; it takes only the bundle. A first cut of D-14 did
take the required set and faulted in exactly the pages the query needed
-- which handed every policy a clairvoyant prefetcher and left the
loophole wide open (the pointer-hoarding policy in tests/test_coverage.py
still scored 1.000 under it). Instead, faults are serviced in the order
the *policy* itself laid its pointers out: bundle item order, then each
item's ``source_item_ids`` order. That is the policy's declared
priority, and it is all the resolver honours. A policy that wants a page
back must say so by ranking it, exactly as it would have to in a real
system where nothing knows the answer in advance.

This module is pure and holds no state; metrics.py owns the accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from somaos.broker.types import MemoryItem


@dataclass(frozen=True, slots=True)
class Coverage:
    """What a bundle can answer for, and what the pointer part cost.

    Computed from the bundle alone -- see the module docstring on why
    this type never sees a query's required ids.
    """

    covered: frozenset[str]
    """resident | faulted -- the ids this bundle can answer for."""

    resident: frozenset[str]
    """Ids of items physically in the bundle. Already paid for."""

    faulted: frozenset[str]
    """Pointer targets loaded back in, in policy-declared order, until
    the residual budget ran out."""

    deferred: frozenset[str]
    """Pointer targets the residual budget could not reach (or whose raw
    size is unknown -- you cannot load a page whose size you do not
    know). Listed, but not available."""

    fault_tokens: int
    """Tokens spent on `faulted`. Real cost: metrics.py adds it to the
    query's bill (D-06 -- cost is what reaches the model, and a faulted
    page reaches the model)."""

    residual_tokens: int
    """Budget left after the bundle's own items, before faulting."""


def resolve_coverage(
    items: Sequence[MemoryItem],
    *,
    budget_tokens: int,
    raw_tokens: Mapping[str, int],
) -> Coverage:
    """Resolve what a bundle covers under D-14's page-fault rule.

    `raw_tokens` maps item id -> the token size of the item as it was
    originally observed. metrics.py builds it from the trace, so a
    policy cannot set the price of its own faults.
    """
    resident = {it.id for it in items}

    # Fault queue in policy-declared order: bundle order, then each
    # item's own source_item_ids order. Deduped, resident ids skipped.
    queue: list[str] = []
    seen: set[str] = set(resident)
    for it in items:
        for sid in it.source_item_ids:
            if sid in seen:
                continue
            seen.add(sid)
            queue.append(sid)

    bundle_tokens = sum(it.tokens for it in items)
    residual = max(0, budget_tokens - bundle_tokens)

    faulted: list[str] = []
    deferred: list[str] = []
    spent = 0
    exhausted = False
    for sid in queue:
        cost = raw_tokens.get(sid)
        if exhausted or cost is None or cost < 0 or spent + cost > residual:
            # Once the budget can no longer service the head of the
            # queue, everything behind it waits too -- a queue is a
            # queue. (Skipping ahead to cheaper pages would be the
            # clairvoyance this rule exists to remove.)
            if cost is not None and cost >= 0 and spent + cost > residual:
                exhausted = True
            deferred.append(sid)
            continue
        faulted.append(sid)
        spent += cost

    return Coverage(
        covered=frozenset(resident | set(faulted)),
        resident=frozenset(resident),
        faulted=frozenset(faulted),
        deferred=frozenset(deferred),
        fault_tokens=spent,
        residual_tokens=residual,
    )


def raw_token_map(trace) -> dict[str, int]:
    """Token size of every item the trace ever observed -- the ground
    truth a page fault is charged against. Built from the trace, never
    from policy state."""
    out: dict[str, int] = {}
    for ev in trace.events:
        if ev.kind == "observe":
            out[ev.observation.item.id] = ev.observation.item.tokens
    return out
