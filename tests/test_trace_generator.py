import ast
import statistics
from pathlib import Path

import pytest

from somaos.bench.trace.generator import GeneratorConfig, from_regime, generate, ground_truth_utility

REGIMES = ["uniform", "variable", "long_gap", "bursty", "high_noise", "adversarial_flat", "topic_drift"]


def _spearman(xs, ys):
    """Manual Spearman rank correlation (stdlib only, no scipy dependency)."""
    def rank(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    var_x = sum((a - mean_rx) ** 2 for a in rx)
    var_y = sum((b - mean_ry) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def small_cfg(regime, seed_root="test-01", n_ticks=800):
    return from_regime(regime, seed_root, n_ticks=n_ticks)


# ---------- determinism ----------

def test_same_config_same_trace_id():
    cfg = small_cfg("uniform")
    t1 = generate(cfg)
    t2 = generate(cfg)
    assert t1.trace_id == t2.trace_id
    assert len(t1.events) == len(t2.events)
    for e1, e2 in zip(t1.events, t2.events):
        assert e1 == e2


def test_different_seed_different_trace():
    t1 = generate(small_cfg("uniform", seed_root="seed-a"))
    t2 = generate(small_cfg("uniform", seed_root="seed-b"))
    assert t1.trace_id != t2.trace_id


# ---------- no-future invariant ----------

@pytest.mark.parametrize("regime", REGIMES)
def test_no_query_asks_about_future(regime):
    trace = generate(small_cfg(regime))
    created_tick = {}
    for ev in trace.events:
        if ev.kind == "observe":
            created_tick[ev.observation.item.id] = ev.observation.item.created_tick
    for ev in trace.events:
        if ev.kind == "query":
            for iid in ev.query.required_item_ids:
                assert iid in created_tick, f"query references unknown item {iid}"
                assert created_tick[iid] < ev.query.tick, (
                    f"query at tick {ev.query.tick} requires item created at "
                    f"tick {created_tick[iid]} (not strictly before)"
                )


# ---------- regime-specific properties ----------

def test_long_gap_median_gap_at_least_500():
    trace = generate(from_regime("long_gap", "test-01", n_ticks=5000))
    created_tick = {}
    for ev in trace.events:
        if ev.kind == "observe":
            created_tick[ev.observation.item.id] = ev.observation.item.created_tick
    gaps = []
    for ev in trace.events:
        if ev.kind == "query":
            for iid in ev.query.required_item_ids:
                gaps.append(ev.query.tick - created_tick[iid])
    assert gaps, "no queries generated"
    assert statistics.median(gaps) >= 500


def test_high_noise_mostly_low_surprise():
    trace = generate(from_regime("high_noise", "test-01", n_ticks=3000))
    items = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    assert items
    low_surprise = [it for it in items if it.surprise < 0.2]
    assert len(low_surprise) / len(items) >= 0.75


def test_adversarial_flat_surprise_uncorrelated_with_utility():
    trace = generate(from_regime("adversarial_flat", "test-01", n_ticks=4000))
    utility = ground_truth_utility(trace)
    items = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    surprises = [it.surprise for it in items]
    utils = [utility[it.id] for it in items]
    rho = _spearman(surprises, utils)
    assert abs(rho) < 0.05, f"spearman={rho}"


def test_uniform_regime_uniform_tokens():
    trace = generate(from_regime("uniform", "test-01", n_ticks=500))
    items = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    tokens = {it.tokens for it in items}
    assert tokens == {100}


def test_variable_regime_has_token_variance():
    trace = generate(from_regime("variable", "test-01", n_ticks=1500))
    items = [ev.observation.item for ev in trace.events if ev.kind == "observe"]
    tokens = [it.tokens for it in items]
    assert len(set(tokens)) > 5


def test_bursty_regime_has_uneven_observation_rate():
    """Bursty regime alternates lambda within each burst_period (40 ticks:
    first half high, second half low). Compare mean obs/tick in the on-phase
    vs off-phase directly, rather than windowing at the burst period itself
    (which would smooth exactly one full cycle per window and hide the
    pattern)."""
    burst_period = 40
    on_fraction = 0.5
    trace = generate(from_regime("bursty", "test-01", n_ticks=800))
    per_tick = [0] * 800
    for ev in trace.events:
        if ev.kind == "observe":
            per_tick[ev.tick] += 1
    on_ticks = [c for t, c in enumerate(per_tick) if (t % burst_period) < burst_period * on_fraction]
    off_ticks = [c for t, c in enumerate(per_tick) if (t % burst_period) >= burst_period * on_fraction]
    mean_on = sum(on_ticks) / len(on_ticks)
    mean_off = sum(off_ticks) / len(off_ticks)
    assert mean_on > 5 * mean_off, f"mean_on={mean_on}, mean_off={mean_off}"


def test_topic_drift_active_topics_change_over_windows():
    trace = generate(from_regime("topic_drift", "test-01", n_ticks=1200))
    early_topics = set()
    late_topics = set()
    for ev in trace.events:
        if ev.kind == "query":
            if ev.query.tick < 300:
                early_topics.update(ev.query.topics)
            elif ev.query.tick >= 900:
                late_topics.update(ev.query.topics)
    assert early_topics and late_topics
    assert early_topics != late_topics


# ---------- generator doesn't know policies (layering) ----------

def test_generator_module_does_not_import_policies():
    root = Path(__file__).resolve().parent.parent
    for path in (root / "somaos" / "bench" / "trace").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "broker.policies" not in node.module, f"{path} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "broker.policies" not in alias.name, f"{path} imports {alias.name}"


# ---------- sanity: ground_truth_utility well-formed ----------

def test_ground_truth_utility_only_references_real_items():
    trace = generate(small_cfg("uniform"))
    item_ids = {ev.observation.item.id for ev in trace.events if ev.kind == "observe"}
    utility = ground_truth_utility(trace)
    assert set(utility.keys()) <= item_ids


def test_from_regime_unknown_raises():
    with pytest.raises(KeyError):
        from_regime("no_such_regime", "seed")


@pytest.mark.parametrize("regime", REGIMES)
def test_every_regime_generates_nonempty_trace(regime):
    trace = generate(small_cfg(regime, n_ticks=500))
    n_obs = sum(1 for ev in trace.events if ev.kind == "observe")
    n_q = sum(1 for ev in trace.events if ev.kind == "query")
    assert n_obs > 0
    assert n_q > 0
