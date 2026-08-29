"""WP-A2 -- the fidelity axis under capacity pressure.

Invariants under test: I2 (the store stays within budget), I4 (identity
and intent never fade), I5 (dilution is reproducible), I7 (fidelity only
falls) and, above all, N-01 -- pressure must never make a memory
unreachable.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from somaos.broker.dilution import DilutionEngine, QuotaExceeded, compose_fidelity
from somaos.broker.dilution.engine import COUNTER_FLOOR
from somaos.broker.memory.node import ArchiveLevel, Region, make_node
from somaos.broker.memory.tree import MemoryTree
from somaos.broker.memory.vector import DEFAULT_DIM, Grade, embed

D0_BYTES = DEFAULT_DIM * 4


def _tree(n=20, region=Region.ARCHIVE):
    tree = MemoryTree()
    root = make_node(
        region=region, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=embed(("work",)), keys=("work",),
    )
    tree.insert(root)
    kids = [
        tree.insert(
            make_node(
                region=region, level=int(ArchiveLevel.SPECIFIC_EVENT),
                vec=embed(("work", f"d{i}")), keys=("work", f"d{i}"),
            ),
            parent=root.addr, tick=i,
        )
        for i in range(n)
    ]
    return tree, root.addr, kids


# ---------------------------------------------------------------- fidelity bound


def test_composed_fidelity_is_a_lower_bound_on_the_true_cosine():
    """The triangle inequality on angular distance, checked numerically."""
    rng = np.random.default_rng(3)
    for _ in range(200):
        a = rng.standard_normal(64)
        b = a + rng.standard_normal(64) * 0.4
        c = b + rng.standard_normal(64) * 0.4
        cos = lambda x, y: float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y)))
        bound = compose_fidelity(cos(a, b), cos(b, c))
        assert bound <= cos(a, c) + 1e-9


def test_composed_fidelity_never_rises():
    assert compose_fidelity(0.9, 0.8) < 0.9
    assert compose_fidelity(1.0, 1.0) == pytest.approx(1.0)


def test_composed_fidelity_floors_at_zero():
    assert compose_fidelity(0.05, 0.05) == 0.0


# ---------------------------------------------------------------- budget (I2)


def test_a_tree_already_within_budget_is_left_alone():
    tree, _, _ = _tree(5)
    engine = DilutionEngine(store_budget_bytes=10 * D0_BYTES)
    assert engine.enforce(tree, tick=1) == ()
    assert tree.grade_histogram()["D0_EXACT"] == 6


def test_enforce_brings_the_store_under_budget():
    tree, _, _ = _tree(20)
    engine = DilutionEngine(store_budget_bytes=6000)
    engine.enforce(tree, tick=1000)
    assert tree.store_bytes() <= 6000


def test_enforce_is_idempotent():
    tree, _, _ = _tree(20)
    engine = DilutionEngine(store_budget_bytes=6000)
    engine.enforce(tree, tick=1000)
    settled = tree.store_bytes()
    assert engine.enforce(tree, tick=1001) == ()
    assert tree.store_bytes() == settled


def test_tighter_budgets_cost_more_fidelity():
    """The capacity curve (M1) in miniature: less room, less detail."""
    seen = []
    for budget in (16000, 6000, 1500, 500):
        tree, _, _ = _tree(20)
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=1000)
        assert tree.store_bytes() <= budget
        seen.append(tree.mean_fidelity())
    assert seen == sorted(seen, reverse=True)


# ---------------------------------------------------------------- ladder order


def test_the_cheap_rung_is_taken_everywhere_before_the_expensive_one():
    """Rung-major, not victim-major: int8 is nearly free, so spend it first."""
    tree, _, _ = _tree(20)
    # Enough pressure to need int8 across the board, not enough to need binary.
    DilutionEngine(store_budget_bytes=6000).enforce(tree, tick=1000)
    hist = tree.grade_histogram()
    assert hist["D0_EXACT"] == 0
    assert hist["D2_BINARY"] == 0
    assert hist["D1_INT8"] == 21


def test_the_coldest_memory_is_the_one_that_fades_first():
    tree, root, kids = _tree(6)
    for kid in kids[1:]:
        tree.touch(kid, tick=1000)
    DilutionEngine(store_budget_bytes=6 * D0_BYTES).enforce(tree, tick=1000)
    assert tree.get(kids[0]) is None  # replaced by a faded version
    assert tree.resolve(kids[0]).node.grade is Grade.D1_INT8
    for kid in kids[1:]:
        assert tree.get(kid).grade is Grade.D0_EXACT



def test_extreme_pressure_dissolves_into_the_group_but_never_into_nothing():
    """The floor: 'things like this happened here, n times'."""
    tree, root, kids = _tree(12)
    engine = DilutionEngine(store_budget_bytes=64)
    engine.enforce(tree, tick=5000)
    assert tree.store_bytes() <= 64
    survivor = tree.resolve(kids[0]).node.addr
    assert tree.counters.get(survivor, 0) > 0
    for kid in kids:
        assert tree.resolve(kid) is not None


def test_a_memory_too_faded_is_tallied_rather_than_dragging_the_gist():
    """D3 lets a memory shape the group; D4 says it is past being trusted to.

    Which of the two a memory gets is decided by its fidelity bound against
    COUNTER_FLOOR, so this drives the store past the floor and checks every
    dissolution took the branch its own fidelity called for.
    """
    tree, root, kids = _tree(24)
    engine = DilutionEngine(store_budget_bytes=64)
    engine.enforce(tree, tick=5000)

    dissolutions = [e for e in engine.log if e.grade_after.startswith("D3") or e.grade_after.startswith("D4")]
    assert dissolutions
    for event in dissolutions:
        counted = event.grade_after == "D4_COUNTER"
        assert counted == (event.fidelity_before < COUNTER_FLOOR)
        if counted:
            assert "too faded" in event.reason


def test_a_dissolved_child_makes_its_parent_stand_for_more():
    tree, root, kids = _tree(4)
    tree.dissolve_into_parent(kids[0])
    survivor = tree.resolve(kids[0]).node
    assert survivor.addr == root
    assert survivor.n_merged == 2
    # The parent kept its own vector, so its own fidelity is untouched --
    # what it lost is not detail about itself, it is the ability to say
    # anything specific about the child.
    assert survivor.fidelity == 1.0
    assert tree.resolve(kids[0]).fidelity < 1.0


def test_the_stored_fidelity_is_a_bound_not_a_measurement():
    """Documented looseness, pinned so nobody later mistakes it for truth.

    After a few compositions the bound sits well below the vector's real
    cosine to its original -- safe, but not the number to report.
    """
    import numpy as np
    from somaos.broker.memory.vector import similarity

    tree, root, kids = _tree(20)
    originals = {k: np.array(tree.get(k).vec, dtype=np.float32) for k in kids}
    DilutionEngine(store_budget_bytes=800).enforce(tree, tick=5000)
    for kid, original in originals.items():
        node = tree.resolve(kid).node
        bound = tree.resolve(kid).fidelity
        if node.grade in (Grade.D1_INT8, Grade.D2_BINARY):
            assert bound <= similarity(original, node.vec) + 1e-6


def test_a_tallied_child_adds_to_the_count_but_not_to_the_weight():
    """D4 says: this happened, and that is all I can vouch for."""
    tree, root, kids = _tree(4)
    before = tree.get(root).n_merged
    tree.dissolve_into_parent(kids[0], counted=True)
    assert tree.get(root).n_merged == before
    assert tree.counters[root] == 1


def test_resolving_a_dissolved_address_reports_what_is_left_of_it():
    """Not the parent's fidelity -- the parent may be pristine and still say
    almost nothing about the individual that dissolved into it."""
    tree, root, kids = _tree(8)
    tree.dissolve_into_parent(kids[0], counted=True)
    assert tree.resolve(kids[0]).fidelity < tree.get(root).fidelity


# ---------------------------------------------------------------- N-01 / N-06


def test_nothing_becomes_unreachable_no_matter_how_tight_the_budget():
    tree, _, kids = _tree(24)
    for budget in (8000, 2000, 500, 128, 32):
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=9000)
        for kid in kids:
            assert tree.resolve(kid) is not None


def test_identity_and_intent_never_fade(): 
    """I4: an agent must not stop being itself because memory filled up."""
    tree = MemoryTree()
    core = tree.insert(make_node(
        region=Region.CORE, level=0, vec=embed(("careful", "curious")),
        keys=("trait",),
    ))
    trig = tree.insert(make_node(
        region=Region.TRIGGER, level=0, vec=embed(("每morning",)), keys=("morning",),
    ))
    root = tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=embed(("work",)), keys=("work",),
    ))
    for i in range(20):
        tree.insert(make_node(
            region=Region.ARCHIVE, level=int(ArchiveLevel.SPECIFIC_EVENT),
            vec=embed(("work", f"d{i}")), keys=(f"d{i}",),
        ), parent=root, tick=i)

    DilutionEngine(store_budget_bytes=3 * D0_BYTES).enforce(tree, tick=9000)
    assert tree.get(core).grade is Grade.D0_EXACT
    assert tree.get(core).fidelity == 1.0
    assert tree.get(trig).grade is Grade.D0_EXACT


def test_a_budget_too_small_for_identity_is_an_error_not_a_quiet_erosion():
    tree = MemoryTree()
    for i in range(4):
        tree.insert(make_node(region=Region.CORE, level=0, vec=embed((f"t{i}",))))
    engine = DilutionEngine(store_budget_bytes=D0_BYTES)
    with pytest.raises(QuotaExceeded):
        engine.enforce(tree, tick=0)


def test_reserved_and_available_split_the_budget():
    tree = MemoryTree()
    tree.insert(make_node(region=Region.CORE, level=0, vec=embed(("me",))))
    engine = DilutionEngine(store_budget_bytes=10 * D0_BYTES)
    assert engine.reserved_bytes(tree) == D0_BYTES
    assert engine.available_bytes(tree) == 9 * D0_BYTES


def test_a_parentless_node_is_never_dissolved_into_nowhere():
    tree = MemoryTree()
    lone = tree.insert(make_node(
        region=Region.ARCHIVE, level=int(ArchiveLevel.GENERAL_EVENT),
        vec=embed(("only",)), keys=("only",),
    ))
    DilutionEngine(store_budget_bytes=1).enforce(tree, tick=100)
    assert tree.resolve(lone) is not None
    assert tree.resolve(lone).node.grade is Grade.D2_BINARY  # faded, not gone


# ---------------------------------------------------------------- I7 / I5


def test_fidelity_only_ever_falls():
    tree, _, kids = _tree(16)
    seen = {k: 1.0 for k in kids}
    for budget in (10000, 4000, 1200, 300):
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=9000)
        for kid in kids:
            now = tree.resolve(kid).fidelity
            assert now <= seen[kid] + 1e-9
            seen[kid] = now


def test_grade_only_ever_advances():
    tree, _, kids = _tree(16)
    seen = {k: Grade.D0_EXACT for k in kids}
    for budget in (10000, 4000, 1200, 300):
        DilutionEngine(store_budget_bytes=budget).enforce(tree, tick=9000)
        for kid in kids:
            now = tree.resolve(kid).node.grade
            assert now >= seen[kid]
            seen[kid] = now


def test_dilution_is_reproducible():
    """I5: same store, same order, same result -- replay depends on it."""
    def run():
        tree, _, kids = _tree(20)
        engine = DilutionEngine(store_budget_bytes=1500)
        engine.enforce(tree, tick=1000)
        return (
            tree.store_bytes(),
            tree.grade_histogram(),
            [tree.resolve(k).node.addr for k in kids],
            [round(e.fidelity_after, 12) for e in engine.log],
        )

    assert run() == run()


# ---------------------------------------------------------------- audit trail


def test_every_fade_is_logged_with_what_it_cost():
    tree, _, _ = _tree(20)
    engine = DilutionEngine(store_budget_bytes=1500)
    events = engine.enforce(tree, tick=1234)
    assert events and len(engine.log) == len(events)
    for event in events:
        assert event.tick == 1234
        assert event.bytes_after <= event.bytes_before
        assert event.fidelity_after <= event.fidelity_before + 1e-9
        assert event.reason
        assert set(event.to_jsonable()) >= {
            "addr_before", "addr_after", "grade_before", "grade_after",
            "fidelity_before", "fidelity_after", "retrieval_strength",
        }


def test_the_log_lets_you_follow_one_memory_down_the_ladder():
    tree, _, kids = _tree(20)
    engine = DilutionEngine(store_budget_bytes=1500)
    engine.enforce(tree, tick=1000)
    by_before = {e.addr_before: e for e in engine.log}
    addr, steps = kids[0], []
    while addr in by_before:
        event = by_before[addr]
        steps.append((event.grade_before, event.grade_after))
        addr = event.addr_after
    assert steps  # the coldest memory moved at least one rung
    assert [s[0] for s in steps[1:]] == [s[1] for s in steps[:-1]]


# ------------------------------------------------ the log records outcomes

def _pressured(*, budget: int, days: int = 400):
    """An agent squeezed hard enough that enforcement runs out of moves."""
    from somaos.broker import CoreLevel, Observation, SomaOS

    soma = SomaOS(store_budget_bytes=budget, context_budget_tokens=256,
                  recall_ops_budget=16)
    soma.seed_identity(("careful",), level=CoreLevel.TRAIT)
    for day in range(days):
        soma.remember(Observation.of("nin", "coffee", "morning",
                                     tick=day, topic="routine"))
        soma.remember(Observation.of("nin", "task%d" % day, "work",
                                     tick=day, topic="work"))
        soma.tick(day)
    return soma


def test_every_event_matches_the_tree_at_the_moment_it_is_emitted():
    """The log is the evidence for the dilution argument, so it must not
    record a degradation that did not happen.

    It did. ``replace_node`` has two legitimate refusals -- the re-encoded
    content already sits at this address, or at one that forwards back
    here -- and both return the old address unchanged. ``_apply_rung``
    reported its intent regardless, so a store could sit over budget with
    a log claiming it had been brought under.
    """
    from somaos.broker import CoreLevel, Observation, SomaOS

    soma = SomaOS(store_budget_bytes=12_000, context_budget_tokens=256,
                  recall_ops_budget=16)
    soma.seed_identity(("careful",), level=CoreLevel.TRAIT)

    checked = 0
    for day in range(200):
        soma.remember(Observation.of("nin", "coffee", "morning",
                                     tick=day, topic="routine"))
        soma.remember(Observation.of("nin", "task%d" % day, "work",
                                     tick=day, topic="work"))
        seen = len(soma.dilution.log)
        soma.tick(day)
        for event in soma.dilution.log[seen:]:
            node = soma.tree.get(event.addr_after)
            if node is None or event.grade_after in ("D3_MERGED", "D4_COUNTER"):
                continue                      # dissolved: nothing left to check
            checked += 1
            assert node.grade.name == event.grade_after
            assert node.nbytes == event.bytes_after

    assert checked > 0, "no dilution happened, so nothing was verified"


def test_a_refused_dilution_produces_no_event():
    soma = _pressured(budget=12_000)
    before = soma.tree.store_bytes()
    events = soma.dilution.enforce(soma.tree, tick=400)
    if soma.tree.store_bytes() == before:
        assert events == (), "nothing changed, so nothing should be reported"


def test_running_out_of_room_is_reported_not_swallowed():
    """A knob called "brain size" may not quietly fail to hold.

    Enforcement can genuinely run out of moves. What it must not do is
    return as though it succeeded, leaving the store over budget with
    nothing anywhere saying so.
    """
    soma = _pressured(budget=12_000)
    over = soma.stats()["over_budget_bytes"]
    if soma.tree.store_bytes() > soma.store_budget_bytes:
        assert over > 0
        assert over == soma.tree.store_bytes() - soma.store_budget_bytes
    else:
        assert over == 0


def test_a_store_with_room_reports_no_shortfall():
    soma = _pressured(budget=2_000_000, days=100)
    assert soma.stats()["over_budget_bytes"] == 0
    assert soma.dilution.shortfall(soma.tree) == 0


def test_identity_never_fades_however_hard_the_store_is_squeezed():
    """The guarantee an application is really buying: same agent after a
    reload, whatever the memory has been through (N-06)."""
    soma = _pressured(budget=12_000)
    assert soma.stats()["identity"] > 0
    for addr in soma.core.addresses():
        assert soma.tree.get(addr).grade.name == "D0_EXACT"
        assert soma.tree.get(addr).fidelity == 1.0
