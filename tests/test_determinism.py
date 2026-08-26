"""End-to-end determinism guarantees. See plans/wp/WP-10-determinism-ci.md #1.

These duplicate a couple of checks that already exist closer to their
source (e.g. test_runner.py's --jobs equivalence), but from the outside:
via subprocess with different PYTHONHASHSEED, which is the one thing an
in-process test can never fully rule out (str/bytes hashing IS
randomized per-process regardless of what the test does inside that
process)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SMOKE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from somaos.bench import runner
cfg = runner.load_config("somaos/bench/configs/smoke.json")
cfg["n_ticks"] = 200
results, timings = runner.run_all(cfg, jobs=1)
print(len(results))
for r in results:
    print(r["run_id"], r["strict_recall"], r["competitive_ratio"])
"""


def _run_subprocess(hashseed: str) -> str:
    import os

    env = dict(os.environ, PYTHONHASHSEED=hashseed)
    result = subprocess.run(
        [sys.executable, "-c", SMOKE_SCRIPT.format(root=str(ROOT))],
        env=env, capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_full_run_stable_across_hashseed():
    out0 = _run_subprocess("0")
    out1 = _run_subprocess("1")
    assert out0 == out1, "results differ under different PYTHONHASHSEED -- something used hash() or set/dict ordering"


def test_repeat_run_in_process_is_identical():
    from somaos.bench import runner

    cfg = runner.load_config("somaos/bench/configs/smoke.json")
    r1, _ = runner.run_all(cfg, jobs=1)
    r2, _ = runner.run_all(cfg, jobs=1)
    assert r1 == r2
