"""Does letting the agent choose beat a searcher that just works?

Run offline (a scripted stand-in, no endpoint needed):

    python -m somaos.bench.experiments.agent_directed

Run against a real model:

    python -m somaos.bench.experiments.agent_directed \\
        --endpoint http://localhost:11434 --model gemma3:4b --record runs/walk.jsonl

Replay a recording, no endpoint:

    python -m somaos.bench.experiments.agent_directed --replay runs/walk.jsonl

This is the question the project was reorganised around. The owner chose
option (ข): the agent picks its own way through its memory, because
otherwise it is not like a person. Everything since has been building the
thing that makes that question answerable rather than rhetorical --
a walk whose moves are a closed set, a budget enforced by the engine
rather than trusted to the chooser, and a fast path good enough that
beating it means something.

**The fast path is the control and it is not a stub.** Best-first with
backtracking over a shared frontier, which is what every published
number was measured with. If a model cannot beat it, that is the result,
and it gets reported as the result.

What is compared, per policy, on identical seeds and identical budgets:

    detail       can it say which exact thing happened
    gist         can it say what the period was about
    habit        does it know what the agent usually does
    comparisons  vector comparisons per question -- the N-08 cost
    calls        model calls per question -- what the model costs
    stalls       legal moves that achieved nothing
    off_menu     answers that named something not offered
    recovered    off-menu answers the model then corrected

Dev seeds only. This finds out whether the idea works; it does not judge
it (N-15).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys

from somaos.bench.lifeworld import WorldConfig, generate
from somaos.bench.modelclient import ChatModel, ReplayModel, RecordingModel, StubModel
from somaos.bench.score import Marker
from somaos.broker.policies.life import Budgets, STree
from somaos.broker.recall.navigator import CallableNavigator, FastPathNavigator
from somaos.broker.recall.prompting import PromptedChooser, parse_choice

#: "  3. Bring this memory to mind." -> "3"
_OPTION_LINE = re.compile(r"^(\d{1,3})\.\s")
#: "Effort left: 8 moves.  Brought to mind: 2 (room for 6 more, ...)"
_EFFORT = re.compile(r"Effort left:\s*(\d+)")
_ROOM = re.compile(r"room for (\d+) more")

SEEDS = ("dev-01", "dev-02", "dev-03")
TICKS = 200
STORE_BYTES = 200_000
CONTEXT_TOKENS = 256

#: Deliberately small. Every step is a model call, so an ops ceiling that
#: is fine for a searcher is a latency disaster for a model in the loop:
#: at 32 a single question could cost 32 round trips. Part of what this
#: experiment is for is finding out how few steps a model needs to earn
#: its keep -- so the fast path is measured at the same ceiling, and any
#: advantage it shows at higher ceilings is reported separately.
RECALL_OPS = 8


def scripted_chooser(prompt: str) -> str:
    """A stand-in that reads the menu and always takes the first move.

    Not a model and not pretending to be one. It exists so the harness
    can be finished, exercised and tested before an endpoint exists, and
    so a run that produces nothing tells us the harness is broken rather
    than that the model is bad.
    """
    lines = [line.strip() for line in prompt.splitlines()]
    numbered = [(m.group(1), line) for line in lines
                if (m := _OPTION_LINE.match(line))]
    if not numbered:
        return "stop"

    def find(fragment: str) -> str | None:
        for number, line in numbered:
            if fragment in line:
                return number
        return None

    # Alternate: take a memory as you pass it, then move on. Measured at
    # detail 0.800 against the fast path's 0.880 using a quarter of the
    # effort, which makes it a fair floor rather than a strawman -- a
    # model that cannot beat this is not navigating, and a harness where
    # nothing can score is not measuring.
    # Alternate on effort left rather than on how many have been brought
    # to mind: bringing one to mind spends no effort, so a rule keyed to
    # that count never flips back and the walker descends forever having
    # collected exactly one memory. Effort changes on every move, so it
    # is the thing that actually alternates.
    effort = 0
    for line in lines:
        found = _EFFORT.search(line)
        if found:
            effort = int(found.group(1))
    room = 0
    for line in lines:
        found = _ROOM.search(line)
        if found:
            room = int(found.group(1))

    materialize = find("Bring this memory to mind")
    if materialize and room > 0 and effort % 2 == 0:
        return materialize
    moved = find("Go into") or find("Go to a related")
    return moved or materialize or "stop"


def _build(trace, budgets: Budgets, navigator):
    policy = STree()
    policy.reset(budgets=budgets, tokens_of=_pricer(trace), seed_root="dev")
    by_tick: dict[int, list] = {}
    for episode in trace.episodes:
        by_tick.setdefault(episode.tick, []).append(episode)
    for tick in range(trace.config.n_ticks):
        for episode in by_tick.get(tick, ()):
            policy.perceive(episode)
        policy.on_tick(tick)
    return policy


def _pricer(trace):
    """Price context in tokens taken from the world, never from the policy.

    A component that prices its own output will eventually price it
    favourably; the bench holds the prices.
    """
    per_episode = {episode.id: episode.tokens for episode in trace.episodes}

    def tokens_of(node) -> int:
        return max(
            (per_episode.get(key, 0) for key in node.keys),
            default=0,
        ) or 8 + 8 * max(0, node.level)

    return tokens_of


def _run_one(seed: str, navigator, *, chooser=None) -> dict:
    trace = generate(WorldConfig(n_ticks=TICKS, seed_root=seed))
    budgets = Budgets(store_bytes=STORE_BYTES, context_tokens=CONTEXT_TOKENS,
                      recall_ops=RECALL_OPS)
    policy = _build(trace, budgets, navigator)
    marker = Marker(trace)

    results = []
    questions = [q for q in trace.questions if q.kind != "trigger"]
    for question in questions:
        policy.tree.reset_comparisons()
        from somaos.broker.recall.machine import RecallMachine

        machine = RecallMachine(
            policy.tree, ops_budget=RECALL_OPS,
            context_budget_tokens=CONTEXT_TOKENS, beam=policy.beam,
            tokens_of=policy.tokens_of,
        )
        machine.begin(topics=question.cue_topics, entities=question.cue_entities,
                      tick=question.tick, resident=policy.core.addresses())
        outcome = navigator.drive(machine, max_materialized=8)
        results.append(marker.mark(
            question, outcome.nodes, tokens=outcome.total_tokens,
            comparisons=policy.tree.comparisons, ops=outcome.path.ops_used,
        ))

    detail = [r.detail for r in results if r.kind == "detail"]
    gist = [r.gist for r in results if r.kind in ("detail", "gist", "habit")]
    habit = [r.gist for r in results if r.kind == "habit"]

    row = {
        "seed": seed,
        "questions": len(results),
        "detail": round(st.mean(detail), 4) if detail else 0.0,
        "gist": round(st.mean(gist), 4) if gist else 0.0,
        "habit": round(st.mean(habit), 4) if habit else 0.0,
        "comparisons": round(st.mean(r.comparisons for r in results), 1),
        "tokens": round(st.mean(r.tokens for r in results), 1),
        "ops": round(st.mean(r.ops for r in results), 2),
    }
    if isinstance(navigator, _Counting):
        row.update({
            "calls_per_question": round(
                navigator.total_calls / max(len(results), 1), 2
            ),
            "stalls": navigator.total_stalls,
            "off_menu": navigator.total_off_menu,
            "recovered": navigator.total_recovered,
        })
    if chooser is not None:
        row["prompt_chars_per_question"] = round(
            chooser.prompt_chars / max(len(results), 1), 1
        )
    return row


class _Counting(CallableNavigator):
    """A navigator that also keeps running totals across walks.

    ``CallableNavigator`` resets its counters on every walk, which is
    right for the navigator -- they describe one recall -- and useless
    for a run over sixty questions. Totals are kept on the object rather
    than in a dict keyed by ``id()``: CPython reuses ids once an object
    is collected, so a per-seed navigator could silently inherit the
    tally of a previous one that had been freed.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.total_calls = 0
        self.total_stalls = 0
        self.total_off_menu = 0
        self.total_recovered = 0

    def drive(self, machine, *, max_materialized: int = 8):
        result = super().drive(machine, max_materialized=max_materialized)
        self.total_calls += self.calls
        self.total_stalls += self.stalls
        self.total_off_menu += self.off_menu
        self.total_recovered += self.recovered
        return result


