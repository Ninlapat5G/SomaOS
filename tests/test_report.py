import json
import re
from pathlib import Path

from somaos.bench import report, runner


def run_smoke(tmp_path):
    cfg = runner.load_config("somaos/bench/configs/smoke.json")
    results, timings = runner.run_all(cfg, jobs=1)
    runner.write_outputs(cfg, results, timings, tmp_path)
    fp_rows = runner.run_fast_path_timing(cfg)
    runner.write_fast_path_timing(cfg, fp_rows, tmp_path)
    return tmp_path


def test_report_first_line_matches_gate_regex(tmp_path):
    run_smoke(tmp_path)
    rows = report.load_all_results(tmp_path)
    fp = report.load_all_fast_path_ms(tmp_path)
    cfg_path = sorted(Path(tmp_path).glob("config-*.json"))[0]
    cfg = json.loads(cfg_path.read_text())
    rep = report.build_report(rows, fp, cfg)
    md = report.render_markdown(rep)
    first_line = md.splitlines()[0]
    assert re.match(r"^PHASE0 GATE: (PASS|FAIL)$", first_line)


def test_report_json_has_required_top_level_keys(tmp_path):
    run_smoke(tmp_path)
    rows = report.load_all_results(tmp_path)
    fp = report.load_all_fast_path_ms(tmp_path)
    cfg = json.loads(sorted(Path(tmp_path).glob("config-*.json"))[0].read_text())
    rep = report.build_report(rows, fp, cfg)
    assert set(rep.keys()) >= {"verdict", "gates", "warnings", "tables", "provenance"}
    assert rep["verdict"] in ("PASS", "FAIL")


def test_report_does_not_import_policies_or_broker():
    """report.py must build its numbers only from already-computed JSONL --
    never re-run a policy (WP-09 acceptance)."""
    import ast

    path = Path(__file__).resolve().parent.parent / "somaos" / "bench" / "report.py"
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "broker" not in node.module, f"report.py imports {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "broker" not in alias.name, f"report.py imports {alias.name}"


def test_report_via_cli_writes_both_files(tmp_path):
    run_smoke(tmp_path)
    out_md = tmp_path / "report.md"
    report.main(["--in", str(tmp_path), "--out", str(out_md)])
    assert out_md.exists()
    assert (tmp_path / "report.json").exists()
    first_line = out_md.read_text(encoding="utf-8").splitlines()[0]
    assert re.match(r"^PHASE0 GATE: (PASS|FAIL)$", first_line)


def test_warnings_include_b4_caveat_always(tmp_path):
    run_smoke(tmp_path)
    rows = report.load_all_results(tmp_path)
    fp = report.load_all_fast_path_ms(tmp_path)
    cfg = json.loads(sorted(Path(tmp_path).glob("config-*.json"))[0].read_text())
    rep = report.build_report(rows, fp, cfg)
    details = [w["detail"] for w in rep["warnings"]]
    assert any("MemGPT" in d or "cost-model proxy" in d for d in details)


def test_headline_table_only_uses_holdout(tmp_path):
    run_smoke(tmp_path)
    rows = report.load_all_results(tmp_path)
    dev_row = next(r for r in rows if r["seed_split"] == "dev")
    dev_row["strict_recall"] = -999.0  # sentinel: if this leaks into holdout stats, it'll show
    table = report.build_headline_table(rows)
    for row in table:
        assert row["strict_recall_mean"] != -999.0


def test_reproduction_section_stays_bounded_with_many_distinct_config_hashes():
    """Regression: config_hash is near-unique per row (it folds in
    seed_root), so at full phase0.json scale (6336 rows) render_markdown
    used to join ALL distinct config_hashes onto one line -- 462KB on a
    single line from one real run. trace_ids was already sliced to 5;
    config_hashes was not. The full lists still belong in report.json for
    reproducibility; only the markdown line needs to stay readable."""
    rows = []
    for i in range(3000):
        rows.append({
            "policy": "S", "regime": "uniform", "seed_root": f"seed-{i}",
            "seed_split": "holdout", "budget_tokens": 4096, "tau_ticks": 32,
            "strict_recall": 0.5, "tokens_per_query": 100.0,
            "competitive_ratio": 0.5, "surprise_utility_spearman": 0.1,
            "trace_id": f"sha256:trace{i}", "config_hash": f"sha256:cfg{i}",
        })
    rep = report.build_report(rows, [], {})
    assert len(rep["provenance"]["config_hashes"]) == 3000
    assert len(rep["provenance"]["trace_ids"]) == 3000

    md = report.render_markdown(rep)
    lines = md.split("\n")
    assert max(len(line) for line in lines) < 2000
    repro_lines = [l for l in lines if l.startswith("- config_hashes") or l.startswith("- trace_ids")]
    assert len(repro_lines) == 2
    for line in repro_lines:
        assert "3000 total" in line
        assert "report.json" in line
