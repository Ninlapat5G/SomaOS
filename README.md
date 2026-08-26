# SomaOS — Phase 0

Memory-policy spike. See `CLAUDE.md` and `plans/` before touching anything.

```bash
pip install -e ".[dev]"
pytest -q
python -m somaos.bench.runner --config somaos/bench/configs/smoke.json
python -m somaos.bench.report --in runs/ --out runs/report.md
```