def compare(model, *, seeds=SEEDS, on_error: str = "stop") -> dict:
    """Run both policies over the same seeds and report both."""
    fast_rows, agent_rows = [], []

    for seed in seeds:
        fast_rows.append(_run_one(seed, FastPathNavigator()))

    for seed in seeds:
        chooser = PromptedChooser(model.complete, keep_transcript=False)
        navigator = _Counting(chooser, on_error=on_error)
        agent_rows.append(_run_one(seed, navigator, chooser=chooser))

    return {"fast_path": fast_rows, "agent_directed": agent_rows}


def _mean(rows: list[dict], key: str) -> float:
    values = [row[key] for row in rows if key in row]
    return round(st.mean(values), 4) if values else 0.0


def verdict(comparison: dict) -> dict:
    """State plainly whether the model earned its cost. No rounding our way."""
    fast, agent = comparison["fast_path"], comparison["agent_directed"]

    detail_delta = _mean(agent, "detail") - _mean(fast, "detail")
    gist_delta = _mean(agent, "gist") - _mean(fast, "gist")
    calls = _mean(agent, "calls_per_question")

    if detail_delta > 0.02 or gist_delta > 0.02:
        outcome = "agent-directed recall is better"
    elif detail_delta < -0.02 or gist_delta < -0.02:
        outcome = "agent-directed recall is worse"
    else:
        outcome = "no measurable difference"

    return {
        "fast_path": {k: _mean(fast, k) for k in ("detail", "gist", "habit", "comparisons")},
        "agent_directed": {
            k: _mean(agent, k)
            for k in ("detail", "gist", "habit", "comparisons",
                      "calls_per_question", "off_menu", "recovered", "stalls")
        },
        "detail_delta": round(detail_delta, 4),
        "gist_delta": round(gist_delta, 4),
        "model_calls_per_question": calls,
        "outcome": outcome,
        "note": (
            "The fast path is best-first search with backtracking, not a "
            "stub, and every published number was measured with it. A model "
            "that does not beat it has not earned the calls it costs. "
            "Dev seeds; this is not the holdout (N-15)."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", help="OpenAI-compatible base URL")
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--record", help="write the transcript here")
    parser.add_argument("--replay", help="replay a recorded transcript instead")
    parser.add_argument("--on-error", default="stop", choices=("stop", "raise"))
    parser.add_argument("--seeds", default=",".join(SEEDS))
    args = parser.parse_args(argv)

    seeds = tuple(s.strip() for s in args.seeds.split(",") if s.strip())

    if args.replay:
        model = ReplayModel(args.replay)
        source = f"replay:{args.replay}"
    elif args.endpoint:
        model = ChatModel(args.endpoint, model=args.model, api_key=args.api_key)
        source = f"{args.model} at {args.endpoint}"
    else:
        model = StubModel(scripted_chooser)
        source = "scripted stand-in (no endpoint)"

    recorder = RecordingModel(model, args.record) if args.record else None
    try:
        comparison = compare(recorder or model, seeds=seeds, on_error=args.on_error)
    finally:
        if recorder is not None:
            recorder.close()

    out = {
        "model": source,
        "budgets": {
            "store_bytes": STORE_BYTES,
            "context_tokens": CONTEXT_TOKENS,
            "recall_ops": RECALL_OPS,
        },
        "seeds": list(seeds),
        "seconds_waiting_on_model": round(getattr(model, "seconds", 0.0), 2),
        **comparison,
        "verdict": verdict(comparison),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
