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
