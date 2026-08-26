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

from somaos.bench.metrics import build_metric_row
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
    args = parser.parse_args(argv)

    config = load_config(args.config)
    results, timings = run_all(config, jobs=args.jobs)
    paths = write_outputs(config, results, timings, args.out)

    print(f"wrote {len(results)} rows to {paths['results']}")
    print(f"wrote timing to {paths['timing']}")
    print(f"wrote config snapshot to {paths['config']}")


if __name__ == "__main__":
    main()
