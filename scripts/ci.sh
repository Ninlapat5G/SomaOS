#!/usr/bin/env bash
# Phase 0 CI script. See plans/wp/WP-10-determinism-ci.md #4.
# Repo isn't wired to a remote CI provider yet -- run this locally, or
# point a GitHub Actions / etc. workflow at it once one exists.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== lint (ruff, optional) =="
if command -v ruff >/dev/null 2>&1; then
    ruff check .
else
    echo "ruff not installed, skipping (not a hard dependency)"
fi

echo "== unit tests (excluding perf) =="
python -m pytest -q -m "not perf"

echo "== perf tests (informational, non-blocking) =="
python -m pytest -q -m perf -s || echo "perf tests failed/slow -- see output above, not blocking CI"

echo "== smoke benchmark run =="
rm -rf runs
python -m somaos.bench.runner --config somaos/bench/configs/smoke.json --out runs

echo "== smoke report =="
python -m somaos.bench.report --in runs --out runs/report.md
head -n 1 runs/report.md

echo "== CI done =="
