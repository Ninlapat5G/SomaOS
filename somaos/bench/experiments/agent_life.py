"""Run a simulated life and watch the memory system behave.

Run:  python -m somaos.bench.experiments.agent_life

Unit tests check each module against its own contract. This checks the
thing they cannot: that the five of them compose into something that
behaves like memory over time. It runs an agent through a long stretch of
days under a store far too small to hold them all, and asks four questions
that only make sense at this scale:

    does a routine become a habit without being told to
    does the detail of old, unused days fade while the shape of them stays
    can the agent still recall a specific day it keeps returning to
    is every memory it ever formed still reachable

The world is deliberately mundane -- a repeated morning, a repeated
evening, and a stream of one-off events -- because that is the mix the
design claims to handle: the repeated parts should consolidate into who
the agent is, and the one-off parts should thin out into a gist without
disappearing.
"""
from __future__ import annotations

import json
import sys

from somaos.broker.consolidation import ConsolidationMachine
from somaos.broker.dilution import DilutionEngine
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import embed, similarity
from somaos.broker.recall import Move, RecallMachine
from somaos.broker.regions.trigger import Trigger, TriggerKind, TriggerRegistry

DAYS = 200
STORE_BUDGET = 96_000          # about a quarter of what these days need at full precision
CONSOLIDATE_EVERY = 20

MORNING = ("coffee", "inbox")
EVENING = ("walk", "river")


def _general(tree, topic, tick=0):
    return tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=embed((topic,)), keys=(topic,), span=(tick, tick),
        text_ref=f"the {topic}s",
    ), tick=tick)


def _episode(tree, parent, topic, detail, tick):
    keys = (topic, *detail) if isinstance(detail, tuple) else (topic, detail)
    return tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
        vec=embed(keys), keys=keys, span=(tick, tick),
        text_ref=f"day {tick}: {' '.join(keys)}",
    ), parent=parent, tick=tick)


def live(days: int = DAYS, store_budget: int = STORE_BUDGET) -> dict:
    tree = MemoryTree(beam=4)
    dilution = DilutionEngine(store_budget_bytes=store_budget)
    consolidation = ConsolidationMachine(dilution=dilution)
    triggers = TriggerRegistry()

    mornings = _general(tree, "morning")
    evenings = _general(tree, "evening")
    oneoffs = _general(tree, "oneoff")

    triggers.arm(Trigger(
        id="daily-review", kind=TriggerKind.TIME,
        due_tick=CONSOLIDATE_EVERY, every=CONSOLIDATE_EVERY, action="consolidate",
    ))

    landmark = None
    every_address: list[str] = []
    cycles = []

    for tick in range(days):
        every_address.append(_episode(tree, mornings, "morning", MORNING, tick))
        every_address.append(_episode(tree, evenings, "evening", EVENING, tick))
        every_address.append(_episode(tree, oneoffs, "oneoff", f"thing{tick}", tick))

        if tick == 7:
            # One day the agent keeps coming back to.
            landmark = _episode(tree, oneoffs, "oneoff", "the storm", tick)
            every_address.append(landmark)

        if landmark is not None and tick % 9 == 0:
            machine = RecallMachine(tree, ops_budget=12, context_budget_tokens=512)
            machine.begin(topics=("oneoff",), entities=("the storm",), tick=tick)
            while Move.DESCEND in machine.offer():
                machine.step(Move.DESCEND)
            if Move.MATERIALIZE in machine.offer():
                machine.step(Move.MATERIALIZE)
            machine.finish()

        # Two things start a cycle: the schedule, and pressure. A store
        # that only reclaims on a timer overshoots its budget for as long as
        # the timer has left to run, which is not a budget.
        due = [f.id for f in triggers.on_tick(tick)]
        pressured = tree.store_bytes() > store_budget
        if due or pressured:
            report = consolidation.run(tree, tick=tick, window=CONSOLIDATE_EVERY * 2)
            for trigger_id in due:
                triggers.complete(trigger_id, tick=tick)
            cycles.append({
                "tick": tick,
                "reason": "scheduled" if due else "pressure",
                "crystallised": [list(c.keys) for c in report.crystallised],
                "diluted": len(report.diluted),
                "bytes_after": report.bytes_after,
            })

    habits = sorted(
        (tree.get(a).text_ref, tree.get(a).n_merged)
        for a in tree.region_members(Region.SKILL)
    )

    landmark_node = tree.resolve(landmark)
    stale = tree.resolve(every_address[2])  # an early one-off, never revisited

    machine = RecallMachine(tree, ops_budget=12, context_budget_tokens=512)
    machine.begin(topics=("oneoff",), entities=("the storm",), tick=days)
    found = machine.run_fast_path().nodes

    return {
        "days": days,
        "over_budget_after_any_cycle": [
            c["tick"] for c in cycles if c["bytes_after"] > store_budget
        ],
        "cycles_by_reason": {
            reason: sum(1 for c in cycles if c["reason"] == reason)
            for reason in ("scheduled", "pressure")
        },
        "store_budget_bytes": store_budget,
        "store_bytes": tree.store_bytes(),
        "nodes": len(tree),
        "addresses_issued": len(every_address),
        "all_addresses_resolve": all(tree.resolve(a) is not None for a in every_address),
        "grades": {g: c for g, c in tree.grade_histogram().items() if c},
        "habits_formed": habits,
        "consolidation_cycles": len(cycles),
        "landmark": {
            "grade": landmark_node.node.grade.name,
            "fidelity_bound": round(landmark_node.fidelity, 3),
            "recollections": tree.stat(landmark).use_count,
            "retrieval_strength": round(tree.retrieval_strength(landmark, tick=days), 3),
            "still_found_by_a_cold_walk": any(
                "the storm" in n.keys for n in found
            ),
        },
        "never_revisited_early_day": {
            "grade": stale.node.grade.name,
            "retrieval_strength": round(
                tree.retrieval_strength(every_address[2], tick=days), 4
            ),
            "still_resolves": stale.node is not None,
            "still_about_its_topic": round(
                similarity(embed(("oneoff",)), stale.node.vec), 3
            ),
        },
    }


def main() -> int:
    json.dump(live(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
