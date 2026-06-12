#!/usr/bin/env python3
"""
Compare deterministic-only route handling against LLM fallback on a reviewed route benchmark.

The deterministic baseline is intentionally simple:
- link_disambiguation -> unresolved
- link_normalization_or_target_recovery -> unresolved
- extraction_presence_check -> not_reference

This lets us quantify what the LLM adds on the routed hard-case subset.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare deterministic-only and LLM route handling on reviewed gold."
    )
    parser.add_argument(
        "--input-benchmark",
        required=True,
        help="Route benchmark input JSONL used for the LLM runner.",
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Reviewed route-gold JSONL.",
    )
    parser.add_argument(
        "--llm-predicted",
        required=True,
        help="LLM prediction JSONL from scripts/28_run_document_reference_llm_pilot.py.",
    )
    parser.add_argument(
        "--tag",
        default="current",
        help="Tag for output file names under data/annotations/document_reference_links/eval/.",
    )
    return parser.parse_args()


def _load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_baseline_predictions(input_rows: list[dict]) -> list[dict]:
    baseline: list[dict] = []
    for row in input_rows:
        route = row["llm_route"]
        if route in {"link_disambiguation", "link_normalization_or_target_recovery"}:
            result = {
                "decision": "unresolved",
                "target_document_id": None,
                "confidence": 1.0,
                "rationale": "deterministic_baseline_no_llm",
            }
        elif route == "extraction_presence_check":
            result = {
                "decision": "not_reference",
                "reference_type": "unknown",
                "normalized_body": "",
                "confidence": 1.0,
                "rationale": "deterministic_baseline_no_llm",
            }
        else:
            raise ValueError(f"Unsupported route: {route}")

        baseline.append(
            {
                "entry_id": row["entry_id"],
                "llm_route": route,
                "source": row.get("source"),
                "document_id": row.get("document_id") or row.get("source_document_id"),
                "document_path": row.get("document_path") or row.get("source_document_path"),
                "prompt": None,
                "result": result,
                "expected_outcome": row.get("expected_outcome"),
                "timestamp_unix": None,
                "model": "deterministic_baseline_no_llm",
                "prompt_version": None,
            }
        )
    return baseline


def _run_eval(predicted: Path, gold: Path, json_output: Path, md_output: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "29_evaluate_document_reference_llm_routes.py"),
        "--predicted",
        str(predicted),
        "--gold",
        str(gold),
        "--json-output",
        str(json_output),
        "--md-output",
        str(md_output),
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    args = parse_args()

    eval_dir = PROJECT_ROOT / "data" / "annotations" / "document_reference_links" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    benchmark_rows = _load_jsonl(args.input_benchmark)
    baseline_rows = _build_baseline_predictions(benchmark_rows)

    baseline_predicted = eval_dir / f"document_reference_llm_route_{args.tag}.baseline.jsonl"
    baseline_json = eval_dir / f"document_reference_llm_route_{args.tag}.baseline_eval.json"
    baseline_md = eval_dir / f"document_reference_llm_route_{args.tag}.baseline_eval.md"
    llm_json = eval_dir / f"document_reference_llm_route_{args.tag}.llm_eval.json"
    llm_md = eval_dir / f"document_reference_llm_route_{args.tag}.llm_eval.md"
    compare_json = eval_dir / f"document_reference_llm_route_{args.tag}.comparison.json"
    compare_md = eval_dir / f"document_reference_llm_route_{args.tag}.comparison.md"

    _write_jsonl(baseline_predicted, baseline_rows)
    _run_eval(baseline_predicted, Path(args.gold), baseline_json, baseline_md)
    _run_eval(Path(args.llm_predicted), Path(args.gold), llm_json, llm_md)

    baseline_report = json.loads(baseline_json.read_text(encoding="utf-8"))
    llm_report = json.loads(llm_json.read_text(encoding="utf-8"))

    compare = {
        "input_benchmark": args.input_benchmark,
        "gold": args.gold,
        "baseline_predicted": str(baseline_predicted),
        "llm_predicted": args.llm_predicted,
        "baseline": baseline_report,
        "llm": llm_report,
        "delta": {
            "decision_accuracy": llm_report["decision_accuracy"] - baseline_report["decision_accuracy"],
            "full_accuracy": llm_report["full_accuracy"] - baseline_report["full_accuracy"],
        },
        "per_route_delta": {},
    }

    all_routes = set(baseline_report["per_route"]) | set(llm_report["per_route"])
    for route in sorted(all_routes):
        before = baseline_report["per_route"].get(route, {})
        after = llm_report["per_route"].get(route, {})
        compare["per_route_delta"][route] = {
            "decision_accuracy": after.get("decision_accuracy", 0.0) - before.get("decision_accuracy", 0.0),
            "target_accuracy": after.get("target_accuracy", 0.0) - before.get("target_accuracy", 0.0),
            "reference_type_accuracy": after.get("reference_type_accuracy", 0.0) - before.get("reference_type_accuracy", 0.0),
        }

    compare_json.write_text(json.dumps(compare, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Document Reference LLM Route Comparison",
        "",
        f"- benchmark input: `{args.input_benchmark}`",
        f"- gold: `{args.gold}`",
        f"- deterministic baseline: `{baseline_predicted}`",
        f"- llm predictions: `{args.llm_predicted}`",
        "",
        "## Overall",
        "",
        f"- deterministic decision accuracy: `{baseline_report['decision_accuracy']:.3f}`",
        f"- llm decision accuracy: `{llm_report['decision_accuracy']:.3f}`",
        f"- decision-accuracy delta: `{compare['delta']['decision_accuracy']:+.3f}`",
        f"- deterministic full accuracy: `{baseline_report['full_accuracy']:.3f}`",
        f"- llm full accuracy: `{llm_report['full_accuracy']:.3f}`",
        f"- full-accuracy delta: `{compare['delta']['full_accuracy']:+.3f}`",
        "",
        "## Per Route",
        "",
    ]
    for route in sorted(all_routes):
        before = baseline_report["per_route"].get(route, {})
        after = llm_report["per_route"].get(route, {})
        lines.append(f"### `{route}`")
        lines.append("")
        lines.append(f"- deterministic decision accuracy: `{before.get('decision_accuracy', 0.0):.3f}`")
        lines.append(f"- llm decision accuracy: `{after.get('decision_accuracy', 0.0):.3f}`")
        lines.append(
            f"- decision-accuracy delta: `{compare['per_route_delta'][route]['decision_accuracy']:+.3f}`"
        )
        if after.get("target_rows") or before.get("target_rows"):
            lines.append(f"- deterministic target accuracy: `{before.get('target_accuracy', 0.0):.3f}`")
            lines.append(f"- llm target accuracy: `{after.get('target_accuracy', 0.0):.3f}`")
        if after.get("reference_type_rows") or before.get("reference_type_rows"):
            lines.append(
                f"- deterministic reference-type accuracy: `{before.get('reference_type_accuracy', 0.0):.3f}`"
            )
            lines.append(
                f"- llm reference-type accuracy: `{after.get('reference_type_accuracy', 0.0):.3f}`"
            )
        lines.append("")
    compare_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LLM ROUTE COMPARISON COMPLETE ---")
    print(f"Baseline predicted:  {baseline_predicted}")
    print(f"Baseline eval:       {baseline_md}")
    print(f"LLM eval:            {llm_md}")
    print(f"Comparison report:   {compare_md}")


if __name__ == "__main__":
    main()
