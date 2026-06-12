#!/usr/bin/env python3
"""
Evaluate document-reference linking against a reviewed availability benchmark.

Unlike the positive-only exact-link evaluator, this script scores the full
reviewed document-reference set, including:
- exact in-corpus targets
- same-proceeding-only targets
- references reviewed as not present in the corpus
- references whose target remains unknown / insufficient for linking
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate document-reference predictions against reviewed availability gold."
    )
    parser.add_argument(
        "--predicted",
        required=True,
        help="Predicted link JSONL.",
    )
    parser.add_argument(
        "--reviewed",
        required=True,
        help="Reviewed availability JSONL.",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--md-output",
        default=None,
        help="Optional Markdown output path.",
    )
    return parser.parse_args()


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _occurrence_key(row: dict[str, Any]) -> tuple[str, int]:
    document_id = row.get("source_document_id", row.get("document_id"))
    raw_start = row.get("raw_start", row.get("start_char"))
    if document_id is None or raw_start is None:
        raise KeyError("row is missing occurrence-key fields")
    return (str(document_id), int(raw_start))


def _predicted_status(predicted_rows: list[dict[str, Any]]) -> str:
    return "linked" if predicted_rows else "unresolved"


def _classify_review_row(review_row: dict[str, Any], predicted_rows: list[dict[str, Any]]) -> str:
    availability = str(review_row.get("target_availability_gold") or "unknown")
    gold_document_id = review_row.get("target_document_id_gold")
    predicted_targets = {row.get("target_document_id") for row in predicted_rows if row.get("target_document_id")}
    predicted_scopes = {str(row.get("target_match_scope") or "") for row in predicted_rows}

    if availability == "exact_in_corpus":
        if (
            gold_document_id
            and predicted_targets == {gold_document_id}
            and predicted_scopes == {"exact_decision"}
        ):
            return "exact_correct"
        if predicted_rows:
            return "exact_wrong_or_extra"
        return "exact_missed"

    if availability == "same_proceeding_only":
        if (
            gold_document_id
            and predicted_targets == {gold_document_id}
            and predicted_scopes == {"same_proceeding"}
        ):
            return "same_proceeding_correct"
        if predicted_rows:
            return "same_proceeding_wrong_or_extra"
        return "same_proceeding_missed"

    if availability == "not_in_corpus":
        return "not_in_corpus_overlinked" if predicted_rows else "not_in_corpus_correctly_unresolved"

    return "unknown_linked" if predicted_rows else "unknown_unresolved"


def _example_row(review_row: dict[str, Any], predicted_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source_document_id": review_row.get("source_document_id", review_row.get("document_id")),
        "raw_start": int(review_row.get("raw_start", review_row.get("start_char"))),
        "reference_text": review_row.get("reference_text"),
        "availability_gold": review_row.get("target_availability_gold"),
        "gold_target_document_id": review_row.get("target_document_id_gold"),
        "predicted_targets": [
            {
                "target_document_id": row.get("target_document_id"),
                "target_match_scope": row.get("target_match_scope"),
                "link_method": row.get("link_method"),
            }
            for row in predicted_rows
        ],
        "review_note": review_row.get("review_note"),
    }


def main() -> None:
    args = parse_args()

    predicted_rows = _load_jsonl(args.predicted)
    review_rows = _load_jsonl(args.reviewed)

    predicted_by_occurrence: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in predicted_rows:
        predicted_by_occurrence[_occurrence_key(row)].append(row)

    outcome_counts: Counter[str] = Counter()
    availability_counts: Counter[str] = Counter()
    source_outcomes: Counter[tuple[str, str]] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for review_row in review_rows:
        key = _occurrence_key(review_row)
        predicted_for_occurrence = predicted_by_occurrence.get(key, [])
        availability = str(review_row.get("target_availability_gold") or "unknown")
        outcome = _classify_review_row(review_row, predicted_for_occurrence)

        availability_counts[availability] += 1
        outcome_counts[outcome] += 1
        source_outcomes[(str(review_row.get("source") or "unknown"), outcome)] += 1
        if len(examples[outcome]) < 10:
            examples[outcome].append(_example_row(review_row, predicted_for_occurrence))

    exact_correct = outcome_counts["exact_correct"]
    exact_total = availability_counts["exact_in_corpus"]
    same_correct = outcome_counts["same_proceeding_correct"]
    same_total = availability_counts["same_proceeding_only"]
    unavailable_total = availability_counts["not_in_corpus"] + availability_counts["unknown"]
    correctly_unresolved_unavailable = (
        outcome_counts["not_in_corpus_correctly_unresolved"] + outcome_counts["unknown_unresolved"]
    )

    result = {
        "predicted_path": str(Path(args.predicted).resolve()),
        "reviewed_path": str(Path(args.reviewed).resolve()),
        "reviewed_row_count": len(review_rows),
        "availability_counts": dict(sorted(availability_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "source_outcomes": {
            f"{source}::{outcome}": count
            for (source, outcome), count in sorted(source_outcomes.items())
        },
        "exact_in_corpus_accuracy": exact_correct / exact_total if exact_total else 0.0,
        "same_proceeding_accuracy": same_correct / same_total if same_total else 0.0,
        "unavailable_correctly_unresolved_share": (
            correctly_unresolved_unavailable / unavailable_total if unavailable_total else 0.0
        ),
        "examples": {key: value for key, value in sorted(examples.items())},
    }

    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.md_output:
        output_path = Path(args.md_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Document Reference Link Availability Evaluation",
            "",
            f"- predicted: `{result['predicted_path']}`",
            f"- reviewed: `{result['reviewed_path']}`",
            f"- reviewed rows: `{result['reviewed_row_count']}`",
            "",
            "## Availability Counts",
            "",
        ]
        for key, value in sorted(availability_counts.items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.extend(
            [
                "",
                "## Outcome Counts",
                "",
            ]
        )
        for key, value in sorted(outcome_counts.items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.extend(
            [
                "",
                "## Headline Rates",
                "",
                f"- exact in-corpus accuracy: `{result['exact_in_corpus_accuracy']:.4f}`",
                f"- same-proceeding accuracy: `{result['same_proceeding_accuracy']:.4f}`",
                f"- unavailable correctly unresolved share: `{result['unavailable_correctly_unresolved_share']:.4f}`",
                "",
                "## By Source",
                "",
            ]
        )
        for key, value in sorted(result["source_outcomes"].items()):
            lines.append(f"- `{key}`: `{value}`")
        for key, rows in sorted(examples.items()):
            if not rows:
                continue
            lines.extend(
                [
                    "",
                    f"## Examples: {key}",
                    "",
                ]
            )
            for row in rows[:5]:
                predicted_summary = ", ".join(
                    f"{item.get('target_document_id')} ({item.get('target_match_scope')})"
                    for item in row["predicted_targets"]
                ) or "none"
                lines.append(
                    f"- `{row['source_document_id']}@{row['raw_start']}` `{row['reference_text']}` | "
                    f"gold=`{row['availability_gold']}` target=`{row['gold_target_document_id']}` | "
                    f"predicted=`{predicted_summary}`"
                )
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LINK AVAILABILITY EVALUATION COMPLETE ---")
    print(f"Reviewed rows:                    {len(review_rows)}")
    print(f"Exact in-corpus rows:             {exact_total}")
    print(f"Same-proceeding-only rows:        {same_total}")
    print(f"Exact in-corpus correct:          {exact_correct}")
    print(f"Same-proceeding correct:          {same_correct}")
    print(
        "Unavailable correctly unresolved: "
        f"{correctly_unresolved_unavailable}/{unavailable_total}"
    )
    if args.json_output:
        print(f"JSON output:                      {args.json_output}")
    if args.md_output:
        print(f"Markdown output:                  {args.md_output}")


if __name__ == "__main__":
    main()
