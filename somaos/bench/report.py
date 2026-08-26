"""Report generator. See plans/wp/WP-09-report-gate.md.

Reads JSONL only -- never re-runs a policy (that would let a
report-generation bug quietly change the numbers it's reporting on).
No print() outside main().
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import json
from pathlib import Path

from somaos.bench.gate import evaluate_gates, phase0_verdict
from somaos.util.hashing import canonical_json


def load_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_all_results(in_dir: str | Path) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(str(Path(in_dir) / "results-*.jsonl"))):
        rows.extend(load_jsonl(path))
    return rows


def load_all_fast_path_ms(in_dir: str | Path) -> list[float]:
    samples: list[float] = []
    for path in sorted(glob.glob(str(Path(in_dir) / "fastpath-*.jsonl"))):
        for row in load_jsonl(path):
            samples.extend(row.get("ms_per_tick", []))
    return samples


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_headline_table(rows: list[dict]) -> list[dict]:
    """policy x budget, holdout only: strict_recall, tokens_per_query, competitive_ratio."""
    holdout = [r for r in rows if r["seed_split"] == "holdout"]
    groups: dict[tuple, list[dict]] = {}
    for r in holdout:
        groups.setdefault((r["policy"], r["budget_tokens"]), []).append(r)
    table = []
    for (policy, budget), group in sorted(groups.items()):
        crs = [g["competitive_ratio"] for g in group if g["competitive_ratio"] is not None]
        table.append({
            "policy": policy, "budget_tokens": budget,
            "strict_recall_mean": _mean([g["strict_recall"] for g in group]),
            "tokens_per_query_mean": _mean([g["tokens_per_query"] for g in group]),
            "competitive_ratio_mean": _mean(crs),
            "n": len(group),
        })
    return table


def build_per_regime_table(rows: list[dict]) -> list[dict]:
    holdout = [r for r in rows if r["seed_split"] == "holdout"]
    groups: dict[tuple, list[dict]] = {}
    for r in holdout:
        groups.setdefault((r["policy"], r["regime"]), []).append(r)
    table = []
    for (policy, regime), group in sorted(groups.items()):
        table.append({
            "policy": policy, "regime": regime,
            "strict_recall_mean": _mean([g["strict_recall"] for g in group]),
            "n": len(group),
        })
    return table


def build_tau_sensitivity_table(rows: list[dict]) -> list[dict]:
    holdout = [r for r in rows if r["seed_split"] == "holdout" and r["policy"] == "S"]
    groups: dict[int, list[dict]] = {}
    for r in holdout:
        groups.setdefault(r["tau_ticks"], []).append(r)
    table = []
    for tau, group in sorted(groups.items()):
        table.append({"tau_ticks": tau, "strict_recall_mean": _mean([g["strict_recall"] for g in group]), "n": len(group)})
    return table


def build_dev_table(rows: list[dict]) -> list[dict]:
    dev = [r for r in rows if r["seed_split"] == "dev"]
    groups: dict[tuple, list[dict]] = {}
    for r in dev:
        groups.setdefault((r["policy"], r["regime"]), []).append(r)
    table = []
    for (policy, regime), group in sorted(groups.items()):
        table.append({
            "policy": policy, "regime": regime,
            "strict_recall_mean": _mean([g["strict_recall"] for g in group]),
            "n": len(group),
        })
    return table


def build_report(rows: list[dict], fast_path_ms: list[float], cfg: dict) -> dict:
    gates, warnings = evaluate_gates(rows, fast_path_ms, cfg)
    verdict = phase0_verdict(gates)

    static_warnings = [
        "B4 is a cost-model proxy for LLM-managed paging, NOT a MemGPT/Letta "
        "reimplementation -- Phase 0 has no LLM (D-01). See "
        "plans/00_PHASE0_MASTER_PLAN.md #3.4.",
        "competitive_ratio outside regime=uniform is computed against an "
        "upper-bound oracle (D-09), not a proven-optimal one -- treat it as "
        "understated, never overstated.",
        "B2's similarity uses ground-truth topic/entity tags directly (no "
        "embedding model exists in Phase 0), making it a stronger baseline "
        "than a real vector-RAG system would be.",
    ]

    return {
        "verdict": verdict,
        "gates": [dataclasses.asdict(g) for g in gates],
        "warnings": [dataclasses.asdict(w) for w in warnings]
        + [{"code": "STATIC", "detail": d} for d in static_warnings],
        "tables": {
            "headline": build_headline_table(rows),
            "per_regime": build_per_regime_table(rows),
            "tau_sensitivity": build_tau_sensitivity_table(rows),
            "dev": build_dev_table(rows),
        },
        "provenance": {
            "n_rows": len(rows),
            "trace_ids": sorted({r["trace_id"] for r in rows}),
            "config_hashes": sorted({r["config_hash"] for r in rows}),
        },
    }


def render_markdown(report: dict) -> str:
    lines = [f"PHASE0 GATE: {report['verdict']}", ""]
    for g in report["gates"]:
        status = "PASS" if g["passed"] else "FAIL"
        val = f"{g['value']:.4f}" if g["value"] is not None else "N/A"
        lines.append(f"  {g['id']} ... {status}  value={val} threshold={g['threshold']}  {g['detail']}")
    lines.append("")
    lines.append("[WARNINGS]")
    for w in report["warnings"]:
        lines.append(f"  - ({w['code']}) {w['detail']}")
    lines.append("")

    lines.append("## 1. Headline table (holdout only)")
    lines.append("| policy | budget_tokens | strict_recall | tokens/query | competitive_ratio | n |")
    lines.append("|---|---|---|---|---|---|")
    for row in report["tables"]["headline"]:
        cr = f"{row['competitive_ratio_mean']:.4f}" if row["competitive_ratio_mean"] is not None else "N/A"
        lines.append(
            f"| {row['policy']} | {row['budget_tokens']} | {row['strict_recall_mean']:.4f} | "
            f"{row['tokens_per_query_mean']:.1f} | {cr} | {row['n']} |"
        )
    lines.append("")

    lines.append("## 2. Per-regime table (holdout only)")
    lines.append("| policy | regime | strict_recall | n |")
    lines.append("|---|---|---|---|")
    for row in report["tables"]["per_regime"]:
        lines.append(f"| {row['policy']} | {row['regime']} | {row['strict_recall_mean']:.4f} | {row['n']} |")
    lines.append("")

    lines.append("## 3. tau_ticks sensitivity (policy S, holdout only)")
    lines.append("| tau_ticks | strict_recall | n |")
    lines.append("|---|---|---|")
    for row in report["tables"]["tau_sensitivity"]:
        lines.append(f"| {row['tau_ticks']} | {row['strict_recall_mean']:.4f} | {row['n']} |")
    lines.append("")

    lines.append("## 4. Dev-set table (NOT used to decide gates)")
    lines.append("| policy | regime | strict_recall | n |")
    lines.append("|---|---|---|---|")
    for row in report["tables"]["dev"]:
        lines.append(f"| {row['policy']} | {row['regime']} | {row['strict_recall_mean']:.4f} | {row['n']} |")
    lines.append("")

    lines.append("## 5. Reproduction")
    lines.append(f"- rows: {report['provenance']['n_rows']}")
    lines.append(f"- trace_ids: {', '.join(report['provenance']['trace_ids'][:5])}"
                  + (" ..." if len(report["provenance"]["trace_ids"]) > 5 else ""))
    lines.append(f"- config_hashes: {', '.join(report['provenance']['config_hashes'])}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SomaOS Phase 0 report generator")
    parser.add_argument("--in", dest="in_dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    rows = load_all_results(args.in_dir)
    fast_path_ms = load_all_fast_path_ms(args.in_dir)

    config_paths = sorted(glob.glob(str(Path(args.in_dir) / "config-*.json")))
    cfg = json.loads(Path(config_paths[-1]).read_text()) if config_paths else {}

    report = build_report(rows, fast_path_ms, cfg)
    markdown = render_markdown(report)

    out_path = Path(args.out)
    out_path.write_text(markdown, encoding="utf-8")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(canonical_json(report), encoding="utf-8")

    print(f"wrote {out_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
