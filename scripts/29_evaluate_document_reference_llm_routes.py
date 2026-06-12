#!/usr/bin/env python3
"""
Evaluate route-based LLM fallback decisions against a small reviewed gold slice.

The reviewed gold rows are keyed by `entry_id` and represent bounded fallback tasks:
- link_disambiguation
- link_normalization_or_target_recovery
- extraction_presence_check

This evaluator reports:
- overall decision accuracy
- per-route decision accuracy
- target-id accuracy for exact-target decisions
- reference-type accuracy for extraction-presence decisions
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate document-reference LLM fallback results against reviewed gold."
    )
    parser.add_argument(
        "--predicted",
        required=True,
        help="Predicted JSONL produced by scripts/28_run_document_reference_llm_pilot.py.",
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Reviewed route-gold JSONL file.",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Optional JSON evaluation output.",
    )
    parser.add_argument(
        "--md-output",
        default=None,
        help="Optional Markdown evaluation output.",
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


def _safe_div(num: int, den: int) -> float:
    return num / den if den else 0.0


def main() -> None:
    args = parse_args()

    predicted_rows = _load_jsonl(args.predicted)
    gold_rows = _load_jsonl(args.gold)

    predicted_by_id = {str(row["entry_id"]): row for row in predicted_rows}
    gold_by_id = {str(row["entry_id"]): row for row in gold_rows}

    missing_predictions: list[dict] = []
    route_counts = Counter()
    route_correct = Counter()
    route_target_counts = Counter()
    route_target_correct = Counter()
    route_ref_type_counts = Counter()
    route_ref_type_correct = Counter()
    mismatches: list[dict] = []

    total_gold = len(gold_rows)
    decision_correct = 0
    fully_correct = 0

    for entry_id, gold in gold_by_id.items():
        route = str(gold.get("llm_route") or "unknown")
        route_counts[route] += 1

        predicted = predicted_by_id.get(entry_id)
        if predicted is None:
            missing_predictions.append(
                {
                    "entry_id": entry_id,
                    "llm_route": route,
                    "expected_decision": gold.get("expected_decision"),
                }
            )
            continue

        result = predicted.get("result") or {}
        predicted_decision = result.get("decision")
        expected_decision = gold.get("expected_decision")

        decision_match = predicted_decision == expected_decision
        if decision_match:
            decision_correct += 1
            route_correct[route] += 1
        else:
            mismatches.append(
                {
                    "entry_id": entry_id,
                    "llm_route": route,
                    "expected_decision": expected_decision,
                    "predicted_decision": predicted_decision,
                    "expected_target_document_id": gold.get("expected_target_document_id"),
                    "predicted_target_document_id": result.get("target_document_id"),
                    "expected_reference_type": gold.get("expected_reference_type"),
                    "predicted_reference_type": result.get("reference_type"),
                }
            )

        fully_correct_here = decision_match

        if expected_decision == "exact_target":
            route_target_counts[route] += 1
            if (
                decision_match
                and result.get("target_document_id") == gold.get("expected_target_document_id")
            ):
                route_target_correct[route] += 1
            else:
                fully_correct_here = False
            if decision_match and result.get("target_document_id") != gold.get("expected_target_document_id"):
                mismatches.append(
                    {
                        "entry_id": entry_id,
                        "llm_route": route,
                        "expected_decision": expected_decision,
                        "predicted_decision": predicted_decision,
                        "expected_target_document_id": gold.get("expected_target_document_id"),
                        "predicted_target_document_id": result.get("target_document_id"),
                        "expected_reference_type": gold.get("expected_reference_type"),
                        "predicted_reference_type": result.get("reference_type"),
                    }
                )

        if expected_decision == "is_reference":
            route_ref_type_counts[route] += 1
            if (
                decision_match
                and (
                    not gold.get("expected_reference_type")
                    or result.get("reference_type") == gold.get("expected_reference_type")
                )
            ):
                route_ref_type_correct[route] += 1
            else:
                fully_correct_here = False
            if decision_match and (
                gold.get("expected_reference_type")
                and result.get("reference_type") != gold.get("expected_reference_type")
            ):
                mismatches.append(
                    {
                        "entry_id": entry_id,
                        "llm_route": route,
                        "expected_decision": expected_decision,
                        "predicted_decision": predicted_decision,
                        "expected_target_document_id": gold.get("expected_target_document_id"),
                        "predicted_target_document_id": result.get("target_document_id"),
                        "expected_reference_type": gold.get("expected_reference_type"),
                        "predicted_reference_type": result.get("reference_type"),
                    }
                )

        if fully_correct_here:
            fully_correct += 1

    overall_accuracy = _safe_div(decision_correct, total_gold)
    full_accuracy = _safe_div(fully_correct, total_gold)

    per_route: dict[str, dict] = {}
    for route in sorted(route_counts):
        decision_total = route_counts[route]
        target_total = route_target_counts[route]
        ref_total = route_ref_type_counts[route]
        per_route[route] = {
            "gold_rows": decision_total,
            "decision_correct": route_correct[route],
            "decision_accuracy": _safe_div(route_correct[route], decision_total),
            "target_rows": target_total,
            "target_correct": route_target_correct[route],
            "target_accuracy": _safe_div(route_target_correct[route], target_total),
            "reference_type_rows": ref_total,
            "reference_type_correct": route_ref_type_correct[route],
            "reference_type_accuracy": _safe_div(route_ref_type_correct[route], ref_total),
        }

    result = {
        "predicted_path": args.predicted,
        "gold_path": args.gold,
        "gold_row_count": total_gold,
        "predicted_row_count": len(predicted_rows),
        "decision_correct_count": decision_correct,
        "decision_accuracy": overall_accuracy,
        "fully_correct_count": fully_correct,
        "full_accuracy": full_accuracy,
        "missing_prediction_count": len(missing_predictions),
        "mismatch_count": len(mismatches),
        "per_route": per_route,
        "missing_predictions": missing_predictions[:100],
        "mismatches": mismatches[:100],
    }

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.md_output:
        md_path = Path(args.md_output)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Document Reference LLM Route Evaluation",
            "",
            f"- predicted: `{args.predicted}`",
            f"- gold rows: `{total_gold}`",
            f"- predicted rows: `{len(predicted_rows)}`",
            f"- correct decisions: `{decision_correct}`",
            f"- decision accuracy: `{overall_accuracy:.3f}`",
            f"- fully correct rows: `{fully_correct}`",
            f"- full accuracy: `{full_accuracy:.3f}`",
            f"- missing predictions: `{len(missing_predictions)}`",
            f"- mismatches: `{len(mismatches)}`",
            "",
            "## Per Route",
            "",
        ]
        for route, stats in per_route.items():
            lines.append(f"### `{route}`")
            lines.append("")
            lines.append(f"- gold rows: `{stats['gold_rows']}`")
            lines.append(f"- decision correct: `{stats['decision_correct']}`")
            lines.append(f"- decision accuracy: `{stats['decision_accuracy']:.3f}`")
            if stats["target_rows"]:
                lines.append(f"- target accuracy: `{stats['target_accuracy']:.3f}`")
            if stats["reference_type_rows"]:
                lines.append(
                    f"- reference-type accuracy: `{stats['reference_type_accuracy']:.3f}`"
                )
            lines.append("")
        if missing_predictions:
            lines.extend(["## Missing Predictions", ""])
            for row in missing_predictions[:20]:
                lines.append(
                    f"- `{row['entry_id']}` expected `{row['expected_decision']}`"
                )
            lines.append("")
        if mismatches:
            lines.extend(["## Mismatches", ""])
            for row in mismatches[:20]:
                lines.append(
                    f"- `{row['entry_id']}` expected decision `{row['expected_decision']}` / target `{row.get('expected_target_document_id')}` / ref type `{row.get('expected_reference_type')}`; predicted decision `{row['predicted_decision']}` / target `{row.get('predicted_target_document_id')}` / ref type `{row.get('predicted_reference_type')}`"
                )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LLM ROUTE EVALUATION COMPLETE ---")
    print(f"Gold rows:              {total_gold}")
    print(f"Predicted rows:         {len(predicted_rows)}")
    print(f"Correct decisions:      {decision_correct}")
    print(f"Decision accuracy:      {overall_accuracy:.3f}")
    print(f"Fully correct rows:     {fully_correct}")
    print(f"Full accuracy:          {full_accuracy:.3f}")
    print(f"Missing predictions:    {len(missing_predictions)}")
    print(f"Mismatches:             {len(mismatches)}")
    if args.json_output:
        print(f"JSON output:            {args.json_output}")
    if args.md_output:
        print(f"Markdown output:        {args.md_output}")


if __name__ == "__main__":
    main()
