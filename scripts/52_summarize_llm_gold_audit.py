#!/usr/bin/env python3
"""Summarize LLM-assisted gold-audit results for manual review."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize LLM gold-audit results.")
    parser.add_argument(
        "--results",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_results.jsonl",
        help="LLM audit result JSONL.",
    )
    parser.add_argument(
        "--json-output",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_summary.json",
        help="Output JSON summary.",
    )
    parser.add_argument(
        "--md-output",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_summary.md",
        help="Output Markdown summary.",
    )
    parser.add_argument("--max-examples", type=int, default=80)
    return parser.parse_args()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _safe_ref(row: dict[str, Any]) -> str:
    ref = row.get("reference") if isinstance(row.get("reference"), dict) else {}
    return str(ref.get("citation_text") or ref.get("reference_text") or "")


def _span(row: dict[str, Any]) -> str:
    ref = row.get("reference") if isinstance(row.get("reference"), dict) else {}
    start = ref.get("start_char")
    end = ref.get("end_char")
    if start is None:
        return ""
    return f"{start}-{end}"


def main() -> None:
    args = parse_args()
    rows = _load_jsonl(args.results)

    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    by_issue: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    review_rows: list[dict[str, Any]] = []

    for row in rows:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        task_type = str(row.get("audit_task_type") or "unknown")
        decision = str(result.get("audit_decision") or "missing")
        issue = str(result.get("issue_type") or "missing")
        source = str(row.get("source") or "unknown")
        by_type[task_type][decision] += 1
        by_issue[issue] += 1
        by_source[source][decision] += 1
        if decision != "agree":
            review_rows.append(row)

    review_rows.sort(
        key=lambda row: (
            str(row.get("audit_task_type") or ""),
            str((row.get("result") or {}).get("audit_decision") or ""),
            str(row.get("document_id") or ""),
            _span(row),
        )
    )

    summary = {
        "result_count": len(rows),
        "decision_counts_by_type": {key: dict(value) for key, value in sorted(by_type.items())},
        "decision_counts_by_source": {key: dict(value) for key, value in sorted(by_source.items())},
        "issue_counts": dict(by_issue),
        "manual_review_count": len(review_rows),
        "manual_review_rows": review_rows,
    }

    json_out = Path(args.json_output)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = [
        "# LLM-Assisted Gold Consistency Audit",
        "",
        "This is an LLM-assisted consistency audit, not inter-annotator agreement.",
        "",
        f"- results: `{len(rows)}`",
        f"- rows needing manual review: `{len(review_rows)}`",
        "",
        "## Decisions by Task Type",
        "",
        "| Task type | Agree | Disagree | Uncertain | Missing |",
        "|---|---:|---:|---:|---:|",
    ]
    for task_type, counts in sorted(by_type.items()):
        lines.append(
            f"| `{task_type}` | {counts.get('agree', 0)} | {counts.get('disagree', 0)} | "
            f"{counts.get('uncertain', 0)} | {counts.get('missing', 0)} |"
        )

    lines.extend(["", "## Issue Counts", ""])
    for issue, count in by_issue.most_common():
        lines.append(f"- `{issue}`: `{count}`")

    lines.extend(["", "## Manual Review Queue", ""])
    for row in review_rows[: args.max_examples]:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        gold = row.get("current_gold") if isinstance(row.get("current_gold"), dict) else {}
        lines.extend(
            [
                f"### `{row.get('audit_task_id')}`",
                "",
                f"- type: `{row.get('audit_task_type')}`",
                f"- source/document: `{row.get('source')}` / `{row.get('document_id')}`",
                f"- span: `{_span(row)}`",
                f"- reference: `{_safe_ref(row)}`",
                f"- gold: `{json.dumps(gold, ensure_ascii=False)}`",
                f"- model decision: `{result.get('audit_decision')}`",
                f"- issue: `{result.get('issue_type')}`",
                f"- suggested classification: `{result.get('suggested_classification')}`",
                f"- suggested law id: `{result.get('suggested_law_id')}`",
                f"- suggested availability: `{result.get('suggested_target_availability')}`",
                f"- suggested target: `{result.get('suggested_target_document_id')}`",
                f"- confidence: `{result.get('confidence')}`",
                f"- evidence: {result.get('evidence')}",
                f"- manual review note: {result.get('manual_review_note')}",
                "",
            ]
        )

    md_out = Path(args.md_output)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- LLM GOLD AUDIT SUMMARY COMPLETE ---")
    print(f"Results:       {len(rows)}")
    print(f"Manual review: {len(review_rows)}")
    print(f"JSON output:   {args.json_output}")
    print(f"MD output:     {args.md_output}")


if __name__ == "__main__":
    main()
