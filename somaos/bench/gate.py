"""Kill-criteria gate. See plans/wp/WP-09-report-gate.md and
target_SomaOS.md #7.4.

Turns the four kill criteria into code that returns PASS/FAIL from
numbers alone. No threshold here may be loosened to make a run pass --
if you're tempted to, that's the sign to stop and report FAIL instead
(plans/HANDOFF_TO_SONNET.md).

Statistical note on significance: this module has no scipy dependency
(Phase 0 is stdlib + numpy only, D-01/CLAUDE.md), so "significant" is
operationalized via bootstrap confidence intervals throughout (for both
KC1's paired difference and KC4's correlation), not a parametric p-value.
A 95%/99% bootstrap CI whose lower bound clears the threshold is treated
as clearing significance at that level -- a standard, if non-classical,
substitute.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Literal

GateId = Literal["KC1", "KC2", "KC3", "KC4"]


@dataclass(frozen=True, slots=True)
class GateResult:
    id: GateId
    passed: bool
    value: float | None
    threshold: float
    detail: str


@dataclass(frozen=True, slots=True)
class Warning_:
    code: str
    detail: str


def _bootstrap_mean_ci(values: list[float], *, n_resamples: int = 10000,
                        alpha: float = 0.05, seed: int = 12345) -> tuple[float, float, float]:
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    lo_idx = max(0, int((alpha / 2) * n_resamples))
    hi_idx = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples) - 1)
    return (sum(values) / n, means[lo_idx], means[hi_idx])


def _match_key(row: dict) -> tuple:
    return (row["regime"], row["budget_tokens"], row["tau_ticks"], row["seed_root"])


def evaluate_kc1(rows: list[dict], *, min_effect: float = 0.05) -> tuple[GateResult, list[Warning_]]:
    """S must beat B2 significantly at equal budget, on holdout seeds,
    excluding adversarial_flat from the decision (surprise carries no
    signal there by construction -- see WP-02 #5.1)."""
    holdout = [r for r in rows if r["seed_split"] == "holdout"]
    s_by_key = {_match_key(r): r for r in holdout if r["policy"] == "S"}
    b2_by_key = {_match_key(r): r for r in holdout if r["policy"] == "B2"}

    diffs_main, diffs_adv = [], []
    for key, s_row in s_by_key.items():
        b2_row = b2_by_key.get(key)
        if b2_row is None:
            continue
        diff = s_row["strict_recall"] - b2_row["strict_recall"]
        if key[0] == "adversarial_flat":
            diffs_adv.append(diff)
        else:
            diffs_main.append(diff)

    warnings: list[Warning_] = []
    mean, lo, hi = _bootstrap_mean_ci(diffs_main)
    passed = bool(diffs_main) and lo > 0 and mean >= min_effect
    result = GateResult(
        id="KC1", passed=passed, value=mean, threshold=min_effect,
        detail=f"S-B2 strict_recall diff mean={mean:.4f} 95% CI=[{lo:.4f}, {hi:.4f}] "
               f"n_pairs={len(diffs_main)} (adversarial_flat excluded from this decision)",
    )

    if diffs_adv:
        adv_mean, adv_lo, adv_hi = _bootstrap_mean_ci(diffs_adv)
        if adv_lo > 0:
            warnings.append(Warning_(
                code="SUSPECTED_LEAK",
                detail=f"S beat B2 in adversarial_flat too (mean diff={adv_mean:.4f}, "
                       f"95% CI=[{adv_lo:.4f}, {adv_hi:.4f}]) -- surprise carries no signal "
                       "there by construction, so this should not happen. Check the harness "
                       "for a leak before trusting KC1 (plans/wp/WP-02-trace-generator.md #5.1).",
            ))
    return result, warnings


def evaluate_kc2(rows: list[dict], *, threshold: float = 0.7,
                  reference_budget: int = 4096) -> GateResult:
    """competitive_ratio must be computed against exact_belady (the only
    mode proven optimal, D-09) -- that means regime=uniform only."""
    values = [
        r["competitive_ratio"] for r in rows
        if r["seed_split"] == "holdout" and r["policy"] == "S"
        and r["regime"] == "uniform" and r["budget_tokens"] == reference_budget
        and r["competitive_ratio"] is not None
    ]
    if not values:
        return GateResult(id="KC2", passed=False, value=None, threshold=threshold,
                           detail="no holdout uniform-regime rows with a defined competitive_ratio")
    median = statistics.median(values)
    return GateResult(
        id="KC2", passed=median >= threshold, value=median, threshold=threshold,
        detail=f"median competitive_ratio (S, uniform, budget={reference_budget}, exact_belady)="
               f"{median:.4f} over {len(values)} holdout seeds",
    )


def evaluate_kc3(fast_path_ms: list[float], *, budget_ms: float) -> GateResult:
    if not fast_path_ms:
        return GateResult(id="KC3", passed=False, value=None, threshold=budget_ms,
                           detail="no fast-path timing samples provided")
    ordered = sorted(fast_path_ms)
    idx = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    p95 = ordered[idx]
    return GateResult(
        id="KC3", passed=p95 <= budget_ms, value=p95, threshold=budget_ms,
        detail=f"p95 fast-path ms/tick={p95:.4f} vs budget={budget_ms:.4f} "
               f"(D-07 reference cost model, n={len(ordered)} samples)",
    )


def evaluate_kc4(rows: list[dict], *, threshold: float = 0.25) -> GateResult:
    """surprise_utility_spearman is a property of the trace, not the
    policy (it never reads policy state) -- dedupe by (regime, seed) so
    each trace is counted once even though every policy shares a row."""
    seen: dict[tuple, float] = {}
    for r in rows:
        if r["seed_split"] != "holdout" or r["regime"] == "adversarial_flat":
            continue
        key = (r["regime"], r["seed_root"])
        seen.setdefault(key, r["surprise_utility_spearman"])
    values = list(seen.values())
    if not values:
        return GateResult(id="KC4", passed=False, value=None, threshold=threshold,
                           detail="no non-adversarial holdout traces found")
    median = statistics.median(values)
    _, lo, _ = _bootstrap_mean_ci(values, alpha=0.02)  # 98% CI -> one-sided ~99%
    passed = median > threshold and lo > 0
    return GateResult(
        id="KC4", passed=passed, value=median, threshold=threshold,
        detail=f"median spearman(surprise, ground_truth_utility)={median:.4f}, "
               f"98% bootstrap CI lower bound={lo:.4f}, n_traces={len(values)}",
    )


def evaluate_gates(rows: list[dict], fast_path_ms: list[float], cfg: dict) -> tuple[list[GateResult], list[Warning_]]:
    cost_model = cfg.get("cost_model", {})
    ref_ms = cost_model.get("REF_LLM_CALL_MS", 800.0)
    ref_calls = cost_model.get("REF_TICK_LLM_CALLS", 0.1)
    fraction = cost_model.get("FAST_PATH_BUDGET_FRACTION", 0.05)
    budget_ms = ref_ms * ref_calls * fraction

    kc1, kc1_warnings = evaluate_kc1(rows)
    kc2 = evaluate_kc2(rows)
    kc3 = evaluate_kc3(fast_path_ms, budget_ms=budget_ms)
    kc4 = evaluate_kc4(rows)
    return [kc1, kc2, kc3, kc4], kc1_warnings


def phase0_verdict(results: list[GateResult]) -> Literal["PASS", "FAIL"]:
    return "PASS" if all(r.passed for r in results) else "FAIL"
