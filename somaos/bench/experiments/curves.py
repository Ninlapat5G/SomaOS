"""The three curves the design stands or falls on.

Run:  python -m somaos.bench.experiments.curves

M1  quality against store size -- does detail slide while gist holds, or
    do both fall off the same cliff
M2  quality against context size -- can it answer well on a small context
M3  cost against how much has been remembered -- and how much of that cost
    is the store rather than the effort ceiling

Dev seeds only. These are for finding the design, not for judging it: the
holdout measurement happens once, after the criteria are registered (N-15).
"""
from __future__ import annotations

import statistics as st
import sys

from somaos.bench.arena import sweep
from somaos.bench.lifeworld import WorldConfig, generate

SEEDS = ("dev-01", "dev-02", "dev-03")
POLICIES = ("B0", "B1", "B2", "B2c", "S")


def _table(rows, key, axis, axis_values, policies, fmt="{:>8.3f}"):
    out = [f"{'':>9} " + " ".join(f"{p:>8}" for p in policies)]
    for value in axis_values:
        line = f"{value:>9} "
        for policy in policies:
            picked = [r[key] for r in rows if r["policy"] == policy and r[axis] == value]
            line += (fmt.format(st.mean(picked)) if picked else f"{'n/a':>8}") + " "
        out.append(line.rstrip())
    return "\n".join(out)


def m1_capacity(write) -> None:
    budgets = (400_000, 150_000, 60_000, 24_000, 10_000)
    rows = sweep(ticks=200, seeds=SEEDS, store_budgets=budgets,
                 context_tokens=512, recall_ops=32, policies=POLICIES)
    write("=== M1  quality vs store size (context 512) ===")
    write("\n-- detail: which exact thing happened --")
    write(_table(rows, "detail", "store_budget_bytes", budgets, POLICIES))
    write("\n-- gist: what it was about --")
    write(_table(rows, "gist", "store_budget_bytes", budgets, POLICIES))


def m2_context(write) -> None:
    contexts = (64, 128, 256, 512, 1024)
    rows = []
    for context in contexts:
        rows += sweep(ticks=200, seeds=SEEDS, store_budgets=(150_000,),
                      context_tokens=context, recall_ops=32,
                      policies=("B0", "B2", "B2c", "S"))
    write("\n\n=== M2  quality vs context size (store 150KB) ===")
    write("\n-- detail --")
    write(_table(rows, "detail", "context_budget_tokens", contexts,
                 ("B0", "B2", "B2c", "S")))
    write("\n-- gist --")
    write(_table(rows, "gist", "context_budget_tokens", contexts,
                 ("B0", "B2", "B2c", "S")))


def m3_scale(write) -> None:
    write("\n\n=== M3  recall cost vs how much has been remembered ===")
    write(f"{'memories':>9} {'B0':>8} {'B2c':>8} {'S':>8} {'S/flat':>8}")
    for ticks in (100, 200, 400, 800):
        episodes = len(generate(WorldConfig(n_ticks=ticks, seed_root="dev-01")).episodes)
        rows = sweep(ticks=ticks, seeds=("dev-01",), store_budgets=(400_000,),
                     context_tokens=256, recall_ops=32, policies=("B0", "B2c", "S"))
        cost = {r["policy"]: r["comparisons_per_question"] for r in rows}
        ratio = cost["S"] / cost["B2c"] if cost["B2c"] else 0.0
        write(f"{episodes:>9} {cost['B0']:>8.0f} {cost['B2c']:>8.0f} "
              f"{cost['S']:>8.0f} {ratio:>8.2f}")

    write("\n-- and how much of that is the effort ceiling, not the store --")
    write(f"{'recall_ops':>11} {'comparisons':>12} {'detail':>8} {'gist':>7}")
    for ops in (4, 8, 16, 32, 64):
        row = sweep(ticks=400, seeds=("dev-01",), store_budgets=(400_000,),
                    context_tokens=256, recall_ops=ops, policies=("S",))[0]
        write(f"{ops:>11} {row['comparisons_per_question']:>12.0f} "
              f"{row['detail']:>8.3f} {row['gist']:>7.3f}")


def main() -> int:
    def write(line: str = "") -> None:
        print(line)

    m1_capacity(write)
    m2_context(write)
    m3_scale(write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
