from somaos.bench.gate import (
    evaluate_gates,
    evaluate_kc1,
    evaluate_kc2,
    evaluate_kc3,
    evaluate_kc4,
    phase0_verdict,
)
from somaos.bench.metrics import measure_fast_path_ms_per_tick
from somaos.bench.trace.generator import from_regime, generate


def make_row(policy, regime, seed_root, split, strict_recall, competitive_ratio,
             budget_tokens=4096, tau_ticks=32, surprise_utility_spearman=0.0):
    return {
        "policy": policy, "regime": regime, "seed_root": seed_root, "seed_split": split,
        "budget_tokens": budget_tokens, "tau_ticks": tau_ticks,
        "strict_recall": strict_recall, "competitive_ratio": competitive_ratio,
        "surprise_utility_spearman": surprise_utility_spearman,
    }


def test_kc1_passes_when_s_clearly_beats_b2():
    rows = []
    for i in range(10):
        seed = f"h-{i}"
        rows.append(make_row("S", "uniform", seed, "holdout", 0.9, 0.9))
        rows.append(make_row("B2", "uniform", seed, "holdout", 0.6, 0.6))
    result, warnings = evaluate_kc1(rows)
    assert result.passed
    assert not warnings


def test_kc1_fails_when_s_does_not_beat_b2():
    rows = []
    for i in range(10):
        seed = f"h-{i}"
        rows.append(make_row("S", "uniform", seed, "holdout", 0.5, 0.5))
        rows.append(make_row("B2", "uniform", seed, "holdout", 0.6, 0.6))
    result, warnings = evaluate_kc1(rows)
    assert not result.passed


def test_kc1_excludes_adversarial_flat_from_decision_but_warns():
    rows = []
    for i in range(10):
        seed = f"h-{i}"
        rows.append(make_row("S", "uniform", seed, "holdout", 0.9, 0.9))
        rows.append(make_row("B2", "uniform", seed, "holdout", 0.6, 0.6))
        # S suspiciously beats B2 even where surprise carries no signal
        rows.append(make_row("S", "adversarial_flat", seed, "holdout", 0.9, 0.9))
        rows.append(make_row("B2", "adversarial_flat", seed, "holdout", 0.5, 0.5))
    result, warnings = evaluate_kc1(rows)
    assert result.passed  # decision only used the uniform-regime pairs
    assert any(w.code == "SUSPECTED_LEAK" for w in warnings)


def test_kc1_ignores_dev_split():
    rows = [
        make_row("S", "uniform", "dev-01", "dev", 0.99, 0.99),
        make_row("B2", "uniform", "dev-01", "dev", 0.01, 0.01),
        make_row("S", "uniform", "h-01", "holdout", 0.5, 0.5),
        make_row("B2", "uniform", "h-01", "holdout", 0.5, 0.5),
    ]
    result, _ = evaluate_kc1(rows)
    # only the (degenerate, single-pair) holdout comparison should count
    assert result.detail.count("n_pairs=1") == 1 or "n_pairs=1" in result.detail


def test_kc2_passes_above_threshold():
    rows = [make_row("S", "uniform", f"h-{i}", "holdout", 0.8, 0.75, budget_tokens=4096) for i in range(5)]
    result = evaluate_kc2(rows)
    assert result.passed
    assert result.value == 0.75


def test_kc2_fails_below_threshold():
    rows = [make_row("S", "uniform", f"h-{i}", "holdout", 0.4, 0.4, budget_tokens=4096) for i in range(5)]
    result = evaluate_kc2(rows)
    assert not result.passed


def test_kc2_ignores_wrong_budget():
    rows = [make_row("S", "uniform", "h-01", "holdout", 0.99, 0.99, budget_tokens=1024)]
    result = evaluate_kc2(rows, reference_budget=4096)
    assert not result.passed
    assert result.value is None


def test_kc3_passes_under_budget():
    result = evaluate_kc3([0.1, 0.2, 0.3, 0.15], budget_ms=1.0)
    assert result.passed


def test_kc3_fails_over_budget():
    result = evaluate_kc3([10.0, 20.0, 30.0], budget_ms=1.0)
    assert not result.passed


def test_kc4_passes_with_strong_correlation():
    rows = [make_row("S", "uniform", f"h-{i}", "holdout", 0.5, 0.5,
                      surprise_utility_spearman=0.6) for i in range(8)]
    result = evaluate_kc4(rows)
    assert result.passed


def test_kc4_fails_with_no_correlation():
    rows = [make_row("S", "uniform", f"h-{i}", "holdout", 0.5, 0.5,
                      surprise_utility_spearman=0.01) for i in range(8)]
    result = evaluate_kc4(rows)
    assert not result.passed


def test_kc4_excludes_adversarial_flat():
    rows = [make_row("S", "adversarial_flat", f"h-{i}", "holdout", 0.5, 0.5,
                      surprise_utility_spearman=0.99) for i in range(8)]
    result = evaluate_kc4(rows)
    assert not result.passed  # no non-adversarial data at all -> fail, not vacuous pass


def test_phase0_verdict_all_pass():
    from somaos.bench.gate import GateResult
    results = [GateResult(id="KC1", passed=True, value=0.1, threshold=0.05, detail="")]
    assert phase0_verdict(results) == "PASS"


def test_phase0_verdict_any_fail():
    from somaos.bench.gate import GateResult
    results = [
        GateResult(id="KC1", passed=True, value=0.1, threshold=0.05, detail=""),
        GateResult(id="KC2", passed=False, value=0.5, threshold=0.7, detail=""),
    ]
    assert phase0_verdict(results) == "FAIL"


def test_evaluate_gates_returns_four_results():
    rows = []
    for i in range(6):
        seed = f"h-{i}"
        rows.append(make_row("S", "uniform", seed, "holdout", 0.8, 0.75, surprise_utility_spearman=0.4))
        rows.append(make_row("B2", "uniform", seed, "holdout", 0.6, 0.6, surprise_utility_spearman=0.4))
    cfg = {"cost_model": {"REF_LLM_CALL_MS": 800.0, "REF_TICK_LLM_CALLS": 0.1, "FAST_PATH_BUDGET_FRACTION": 0.05}}
    results, warnings = evaluate_gates(rows, [0.1, 0.2], cfg)
    assert {r.id for r in results} == {"KC1", "KC2", "KC3", "KC4"}


# ---------- fast-path timing measurement ----------

def test_measure_fast_path_covers_every_tick():
    trace = generate(from_regime("bursty", "gate-timing-01", n_ticks=200))
    samples = measure_fast_path_ms_per_tick("S", trace, budget_tokens=1024, seed_root="gate-timing-01")
    assert len(samples) == 200
    assert all(s >= 0 for s in samples)


def test_measure_fast_path_baseline_policy_works_too():
    trace = generate(from_regime("uniform", "gate-timing-02", n_ticks=100))
    samples = measure_fast_path_ms_per_tick("B1", trace, budget_tokens=1024, seed_root="gate-timing-02")
    assert len(samples) == 100
