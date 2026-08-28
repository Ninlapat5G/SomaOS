"""Run every policy through the same life, under the same budgets.

Run:  python -m somaos.bench.arena --ticks 300 --seeds dev-01,dev-02

The comparison this project has not had until now. Every policy sees the
same episodes in the same order, is asked the same questions at the same
ticks, lives under the same three budgets, and is charged for the vector
comparisons it makes. Nothing is priced by the thing being measured: token
costs come from the world, and marking is done by the bench against ground
truth it generated itself.

Results go out as JSONL, one row per (policy, budget, seed). What the rows
are for is the shape of the curves, not any single number: quality against
store size with detail and gist scored apart, and comparisons against store
size. A run where S loses is a result, and it gets reported as one.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from somaos.bench.lifeworld import LifeTrace, WorldConfig, generate
from somaos.bench.score import Marker, RunScore
from somaos.broker.memory.node import MemoryNode
from somaos.broker.policies.life import REGISTRY, Budgets

DEFAULT_STORE_BUDGETS = (400_000, 150_000, 60_000, 24_000, 10_000)
DEFAULT_CONTEXT_TOKENS = 512
DEFAULT_RECALL_OPS = 32


def _token_pricer(trace: LifeTrace):
    """Price a node from the world's own numbers, never the policy's.

    A node that stands for several memories, or for none in particular --
    a chapter, a habit -- is priced at the default. Only a node that still
    is one specific episode gets that episode's cost.
    """
    by_id = trace.tokens_by_id

    def tokens_of(node: MemoryNode) -> int:
        for key in node.keys:
            if key in by_id:
                return by_id[key]
        return 16

    return tokens_of


def run_one(
    trace: LifeTrace,
    policy_name: str,
    budgets: Budgets,
    *,
    seed_root: str,
) -> RunScore:
    policy = REGISTRY[policy_name]()
    policy.reset(budgets=budgets, tokens_of=_token_pricer(trace), seed_root=seed_root)
    marker = Marker(trace)

    episodes_by_tick: dict[int, list] = {}
    for episode in trace.episodes:
        episodes_by_tick.setdefault(episode.tick, []).append(episode)
    questions_by_tick: dict[int, list] = {}
    for question in trace.questions:
        questions_by_tick.setdefault(question.tick, []).append(question)
    intentions_by_tick: dict[int, list] = {}
    for intention in trace.intentions:
        intentions_by_tick.setdefault(intention.armed_tick, []).append(intention)

    score = RunScore(
        policy=policy_name, seed_root=seed_root,
        store_budget_bytes=budgets.store_bytes,
        context_budget_tokens=budgets.context_tokens,
        recall_ops_budget=budgets.recall_ops,
        results=[],
    )
    fired_at: dict[str, int] = {}

    for tick in range(trace.config.n_ticks):
        for intention in intentions_by_tick.get(tick, ()):
            policy.intend(intention)

        cues = tuple(e.topic for e in episodes_by_tick.get(tick, ()))
        for episode in episodes_by_tick.get(tick, ()):
            policy.perceive(episode)
        policy.on_tick(tick)

        for trigger_id in policy.fire(tick, cues):
            fired_at.setdefault(trigger_id, tick)

        for question in questions_by_tick.get(tick, ()):
            if question.kind == "trigger":
                continue
            outcome = policy.recall(question)
            score.results.append(marker.mark(
                question, outcome.nodes, tokens=outcome.tokens,
                comparisons=outcome.comparisons, ops=outcome.ops,
            ))

    for intention in trace.intentions:
        score.trigger_expected += 1
        when = fired_at.get(intention.id)
        if when is None:
            continue
        if intention.kind == "time" and when == intention.due_tick:
            score.trigger_fired += 1
        elif intention.kind == "event":
            score.trigger_fired += 1
        else:
            score.trigger_spurious += 1

    stats = policy.stats()
    score.nodes_final = stats.get("nodes", 0)
    score.store_bytes_final = (
        policy.store_bytes() if hasattr(policy, "store_bytes") else 0
    )
    return score


def sweep(
    *,
    ticks: int,
    seeds: tuple[str, ...],
    store_budgets: tuple[int, ...],
    context_tokens: int,
    recall_ops: int,
    policies: tuple[str, ...],
) -> list[dict]:
    rows = []
    for seed in seeds:
        trace = generate(WorldConfig(n_ticks=ticks, seed_root=seed))
        for store in store_budgets:
            budgets = Budgets(
                store_bytes=store,
                context_tokens=context_tokens,
                recall_ops=recall_ops,
            )
            for name in policies:
                score = run_one(trace, name, budgets, seed_root=seed)
                row = score.to_jsonable()
                row["world"] = trace.summary()
                rows.append(row)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--seeds", default="dev-01")
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS)
    parser.add_argument("--recall-ops", type=int, default=DEFAULT_RECALL_OPS)
    parser.add_argument("--policies", default="B0,B1,B2,B2c,S")
    parser.add_argument(
        "--store-budgets",
        default=",".join(str(b) for b in DEFAULT_STORE_BUDGETS),
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    rows = sweep(
        ticks=args.ticks,
        seeds=tuple(s.strip() for s in args.seeds.split(",") if s.strip()),
        store_budgets=tuple(int(b) for b in args.store_budgets.split(",")),
        context_tokens=args.context_tokens,
        recall_ops=args.recall_ops,
        policies=tuple(p.strip() for p in args.policies.split(",") if p.strip()),
    )

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        print(f"wrote {len(rows)} rows to {path}", file=sys.stderr)
    else:
        for row in rows:
            print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
