"""Does the memory design fit on a microcontroller?

Run:  python -m somaos.bench.experiments.embedded

A phone or a PC never fills up: one memory at full precision is about a
kilobyte, so an ordinary disk holds more life than an agent will ever
have. A microcontroller is the opposite case, and it is the case the
dilution ladder was built for. Flash is measured in megabytes, RAM in
kilobytes, and both are shared with the firmware.

Three questions, because a store that fits but cannot answer is no use,
and one that answers but cannot be walked in RAM is no use either:

E1  capacity -- how much life fits in each chip's flash, per rung
E2  quality  -- what the store still answers at microcontroller budgets
E3  working set -- how many addresses a single recall holds at once,
                   which is what has to fit in SRAM

Dev seeds only. This finds the design; it does not judge it (N-15).
"""
from __future__ import annotations

import json
import statistics as st
import sys

import numpy as np

from somaos.bench.arena import sweep
from somaos.bench.lifeworld import WorldConfig, generate
from somaos.broker.memory.vector import DEFAULT_DIM, Grade, nbytes
from somaos.broker.policies.life import Budgets, STree

SEEDS = ("dev-01", "dev-02", "dev-03")

#: Real parts, with the share of flash a memory store could plausibly get
#: once firmware, the model and the filesystem have taken theirs. The
#: split is a judgement call, not a measurement -- it is stated here so it
#: can be argued with rather than buried in a conclusion.
CHIPS = (
    # name,               flash,       sram,      store share
    ("STM32F401",          512 * 1024,  96 * 1024, 256 * 1024),
    ("nRF52840",          1024 * 1024, 256 * 1024, 512 * 1024),
    ("RP2040 / Pico",     2048 * 1024, 264 * 1024, 1536 * 1024),
    ("ESP32-S3",          8192 * 1024, 512 * 1024, 6144 * 1024),
    ("ESP32-S3 N16R8",   16384 * 1024, 512 * 1024, 12288 * 1024),
)

#: Store budgets to measure quality at, spanning the microcontroller range.
MCU_BUDGETS = (64 * 1024, 128 * 1024, 256 * 1024, 512 * 1024, 1536 * 1024)

#: Addresses are hex digests plus a float score in the frontier. Counted
#: as stored bytes rather than as Python objects, for the same reason the
#: store budget counts sign bits at one bit per dimension: the number has
#: to describe what a real implementation would hold.
BYTES_PER_WORKING_ADDRESS = 16 + 4


def _grade_bytes() -> dict[str, int]:
    probe = np.zeros(DEFAULT_DIM, dtype=np.float32)
    return {g.name: nbytes(probe, g) for g in Grade}


def e1_capacity() -> dict:
    """How many memories fit, per chip, per rung of the ladder."""
    per_grade = _grade_bytes()
    vector_rungs = {k: v for k, v in per_grade.items() if v > 0}

    rows = []
    for name, flash, sram, store in CHIPS:
        fits = {
            grade: store // size for grade, size in vector_rungs.items()
        }
        rows.append({
            "chip": name,
            "flash_bytes": flash,
            "sram_bytes": sram,
            "store_bytes": store,
            "memories_that_fit": fits,
        })
    return {"bytes_per_memory": per_grade, "chips": rows}


def e2_quality() -> dict:
    """What the store still answers when the budget is a chip, not a disk."""
    rows = sweep(ticks=400, seeds=SEEDS, store_budgets=MCU_BUDGETS,
                 context_tokens=256, recall_ops=32, policies=("S", "B2c"))

    out = []
    for budget in MCU_BUDGETS:
        entry = {"store_bytes": budget}
        for policy in ("S", "B2c"):
            picked = [r for r in rows if r["policy"] == policy
                      and r["store_budget_bytes"] == budget]
            if not picked:
                continue
            entry[policy] = {
                "detail": round(st.mean(r["detail"] for r in picked), 4),
                "gist": round(st.mean(r["gist"] for r in picked), 4),
                "habit": round(st.mean(r["gist_habit"] for r in picked), 4),
                "comparisons": round(
                    st.mean(r["comparisons_per_question"] for r in picked), 1
                ),
                "nodes_kept": round(st.mean(r["nodes_final"] for r in picked), 1),
            }
        out.append(entry)
    return {"context_tokens": 256, "recall_ops": 32, "curve": out}


def e3_working_set() -> dict:
    """How many addresses one recall holds at once, at each effort ceiling.

    This is the number that has to fit in SRAM. The store can stay in
    flash and be read a node at a time; the frontier cannot.
    """
    trace = generate(WorldConfig(n_ticks=400, seed_root="dev-01"))
    by_tick: dict[int, list] = {}
    for episode in trace.episodes:
        by_tick.setdefault(episode.tick, []).append(episode)

    rows = []
    for ops in (4, 8, 16, 32, 64):
        policy = STree()
        policy.reset(
            budgets=Budgets(store_bytes=256 * 1024, context_tokens=256,
                            recall_ops=ops),
            tokens_of=lambda node: 20,
            seed_root="dev-01",
        )
        for tick in range(trace.config.n_ticks):
            for episode in by_tick.get(tick, ()):
                policy.perceive(episode)
            policy.on_tick(tick)

        peak = 0
        for question in trace.questions:
            if question.kind == "trigger":
                continue
            policy.recall(question)
            peak = max(peak, policy.machine.peak_working_addresses)

        rows.append({
            "recall_ops": ops,
            "peak_working_addresses": peak,
            "peak_working_bytes": peak * BYTES_PER_WORKING_ADDRESS,
        })
    return {"bytes_per_address": BYTES_PER_WORKING_ADDRESS, "ceilings": rows}


def verdict(capacity: dict, quality: dict, working: dict) -> dict:
    """State what fits and what does not. No rounding in our favour."""
    smallest = quality["curve"][0]
    largest = quality["curve"][-1]
    worst_ram = max(r["peak_working_bytes"] for r in working["ceilings"])
    tightest_sram = min(sram for _, _, sram, _ in CHIPS)

    binary_fit = {
        c["chip"]: c["memories_that_fit"]["D2_BINARY"]
        for c in capacity["chips"]
    }
    return {
        "gist_at_smallest_budget": smallest["S"]["gist"],
        "gist_at_largest_budget": largest["S"]["gist"],
        "detail_at_smallest_budget": smallest["S"]["detail"],
        "detail_at_largest_budget": largest["S"]["detail"],
        "worst_case_working_bytes": worst_ram,
        "tightest_sram_bytes": tightest_sram,
        "working_set_fits_every_chip": worst_ram < tightest_sram,
        "memories_at_binary_per_chip": binary_fit,
        "note": (
            "Capacity and working set are arithmetic on measured sizes and "
            "hold as stated. Quality is measured. None of it is a port: the "
            "implementation is Python and numpy, so 'fits' here means the "
            "design's byte and RAM budgets fit, not that this code runs on "
            "the part."
        ),
    }


def main() -> int:
    capacity = e1_capacity()
    quality = e2_quality()
    working = e3_working_set()
    out = {
        "capacity": capacity,
        "quality": quality,
        "working_set": working,
        "verdict": verdict(capacity, quality, working),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
