"""D-14 page-fault coverage. See somaos/bench/coverage.py and
plans/01_DECISIONS.md D-14.

The end-to-end test at the bottom is the important one: it drives a
deliberately parasitic policy through the *real* metric pipeline and
asserts the scoreboard refuses to reward it. That policy scored a
perfect 1.0 under D-13."""
from typing import Mapping

import pytest

from somaos.bench.coverage import raw_token_map, resolve_coverage
from somaos.bench.metrics import build_metric_row, run_policy_on_trace
from somaos.bench.trace.generator import from_regime, generate
from somaos.broker.policy import register_policy
from somaos.broker.types import ContextBundle, MemoryItem, Observation, QueryView


def item(id_, tokens=10, source_item_ids=()):
    return MemoryItem(id=id_, kind="episodic", tokens=tokens, created_tick=0,
                       topics=(), entities=(), surprise=0.5, novelty=0.0,
                       source_item_ids=source_item_ids)


def test_no_items_covers_nothing():
    cov = resolve_coverage([], budget_tokens=100, raw_tokens={})
    assert cov.covered == frozenset()
    assert cov.fault_tokens == 0


def test_resident_ids_cost_nothing():
    cov = resolve_coverage([item("a", tokens=99)], budget_tokens=100,
                           raw_tokens={"a": 99})
    assert cov.resident == frozenset({"a"})
    assert cov.faulted == frozenset()
    assert cov.fault_tokens == 0


def test_resident_beats_pointer_for_the_same_id():
    """An id that is both resident and pointed-at must never be charged."""
    it = item("a", tokens=10, source_item_ids=("a",))
    cov = resolve_coverage([it], budget_tokens=10, raw_tokens={"a": 10})
    assert cov.resident == frozenset({"a"})
    assert cov.faulted == frozenset()
    assert cov.fault_tokens == 0


def test_faults_are_charged_at_raw_size_not_pointer_size():
    holder = item("h", tokens=1, source_item_ids=("big",))
    cov = resolve_coverage([holder], budget_tokens=100, raw_tokens={"big": 80})
    assert cov.faulted == frozenset({"big"})
    assert cov.fault_tokens == 80
    assert cov.covered == frozenset({"h", "big"})


def test_fault_deferred_when_residual_too_small():
    holder = item("h", tokens=1, source_item_ids=("big",))
    cov = resolve_coverage([holder], budget_tokens=50, raw_tokens={"big": 80})
    assert cov.faulted == frozenset()
    assert cov.deferred == frozenset({"big"})


def test_faults_are_serviced_in_policy_declared_order():
    """The policy's own ordering is the priority, and the resolver
    honours exactly that -- no reshuffling toward whatever happens to
    fit, which would be clairvoyance by another name."""
    holder = item("h", tokens=1, source_item_ids=("first", "second", "third"))
    raw = {"first": 30, "second": 10, "third": 10}
    cov = resolve_coverage([holder], budget_tokens=31, raw_tokens=raw)
    assert cov.faulted == frozenset({"first"})
    assert cov.deferred == frozenset({"second", "third"})
    # Reversing the policy's stated order changes what it gets back.
    holder2 = item("h", tokens=1, source_item_ids=("second", "third", "first"))
    cov2 = resolve_coverage([holder2], budget_tokens=31, raw_tokens=raw)
    assert cov2.faulted == frozenset({"second", "third"})


def test_queue_stops_at_the_first_page_it_cannot_afford():
    holder = item("h", tokens=1, source_item_ids=("big", "small"))
    cov = resolve_coverage([holder], budget_tokens=50,
                           raw_tokens={"big": 80, "small": 1})
    assert cov.faulted == frozenset()
    assert cov.deferred == frozenset({"big", "small"})


def test_bundle_order_drives_the_queue_across_items():
    a = item("a", tokens=1, source_item_ids=("pa",))
    b = item("b", tokens=1, source_item_ids=("pb",))
    raw = {"pa": 10, "pb": 10}
    cov = resolve_coverage([a, b], budget_tokens=13, raw_tokens=raw)
    assert cov.faulted == frozenset({"pa"})
    assert cov.deferred == frozenset({"pb"})


