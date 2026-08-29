"""The harness that will drive a real model, proven before one exists.

Everything here runs offline. That is the point: when the endpoint
arrives, a bad result should mean the model is bad, not that the glue
was never exercised.
"""
from __future__ import annotations

import json

import pytest

from somaos.bench.modelclient import (
    ModelError,
    RecordingModel,
    ReplayModel,
    StubModel,
)
from somaos.broker import (
    CallableNavigator,
    Cue,
    FastPathNavigator,
    NavigationError,
    Observation,
    SomaOS,
    describe,
)
from somaos.broker.recall.machine import RecallMachine
from somaos.broker.recall.prompting import (
    PromptedChooser,
    parse_choice,
    render_prompt,
)


def build(*, days: int = 60, **kwargs) -> SomaOS:
    kwargs.setdefault("store_budget_bytes", 200_000)
    kwargs.setdefault("context_budget_tokens", 256)
    kwargs.setdefault("recall_ops_budget", 8)
    soma = SomaOS(**kwargs)
    for day in range(days):
        soma.remember(Observation.of("nin", "coffee", "morning",
                                     tick=day, topic="routine"))
        soma.remember(Observation.of("nin", "email", "morning",
                                     tick=day, topic="routine"))
        if day == 20:
            soma.remember(Observation.of("nin", "server", "outage",
                                         tick=day, topic="incident",
                                         text_ref="the night the server fell over"))
        soma.tick(day)
    return soma


def a_view(soma: SomaOS, *, topic: str = "incident") -> dict:
    machine = RecallMachine(soma.tree, ops_budget=8, context_budget_tokens=256,
                            beam=soma.beam, tokens_of=soma.tokens_of)
    machine.begin(topics=(topic,), tick=60, resident=soma.core.addresses())
    return describe(machine, max_materialized=8)


# ------------------------------------------------------------- the prompt

def test_the_prompt_never_shows_a_content_address():
    """A 4B model asked to copy back 71 hex characters will fail.

    It drops one, or invents a plausible-looking one, and every such
    answer arrives as an off-menu move. The prompt shows numbers.
    """
    prompt = render_prompt(a_view(build()))
    assert "addr:" not in prompt
    assert not any(len(word) > 40 for word in prompt.split())


def test_the_prompt_shows_only_where_the_walk_stands():
    """Not the store. A prompt listing everything is not navigation."""
    soma = build()
    prompt = render_prompt(a_view(soma))
    mentioned = sum(1 for addr in soma.tree.addresses()
                    if (node := soma.tree.get(addr)) and node.text_ref
                    and node.text_ref in prompt)
    assert mentioned <= 6, "the prompt is showing too much of the store"


def test_the_prompt_says_that_bringing_to_mind_is_free():
    """Moving spends effort and bringing a memory to mind does not.

    A model told only "effort left" will assume both cost, hoard its
    steps, and come back with nothing -- which is exactly what the
    scripted stand-in did before this was stated.
    """
    prompt = render_prompt(a_view(build()))
    assert "no cost in effort" in prompt or "uses none" in prompt


def test_the_prompt_is_deterministic():
    view = a_view(build())
    assert render_prompt(view) == render_prompt(view)


def test_a_retry_prompt_carries_the_reason():
    view = dict(a_view(build()), error="'teleport' is not a move")
    assert "teleport" in render_prompt(view)


# -------------------------------------------------------------- the parser

@pytest.mark.parametrize("reply", [
    "2", "  2  ", "2.", "Option 2", "I choose 2", "CHOICE: 2", "choice=2",
    '{"choice": 2}', '```json\n{"choice": 2}\n```', "```\n2\n```", "**2**",
    "The best option here is 2.", "Answer: 2\n\nBecause of the outage.",
])
def test_the_parser_survives_how_a_small_model_actually_replies(reply):
    view = a_view(build())
    assert parse_choice(reply, view) in view["options"]


@pytest.mark.parametrize("reply", ["stop", "I think we should stop.", '{"move": "stop"}'])
def test_stop_is_understood_however_it_is_phrased(reply):
    view = a_view(build())
    assert parse_choice(reply, view)["move"] == "stop"


@pytest.mark.parametrize("reply", ["99", "", "teleport", "I don't know", '{"choice": 0}'])
def test_an_answer_off_the_menu_is_refused(reply):
    """Generous about form, strict about outcome. Being loose here would
    make "the model chose" meaningless."""
    with pytest.raises(NavigationError):
        parse_choice(reply, a_view(build()))


@pytest.mark.parametrize("reply", [
    "no idea", "I have no idea what you mean.", "not sure",
    "none of these make sense to me",
])
def test_confusion_is_not_read_as_a_decision_to_stop(reply):
    """"no idea" contains a standalone "no", and scanning for stop words
    anywhere in the reply turned that into a deliberate stop.

    A model that has just said it is lost would have been recorded as
    having chosen to finish -- so an experiment asking whether the model
    navigates well would have scored its confusion as decisiveness. It is
    refused instead, which sends it back the menu with the reason.
    """
    with pytest.raises(NavigationError):
        parse_choice(reply, a_view(build()))


