#!/usr/bin/env python3
"""
Run the reviewed document-reference LLM route benchmark in one of three modes:

- deterministic: score the deterministic no-LLM baseline
- llm: run the LLM fallback and score it
- compare: produce both deterministic and LLM reports plus a comparison
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
        description="Run document-reference LLM route benchmark in deterministic, llm, or compare mode."
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "llm", "compare"],
        required=True,
        help="Which mode to run.",
    )
    parser.add_argument(
        "--input-benchmark",
        required=True,
        help="Route benchmark input JSONL.",
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Reviewed route-gold JSONL.",
    )
    parser.add_argument(
        "--tag",
        default="current",
        help="Tag for output file names.",
    )
    parser.add_argument(
        "--prompt-version",
        choices=["v1", "v2"],
        default="v2",
        help="Prompt version for llm mode.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model name for llm mode.",
    )
    parser.add_argument(
        "--llm-predicted",
        default=None,
        help="Existing LLM prediction JSONL to reuse in compare mode.",
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=30000)
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


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    args = parse_args()

    eval_dir = PROJECT_ROOT / "data" / "annotations" / "document_reference_links" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "deterministic":
        input_rows = _load_jsonl(args.input_benchmark)
        predicted = eval_dir / f"document_reference_llm_route_{args.tag}.deterministic.jsonl"
        json_eval = eval_dir / f"document_reference_llm_route_{args.tag}.deterministic_eval.json"
        md_eval = eval_dir / f"document_reference_llm_route_{args.tag}.deterministic_eval.md"
        _write_jsonl(predicted, _build_baseline_predictions(input_rows))
        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "29_evaluate_document_reference_llm_routes.py"),
                "--predicted",
                str(predicted),
                "--gold",
                args.gold,
                "--json-output",
                str(json_eval),
                "--md-output",
                str(md_eval),
            ]
        )
        print("--- DOCUMENT REFERENCE ROUTE MODE COMPLETE ---")
        print(f"Mode:              deterministic")
        print(f"Predicted output:  {predicted}")
        print(f"Evaluation:        {md_eval}")
        return

    if args.mode == "llm":
        predicted = eval_dir / f"document_reference_llm_route_{args.tag}.llm.jsonl"
        json_eval = eval_dir / f"document_reference_llm_route_{args.tag}.llm_eval.json"
        md_eval = eval_dir / f"document_reference_llm_route_{args.tag}.llm_eval.md"
        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "28_run_document_reference_llm_pilot.py"),
                "--input",
                args.input_benchmark,
                "--output",
                str(predicted),
                "--prompt-version",
                args.prompt_version,
                "--model",
                args.model,
                "--retries",
                str(args.retries),
                "--timeout-ms",
                str(args.timeout_ms),
            ]
        )
        _run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "29_evaluate_document_reference_llm_routes.py"),
                "--predicted",
                str(predicted),
                "--gold",
                args.gold,
                "--json-output",
                str(json_eval),
                "--md-output",
                str(md_eval),
            ]
        )
        print("--- DOCUMENT REFERENCE ROUTE MODE COMPLETE ---")
        print(f"Mode:              llm")
        print(f"Predicted output:  {predicted}")
        print(f"Evaluation:        {md_eval}")
        return

    llm_predicted = args.llm_predicted
    if llm_predicted is None:
        llm_predicted = str(
            eval_dir / f"document_reference_llm_route_{args.tag}.llm.jsonl"
        )
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "31_compare_document_reference_llm_routes.py"),
            "--input-benchmark",
            args.input_benchmark,
            "--gold",
            args.gold,
            "--llm-predicted",
            llm_predicted,
            "--tag",
            args.tag,
        ]
    )
    print("--- DOCUMENT REFERENCE ROUTE MODE COMPLETE ---")
    print("Mode:              compare")
    print(f"Comparison tag:    {args.tag}")


if __name__ == "__main__":
    raise SystemExit(main())
