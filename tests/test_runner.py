import ast
import json
from pathlib import Path

import pytest

from somaos.bench import runner


def small_config(tmp_out=None):
    return {
        "policies": ["B0", "B1", "S"],
        "regimes": ["uniform"],
        "budget_tokens": [1024],
        "tau_ticks": [32],
        "n_ticks": 300,
        "seeds": {"dev": ["dev-01"], "holdout": ["h-01"]},
        "opt": {"uniform": "exact_belady", "default": "upper_bound"},
        "weights_file": "somaos/bench/configs/default_weights.json",
    }


def test_resolve_tasks_cartesian_product():
    cfg = small_config()
    tasks = runner.resolve_tasks(cfg)
    # 3 policies * 1 regime * 1 budget * 1 tau * 2 seeds (1 dev + 1 holdout) = 6
    assert len(tasks) == 6


def test_run_all_deterministic_repeat():
    cfg = small_config()
    r1, t1 = runner.run_all(cfg, jobs=1)
    r2, t2 = runner.run_all(cfg, jobs=1)
    assert r1 == r2


def test_run_all_jobs_parallel_matches_serial():
    cfg = small_config()
    r_serial, _ = runner.run_all(cfg, jobs=1)
    r_parallel, _ = runner.run_all(cfg, jobs=2)
    assert r_serial == r_parallel


def test_write_outputs_roundtrip(tmp_path):
    cfg = small_config()
    results, timings = runner.run_all(cfg, jobs=1)
    paths = runner.write_outputs(cfg, results, timings, tmp_path)

    lines = Path(paths["results"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(results)
    for line in lines:
        row = json.loads(line)
        assert "strict_recall" in row

    timing_lines = Path(paths["timing"]).read_text(encoding="utf-8").splitlines()
    assert len(timing_lines) == len(results)

    saved_config = json.loads(Path(paths["config"]).read_text(encoding="utf-8"))
    assert saved_config == cfg


def test_write_outputs_byte_identical_across_runs(tmp_path):
    cfg = small_config()
    r1, t1 = runner.run_all(cfg, jobs=1)
    r2, t2 = runner.run_all(cfg, jobs=2)
    p1 = runner.write_outputs(cfg, r1, t1, tmp_path / "a")
    p2 = runner.write_outputs(cfg, r2, t2, tmp_path / "b")
    content1 = Path(p1["results"]).read_bytes()
    content2 = Path(p2["results"]).read_bytes()
    assert content1 == content2


def test_b0_sanity_gate_hard_failure_on_broken_trace(monkeypatch):
    """If B0 doesn't get strict_recall==1.0, the runner must raise, not
    just warn (WP-08 acceptance #4)."""
    import somaos.bench.metrics as metrics_mod

    original = metrics_mod.build_metric_row

    def broken(*args, **kwargs):
        row = original(*args, **kwargs)
        if kwargs.get("policy_name") == "B0":
            row = dict(row)
            row["strict_recall"] = 0.5
        return row

    monkeypatch.setattr(runner, "build_metric_row", broken)
    cfg = small_config()
    cfg["policies"] = ["B0"]
    with pytest.raises(RuntimeError, match="B0 sanity gate failed"):
        runner.run_all(cfg, jobs=1)


def test_no_print_in_bench_modules_except_runner_main():
    root = Path(__file__).resolve().parent.parent / "somaos" / "bench"
    for path in root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        print_calls_outside_main = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                continue  # allow print() inside main(); checked separately below
        # Find all Call nodes to print(), then check none are inside a
        # FunctionDef named "main".
        main_ranges = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_ranges.append((node.lineno, node.end_lineno))

        def in_main(lineno):
            return any(start <= lineno <= end for start, end in main_ranges)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                assert in_main(node.lineno), f"print() outside main() in {path}:{node.lineno}"