def test_unknown_raw_size_is_deferred_not_free():
    holder = item("h", tokens=1, source_item_ids=("ghost",))
    cov = resolve_coverage([holder], budget_tokens=1000, raw_tokens={})
    assert cov.deferred == frozenset({"ghost"})
    assert cov.fault_tokens == 0


def test_resolution_is_deterministic():
    holder = item("h", tokens=1, source_item_ids=("z", "y", "x"))
    raw = {"x": 10, "y": 10, "z": 10}
    first = resolve_coverage([holder], budget_tokens=21, raw_tokens=raw)
    for _ in range(5):
        again = resolve_coverage([holder], budget_tokens=21, raw_tokens=raw)
        assert again.faulted == first.faulted
    assert first.faulted == frozenset({"z", "y"})


def test_resolver_signature_cannot_see_the_answer_key():
    """Structural guard, same spirit as QueryView having no
    required_item_ids field: if someone ever threads the answer key back
    into fault selection, this fails."""
    import inspect

    params = set(inspect.signature(resolve_coverage).parameters)
    assert "required_item_ids" not in params
    assert params == {"items", "budget_tokens", "raw_tokens"}


def test_raw_token_map_comes_from_the_trace():
    trace = generate(from_regime("uniform", "cov-01", n_ticks=200))
    raw = raw_token_map(trace)
    observed = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    assert len(raw) == len({it.id for it in observed})
    for it in observed:
        assert raw[it.id] == it.tokens


# --------------------------------------------------------------------------
# End-to-end: the exploit D-14 exists to close.
# --------------------------------------------------------------------------


@register_policy("TEST_HOARDER")
class PointerHoarderPolicy:
    """Adversarial policy: keeps no content at all, only a single
    1-token receipt listing every id it has ever seen. Under D-13 this
    scored strict_recall = 1.0 on every regime at any budget, which is
    what proved the metric was broken rather than the policies good."""

    name = "TEST_HOARDER"
    ignores_budget = False

    def __init__(self) -> None:
        self._seen: list[str] = []
        self._budget = 0

    def reset(self, *, budget_tokens: int, seed_root: str, config: Mapping) -> None:
        self._seen = []
        self._budget = budget_tokens

    def observe(self, obs: Observation):
        self._seen.append(obs.item.id)
        return None

    def on_tick(self, tick: int) -> None:
        pass

    def on_query(self, q: QueryView) -> ContextBundle:
        receipt = MemoryItem(
            id="receipt", kind="semantic", tokens=1, created_tick=q.tick,
            topics=(), entities=(), surprise=0.0, novelty=0.0,
            source_item_ids=tuple(self._seen),
        )
        return ContextBundle(query_id=q.id, tick=q.tick,
                             budget_tokens=self._budget, items=(receipt,))

    def stats(self) -> dict[str, float]:
        return {}


@pytest.mark.parametrize("budget", [1024, 4096])
def test_pointer_hoarder_cannot_win_end_to_end(budget):
    trace = generate(from_regime("uniform", "cov-hoard", n_ticks=1000))
    run = run_policy_on_trace("TEST_HOARDER", trace,
                              budget_tokens=budget, seed_root="cov-hoard")
    # It gets *something* -- it does have real headroom, and D-14 charges
    # rather than forbids. What it must not get is a free perfect score.
    assert run.strict_recall < 0.95, (
        "a policy that stores nothing but receipts is scoring near-perfect "
        "recall -- the D-14 page-fault rule is not being applied"
    )
    assert run.direct_covered == 0, "it holds no real content, so nothing is resident"
    assert run.page_fault_tokens > 0 or run.pointer_denied > 0


def test_hoarder_pays_for_everything_it_answers():
    """Every single answer it gives must show up as billed tokens."""
    trace = generate(from_regime("uniform", "cov-hoard2", n_ticks=800))
    row = build_metric_row(
        policy_name="TEST_HOARDER", regime="uniform", trace=trace,
        budget_tokens=4096, tau_ticks=32, seed_root="cov-hoard2",
        seed_split="dev", opt_mode="exact_belady",
    )
    assert row["answered_via_pointer_rate"] == 1.0
    assert row["effective_tokens_per_query"] > row["tokens_per_query"]
    assert row["competitive_ratio"] is None or row["competitive_ratio"] <= 1.0 + 1e-9