def test_an_ambiguous_move_name_is_refused_rather_than_guessed():
    """Four children all carry "descend"; picking one would be inventing
    the model's decision rather than reading it."""
    view = a_view(build())
    if sum(1 for o in view["options"] if o["move"] == "descend") > 1:
        with pytest.raises(NavigationError, match="answer with a number"):
            parse_choice('{"move": "descend"}', view)


# ------------------------------------------------------------ end to end

def test_a_scripted_model_can_drive_a_whole_recall():
    from somaos.bench.experiments.agent_directed import scripted_chooser

    model = StubModel(scripted_chooser)
    chooser = PromptedChooser(model.complete)
    soma = build(navigator=CallableNavigator(chooser))
    result = soma.recall(Cue.about("incident", tick=60))

    assert model.calls > 1, "the model was consulted once and gave up"
    assert result.keys, "the walk brought nothing back"
    assert chooser.prompt_chars > 0


def test_a_model_that_always_answers_off_menu_still_returns_a_memory():
    model = StubModel(lambda prompt: "I have no idea what you mean.")
    soma = build(navigator=CallableNavigator(PromptedChooser(model.complete)))
    result = soma.recall(Cue.about("incident", tick=60))
    assert result.path["stopped_by"] == "chooser went off menu"


def test_a_model_that_fumbles_once_is_shown_the_reason_and_recovers():
    seen: list[str] = []

    def flaky(prompt: str) -> str:
        seen.append(prompt)
        return "no idea" if len(seen) == 1 else "1"

    navigator = CallableNavigator(PromptedChooser(StubModel(flaky).complete))
    soma = build(navigator=navigator)
    soma.recall(Cue.about("incident", tick=60))

    assert navigator.recovered >= 1
    assert "could not be used" in seen[1]


def test_the_model_cannot_outspend_the_effort_ceiling():
    model = StubModel(lambda prompt: "1")
    soma = build(navigator=CallableNavigator(PromptedChooser(model.complete)),
                 recall_ops_budget=4)
    result = soma.recall(Cue.about("incident", tick=60))
    assert result.ops <= 4


# ------------------------------------------------------------ record/replay

def test_a_run_can_be_recorded_and_replayed_without_the_endpoint(tmp_path):
    """A model run is slow and drifts with sampling and version. Being
    able to replay the exact transcript is what makes a result checkable
    by someone who does not have the endpoint."""
    from somaos.bench.experiments.agent_directed import scripted_chooser

    path = tmp_path / "walk.jsonl"
    with RecordingModel(StubModel(scripted_chooser), path) as recorder:
        live = build(navigator=CallableNavigator(
            PromptedChooser(recorder.complete)))
        first = live.recall(Cue.about("incident", tick=60))

    replayed = build(navigator=CallableNavigator(
        PromptedChooser(ReplayModel(path).complete)))
    assert replayed.recall(Cue.about("incident", tick=60)).keys == first.keys


def test_a_replay_that_no_longer_lines_up_is_refused(tmp_path):
    """Otherwise it answers the wrong question with the right-looking
    text, and the run looks reproducible while being nothing of the kind."""
    path = tmp_path / "stale.jsonl"
    path.write_text(json.dumps({"prompt": "a prompt from another run",
                                "reply": "1"}) + "\n", encoding="utf-8")
    soma = build(navigator=CallableNavigator(
        PromptedChooser(ReplayModel(path).complete), on_error="raise"))
    with pytest.raises(ModelError, match="diverged"):
        soma.recall(Cue.about("incident", tick=60))


def test_a_replay_that_runs_out_says_so(tmp_path):
    path = tmp_path / "short.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ModelError, match="different run"):
        ReplayModel(path).complete("anything")


# ------------------------------------------------------------- the compare

def test_the_offline_comparison_produces_a_real_verdict():
    """The harness has to discriminate before a model is plugged into it."""
    from somaos.bench.experiments.agent_directed import (
        compare, scripted_chooser, verdict,
    )

    result = compare(StubModel(scripted_chooser), seeds=("dev-01",))
    decided = verdict(result)

    assert decided["fast_path"]["detail"] > 0.5, "the control scored nothing"
    assert decided["agent_directed"]["detail"] > 0.0, "the stand-in is a strawman"
    assert decided["agent_directed"]["calls_per_question"] > 1
    assert decided["outcome"] in (
        "agent-directed recall is better",
        "agent-directed recall is worse",
        "no measurable difference",
    )


def test_the_fast_path_control_needs_no_model_at_all():
    soma = build(navigator=FastPathNavigator())
    assert soma.recall(Cue.about("incident", tick=60)).keys
