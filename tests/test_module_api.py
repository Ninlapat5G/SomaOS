"""The surface an application actually touches.

Everything here imports only from ``somaos.broker``. That is the point of
the file as much as the assertions are: if using SomaOS from an app ever
requires reaching into a submodule or borrowing something from the
benchmark, one of these stops compiling.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from somaos.broker import (
    CallableEmbedder,
    CallableNavigator,
    CoreLevel,
    Cue,
    FastPathNavigator,
    HashEmbedder,
    Intent,
    NavigationError,
    Observation,
    PersistenceError,
    SomaOS,
    bytes_per_memory,
    describe,
)


def build(*, days: int = 60, **kwargs) -> SomaOS:
    kwargs.setdefault("store_budget_bytes", 200_000)
    kwargs.setdefault("context_budget_tokens", 256)
    kwargs.setdefault("recall_ops_budget", 16)
    soma = SomaOS(**kwargs)
    for day in range(days):
        soma.remember(Observation.of("nin", "coffee", "morning",
                                     tick=day, topic="routine"))
        soma.remember(Observation.of("nin", "email", "morning",
                                     tick=day, topic="routine"))
        if day % 7 == 3:
            soma.remember(Observation.of("nin", "budget", "meeting", f"wk{day // 7}",
                                         tick=day, topic="work"))
        if day == 20:
            soma.remember(Observation.of("nin", "server", "outage", "postmortem",
                                         tick=day, topic="incident"))
        soma.tick(day)
    return soma


# ----------------------------------------------------------------- events

def test_an_observation_needs_something_to_remember():
    with pytest.raises(ValueError):
        Observation(keys=(), tick=0)


def test_a_cue_with_nothing_in_it_is_refused():
    """It would match everything, which is the same as matching nothing."""
    with pytest.raises(ValueError):
        Cue()


def test_a_time_intent_without_a_time_is_refused():
    with pytest.raises(ValueError):
        Intent(id="x", kind="time")


def test_an_event_intent_without_a_cue_is_refused():
    with pytest.raises(ValueError):
        Intent(id="x", kind="event")


# ------------------------------------------------------------------ verbs

def test_remembering_returns_an_address_that_resolves():
    soma = SomaOS(store_budget_bytes=100_000)
    addr = soma.remember(Observation.of("alice", "coffee", tick=1))
    assert soma.tree.resolve(addr).node.keys == ("alice", "coffee")


def test_the_same_experience_twice_is_one_memory_counted_twice():
    """Content addressing dedupes; repetition is counted, not duplicated."""
    soma = SomaOS(store_budget_bytes=100_000)
    first = soma.remember(Observation.of("alice", "coffee", tick=1))
    second = soma.remember(Observation.of("alice", "coffee", tick=2))
    assert first == second
    assert soma.tree.occurrences(first) == 2


def test_recall_always_returns_something():
    soma = build(days=30)
    result = soma.recall(Cue.about("nothing-like-this-ever-happened", tick=30))
    assert isinstance(result.keys, tuple)
    assert result.comparisons >= 0


def test_recall_finds_what_it_is_cued_with():
    soma = build()
    found = soma.recall(Cue.about("incident", tick=60))
    assert any("incident" in keys or "outage" in keys for keys in found.keys)


def test_an_intent_fires_once_and_stays_done():
    soma = SomaOS(store_budget_bytes=100_000)
    soma.intend(Intent(id="standup", kind="time", due_tick=5))
    assert soma.tick(4) == ()
    assert soma.tick(5) == ("standup",)
    assert soma.tick(6) == ()


def test_an_event_intent_fires_on_its_cue():
    soma = SomaOS(store_budget_bytes=100_000)
    soma.intend(Intent(id="ask", kind="event", cue="alice"))
    assert soma.tick(1) == ()
    assert soma.tick(2, cues=("alice",)) == ("ask",)


def test_seeded_identity_is_resident_and_costs_no_ops():
    soma = build(days=20)
    soma.seed_identity(("careful",), level=CoreLevel.TRAIT, text_ref="is careful")
    result = soma.recall(Cue.about("routine", tick=20))
    assert "is careful" in result.text_refs


def test_budgets_must_be_positive():
    for bad in ({"store_budget_bytes": 0},
                {"store_budget_bytes": 10, "context_budget_tokens": 0},
                {"store_budget_bytes": 10, "recall_ops_budget": -1}):
        with pytest.raises(ValueError):
            SomaOS(**bad)


# ------------------------------------------------------------- persistence

def test_a_saved_agent_comes_back_the_same(tmp_path):
    soma = build()
    before = soma.stats()
    path = tmp_path / "agent.somaos"
    soma.save(path)

    again = SomaOS.load(path)
    assert again.stats() == before
    assert again.tick_count == soma.tick_count


def test_every_address_still_resolves_after_a_reload(tmp_path):
    """The promise is that an address never stops answering (N-01).

    A restart is the easiest way to break that promise, so it is checked
    over every live address and every retired one.
    """
    soma = build()
    path = tmp_path / "agent.somaos"
    soma.save(path)
    again = SomaOS.load(path)

    probed = 0
    for addr in list(soma.tree.addresses()) + list(soma.tree.alias.links):
        before = soma.tree.resolve(addr)
        after = again.tree.resolve(addr)
        assert after.node.addr == before.node.addr
        assert after.fidelity == pytest.approx(before.fidelity, abs=1e-12)
        assert np.array_equal(after.node.vec, before.node.vec)
        probed += 1
    assert probed > 0


def test_a_diluted_store_reloads_at_the_same_grades(tmp_path):
    """Grade and dtype are both part of the state, not of the encoding."""
    soma = build(days=120, store_budget_bytes=20_000)
    assert soma.tree.grade_histogram()["D0_EXACT"] < len(soma.tree), "nothing was diluted"

    path = tmp_path / "tight.somaos"
    soma.save(path)
    again = SomaOS.load(path, store_budget_bytes=20_000)

    assert again.tree.grade_histogram() == soma.tree.grade_histogram()
    for addr in soma.tree.addresses():
        assert again.tree.get(addr).vec.dtype == soma.tree.get(addr).vec.dtype


def test_recall_is_unchanged_by_a_round_trip(tmp_path):
    soma = build()
    cue = Cue.about("work", tick=60)
    before = soma.recall(cue)
    path = tmp_path / "agent.somaos"
    soma.save(path)
    assert SomaOS.load(path).recall(cue).keys == before.keys


def test_saving_without_text_leaves_retrieval_unchanged(tmp_path):
    """Invariant V1, checked the only way that means anything: by doing it."""
    soma = build()
    cue = Cue.about("work", tick=60)
    keyed = tuple(sorted(k) for k in soma.recall(cue).keys)

    path = tmp_path / "quiet.somaos"
    soma.save(path, keep_text=False)
    again = SomaOS.load(path)

    assert tuple(sorted(k) for k in again.recall(cue).keys) == keyed
    assert not any(again.tree.get(a).text_ref for a in again.tree.addresses())


def test_a_truncated_store_is_refused_not_half_loaded(tmp_path):
    soma = build(days=20)
    path = tmp_path / "agent.somaos"
    soma.save(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-3]) + "\n", encoding="utf-8")
    with pytest.raises(PersistenceError):
        SomaOS.load(path)


def test_a_foreign_file_is_refused(tmp_path):
    path = tmp_path / "not-a-store.jsonl"
    path.write_text(json.dumps({"kind": "header", "format": "something-else"}) + "\n",
                    encoding="utf-8")
    with pytest.raises(PersistenceError):
        SomaOS.load(path)


def test_a_store_written_by_another_embedder_is_refused(tmp_path):
    """Its addresses would point at vectors nothing can compare against."""
    soma = build(days=20)
    path = tmp_path / "agent.somaos"
    soma.save(path)
    with pytest.raises(ValueError, match="dimensional"):
        SomaOS.load(path, embedder=HashEmbedder(dim=768))


def test_saving_is_atomic_leaving_no_partial_file(tmp_path):
    soma = build(days=20)
    path = tmp_path / "agent.somaos"
    soma.save(path)
    assert not (tmp_path / "agent.somaos.partial").exists()
    assert list(tmp_path.iterdir()) == [path]


# ---------------------------------------------------------------- embedder

def test_a_supplied_embedder_is_used_and_normalised():
    rng = np.random.default_rng(0)
    table: dict[tuple[str, ...], np.ndarray] = {}

    def encode(keys):
        if keys not in table:                       # unnormalised on purpose
            table[keys] = rng.standard_normal(64) * 17.0
        return table[keys]

    soma = SomaOS(store_budget_bytes=100_000,
                  embedder=CallableEmbedder(encode, dim=64))
    addr = soma.remember(Observation.of("alice", "coffee", tick=1))
    vec = soma.tree.get(addr).vec
    assert vec.size == 64
    assert float(np.linalg.norm(vec)) == pytest.approx(1.0, abs=1e-5)


def test_dimensionality_changes_the_capacity_figures():
    """Every number in plans/05_EMBEDDED_TARGET.md assumes 256 dimensions."""
    small = bytes_per_memory(HashEmbedder(dim=256))
    large = bytes_per_memory(HashEmbedder(dim=768))
    assert small["D0_EXACT"] == 1024
    assert large["D0_EXACT"] == 3072
    assert large["D2_BINARY"] == 3 * small["D2_BINARY"]


def test_an_embedder_of_the_wrong_size_is_caught_at_the_boundary():
    soma = SomaOS(store_budget_bytes=100_000,
                  embedder=CallableEmbedder(lambda k: np.ones(8), dim=64))
    with pytest.raises(ValueError, match="dimensions"):
        soma.remember(Observation.of("alice", tick=1))


# --------------------------------------------------------------- navigator

def test_the_fast_path_is_the_default():
    assert isinstance(SomaOS(store_budget_bytes=1000).navigator, FastPathNavigator)


def test_a_chooser_sees_a_menu_and_no_vectors():
    seen: list[dict] = []

    def choose(view):
        seen.append(view)
        return {"move": "stop"}

    soma = build(days=30, navigator=CallableNavigator(choose))
    soma.recall(Cue.about("routine", tick=30))

    assert seen, "the chooser was never consulted"
    view = seen[0]
    assert {"state", "here", "ops_left", "options"} <= set(view)
    assert json.loads(json.dumps(view)) == view, "the view must be JSON-able"
    assert "vec" not in json.dumps(view)


def test_hiding_text_hides_it_from_the_chooser():
    views: list[dict] = []
    soma = build(days=30, navigator=CallableNavigator(
        lambda v: (views.append(v), {"move": "stop"})[1], reveal_text=False))
    soma.recall(Cue.about("routine", tick=30))
    assert views
    blob = json.dumps(views)
    assert "text_ref" not in blob


def test_a_chooser_that_stalls_is_stopped():
    """Materialising the same memory twice is legal and does nothing."""
    nav = CallableNavigator(lambda view: {"move": "materialize"})
    soma = build(days=30, navigator=nav)
    result = soma.recall(Cue.about("routine", tick=30))
    assert nav.stalls >= 1
    assert result.path["stopped_by"] == "chooser stalled"


def test_a_chooser_that_invents_a_move_degrades_instead_of_losing_the_memory():
    nav = CallableNavigator(lambda view: {"move": "teleport"})
    soma = build(days=30, navigator=nav)
    result = soma.recall(Cue.about("routine", tick=30))
    assert nav.off_menu == 1
    assert result.path["stopped_by"] == "chooser went off menu"


def test_a_broken_chooser_still_answers():
    """A model going down must cost quality, not the memory."""
    soma = build(days=30, navigator=CallableNavigator(lambda view: 1 / 0))
    result = soma.recall(Cue.about("routine", tick=30))
    assert result.path["stopped_by"] == "chooser failed"


def test_an_experiment_can_ask_for_the_mistakes_to_be_fatal():
    """Comparing model-driven recall against the fast path must not
    quietly absorb the model's errors, or it measures the absorption."""
    soma = build(days=30, navigator=CallableNavigator(
        lambda view: {"move": "teleport"}, on_error="raise"))
    with pytest.raises(NavigationError):
        soma.recall(Cue.about("routine", tick=30))


def test_a_chooser_cannot_outspend_the_ops_budget():
    calls = {"n": 0}

    def greedy(view):
        calls["n"] += 1
        scored = [o for o in view["options"] if "score" in o]
        return max(scored, key=lambda o: o["score"]) if scored else {"move": "materialize"}

    soma = build(days=60, navigator=CallableNavigator(greedy), recall_ops_budget=8)
    result = soma.recall(Cue.about("routine", tick=60))
    assert result.ops <= 8
    assert calls["n"] < 100, "the walk did not terminate promptly"


def test_the_fast_path_is_a_real_baseline_not_a_stub():
    """A naive chooser should not beat it. If it does, the control is weak
    and every model-versus-no-model comparison built on it is worthless."""
    naive = CallableNavigator(lambda view: {"move": "materialize"})
    cue = Cue.about("routine", tick=60)
    fast = build(navigator=FastPathNavigator()).recall(cue)
    dumb = build(navigator=naive).recall(cue)
    assert len(fast.keys) >= len(dumb.keys)
