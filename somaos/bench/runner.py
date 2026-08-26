"""Benchmark runner. See plans/wp/WP-08-metrics-runner.md.

No print() anywhere in this module except inside main() reporting the
output file paths (plans/00_PHASE0_MASTER_PLAN.md, CLAUDE.md rule "metric
every tuple must export as structured data, never print").
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from somaos.bench.metrics import (
    build_metric_row,
    measure_alloc_ms_at_scale,
    measure_fast_path_ms_per_tick,
)
from somaos.bench.trace.generator import from_regime, generate
from somaos.util.hashing import canonical_json, sha256_str


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def load_weights(config: dict) -> dict | None:
    wf = config.get("weights_file")
    if not wf:
        return None
    return json.loads(Path(wf).read_text())


def resolve_tasks(config: dict) -> list[dict]:
    tasks = []
    opt_cfg = config.get("opt", {})
    default_mode = opt_cfg.get("default", "upper_bound")
    for policy in config["policies"]:
        for regime in config["regimes"]:
            opt_mode = opt_cfg.get(regime, default_mode)
            for budget in config["budget_tokens"]:
                for tau in config["tau_ticks"]:
                    for split, seed_list in config["seeds"].items():
                        for seed_root in seed_list:
                            tasks.append({
                                "policy": policy, "regime": regime,
                                "budget_tokens": budget, "tau_ticks": tau,
                                "seed_root": seed_root, "seed_split": split,
                                "opt_mode": opt_mode, "n_ticks": config["n_ticks"],
                            })
    return tasks


def _sort_key(row: dict) -> tuple:
    return (row["policy"], row["regime"], row["budget_tokens"], row["tau_ticks"], row["seed_root"])


def _run_task(task: dict, weights: dict | None) -> tuple[dict, float]:
    import time

    trace = generate(from_regime(task["regime"], task["seed_root"], n_ticks=task["n_ticks"]))
    policy_config = {}
    if task["policy"] == "S" and weights is not None:
        policy_config["weights"] = weights

    start = time.perf_counter()
    row = build_metric_row(
        policy_name=task["policy"], regime=task["regime"], trace=trace,
        budget_tokens=task["budget_tokens"], tau_ticks=task["tau_ticks"],
        seed_root=task["seed_root"], seed_split=task["seed_split"],
        opt_mode=task["opt_mode"], policy_config=policy_config,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if task["policy"] == "B0" and row["strict_recall"] != 1.0:
        raise RuntimeError(
            f"B0 sanity gate failed: strict_recall={row['strict_recall']} on "
            f"regime={task['regime']} seed={task['seed_root']} -- the trace's ground "
            "truth is broken, not B0 (WP-02 #5.4). Stopping the run."
        )

    return row, elapsed_ms


def _worker(args: tuple[dict, dict | None]) -> tuple[dict, float]:
    task, weights = args
    return _run_task(task, weights)


def run_all(config: dict, *, jobs: int = 1) -> tuple[list[dict], list[dict]]:
    weights = load_weights(config)
    tasks = resolve_tasks(config)
    results: list[dict] = []
    timings: list[dict] = []

    if jobs <= 1:
        for task in tasks:
            row, elapsed_ms = _run_task(task, weights)
            results.append(row)
            timings.append({"run_id": row["run_id"], "elapsed_ms": elapsed_ms})
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for row, elapsed_ms in pool.map(_worker, [(t, weights) for t in tasks]):
                results.append(row)
                timings.append({"run_id": row["run_id"], "elapsed_ms": elapsed_ms})

    results.sort(key=_sort_key)
    timing_by_run_id = {t["run_id"]: t["elapsed_ms"] for t in timings}
    timings_sorted = [{"run_id": r["run_id"], "elapsed_ms": timing_by_run_id[r["run_id"]]} for r in results]

    return results, timings_sorted


D07_N_ITEMS = 10_000
"""The store size D-07 states KC3 must be evaluated at."""


def store_size(policy_name: str, trace, *, budget_tokens: int, seed_root: str,
               policy_config: dict) -> float:
    """How many items the policy actually ends up holding -- recorded so a
    reader can see at what N the per-tick samples were taken."""
    from somaos.broker.policy import build_policy
    from somaos.broker.types import Observation

    policy = build_policy(policy_name)
    policy.reset(budget_tokens=budget_tokens, seed_root=seed_root, config=policy_config)
    for ev in trace.events:
        if ev.kind == "observe":
            policy.observe(Observation(tick=ev.tick, item=ev.observation.item))
        policy.on_tick(ev.tick)
    stats = policy.stats()
    return float(stats.get("store_items", stats.get("encoded", 0.0)))


def run_fast_path_timing(config: dict, *, policy_name: str = "S") -> list[dict]:
    """KC3 (plans/01_DECISIONS.md D-07) needs raw per-tick fast-path timing
    samples, which the main results row doesn't carry (that's a whole-run
    number, not comparable to the D-07 budget). This is deliberately a
    *separate*, small measurement -- one trace per regime, the largest
    configured budget, the first holdout seed -- not a full sweep, since
    it exists only to sanity-check a cost claim, not to be exhaustive.
    Written to its own fastpath-*.jsonl so report.py can read it without
    ever re-running a policy itself (WP-09 acceptance)."""
    weights = load_weights(config)
    policy_config = {"weights": weights} if (policy_name == "S" and weights is not None) else {}
    budget = max(config["budget_tokens"])
    holdout_seeds = config["seeds"].get("holdout", [])
    if not holdout_seeds:
        return []
    seed_root = holdout_seeds[0]

    rows = []
    for regime in config["regimes"]:
        trace = generate(from_regime(regime, seed_root, n_ticks=config["n_ticks"]))
        samples = measure_fast_path_ms_per_tick(
            policy_name, trace, budget_tokens=budget, seed_root=seed_root, policy_config=policy_config,
        )
        rows.append({
            "kind": "per_tick_natural_scale",
            "policy": policy_name, "regime": regime, "seed_root": seed_root,
            "budget_tokens": budget, "n_samples": len(samples), "ms_per_tick": samples,
            "store_items": store_size(policy_name, trace, budget_tokens=budget,
                                       seed_root=seed_root, policy_config=policy_config),
        })

    # D-07's KC3 condition is N_items = 10,000, which the per-tick loop
    # above never reaches: the generator's universe is
    # n_entities x n_topics = 1440 pairs and S saturates near 308 items on
    # every regime. Measured at natural scale, KC3 was answering a much
    # easier question than the one it was written to ask. This row
    # measures the stated condition directly (WP-14).
    scale_trace = generate(from_regime(config["regimes"][0], seed_root, n_ticks=3500))
    tau = min(config["tau_ticks"])
    scale_samples = measure_alloc_ms_at_scale(
        scale_trace, n_items=D07_N_ITEMS, budget_tokens=budget,
        tau_ticks=tau, weights=weights,
    )
    rows.append({
        "kind": "alloc_at_d07_scale",
        "policy": policy_name, "regime": config["regimes"][0], "seed_root": seed_root,
        "budget_tokens": budget, "tau_ticks": tau, "n_items": D07_N_ITEMS,
        "n_samples": len(scale_samples), "ms_per_alloc": scale_samples,
    })
    return rows


def write_fast_path_timing(config: dict, rows: list[dict], out_dir: str | Path) -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"fastpath-{config_hash(config)}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(canonical_json(row) + "\n")
    return str(path)


def config_hash(config: dict) -> str:
    return sha256_str(canonical_json(config))[len("sha256:"):][:16]


def write_outputs(config: dict, results: list[dict], timings: list[dict], out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    h = config_hash(config)

    results_path = out / f"results-{h}.jsonl"
    timing_path = out / f"timing-{h}.jsonl"
    config_path = out / f"config-{h}.json"

    with results_path.open("w", encoding="utf-8") as f:
        for row in results:
            f.write(canonical_json(row) + "\n")

    with timing_path.open("w", encoding="utf-8") as f:
        for row in timings:
            f.write(canonical_json(row) + "\n")

    config_path.write_text(canonical_json(config), encoding="utf-8")

    return {"results": str(results_path), "timing": str(timing_path), "config": str(config_path)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SomaOS Phase 0 benchmark runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="runs")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--skip-fast-path-timing", action="store_true",
                         help="skip the extra KC3 timing pass (main sweep is unaffected)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    results, timings = run_all(config, jobs=args.jobs)
    paths = write_outputs(config, results, timings, args.out)

    print(f"wrote {len(results)} rows to {paths['results']}")
    print(f"wrote timing to {paths['timing']}")
    print(f"wrote config snapshot to {paths['config']}")

    if not args.skip_fast_path_timing:
        fp_rows = run_fast_path_timing(config)
        fp_path = write_fast_path_timing(config, fp_rows, args.out)
        print(f"wrote fast-path timing (KC3) to {fp_path}")


if __name__ == "__main__":
    main()
