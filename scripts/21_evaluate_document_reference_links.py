#!/usr/bin/env python3
"""
Evaluate predicted document-reference links against reviewed link-gold JSONL files.

This evaluator is intentionally conservative:
- exact link match key: (source_document_id, raw_start, target_document_id)
- scoped occurrence key: (source_document_id, raw_start)

The reviewed gold slices are positive-only, so this script reports:
- exact recall on the gold links
- scoped precision/recall within the reviewed occurrences
- wrong-target and missing-link counts
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate predicted document-reference links against reviewed gold."
    )
    parser.add_argument(
        "--predicted",
        required=True,
        help="Predicted link JSONL, e.g. document_reference_links_with_parent_ids_v2.jsonl",
    )
    parser.add_argument(
        "--gold",
        nargs="+",
        required=True,
        help="One or more reviewed link-gold JSONL files.",
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


def _link_key(row: dict) -> tuple[str, int, str]:
    return (row["source_document_id"], int(row["raw_start"]), row["target_document_id"])


def _occurrence_key(row: dict) -> tuple[str, int]:
    return (row["source_document_id"], int(row["raw_start"]))


def main() -> None:
    args = parse_args()

    predicted_rows = _load_jsonl(args.predicted)
    gold_rows: list[dict] = []
    for gold_path in args.gold:
        gold_rows.extend(_load_jsonl(gold_path))

    predicted_by_link = {_link_key(row): row for row in predicted_rows}
    predicted_by_occurrence: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in predicted_rows:
        predicted_by_occurrence[_occurrence_key(row)].append(row)

    gold_by_link = {_link_key(row): row for row in gold_rows}
    gold_by_occurrence: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in gold_rows:
        gold_by_occurrence[_occurrence_key(row)].append(row)

    gold_link_keys = set(gold_by_link)
    predicted_link_keys = set(predicted_by_link)
    tp_link_keys = gold_link_keys & predicted_link_keys
    missed_link_keys = gold_link_keys - predicted_link_keys

    scoped_occurrences = set(gold_by_occurrence)
    predicted_in_scope = {
        key: predicted_by_occurrence.get(key, [])
        for key in scoped_occurrences
    }

    scoped_predicted_link_count = sum(len(rows) for rows in predicted_in_scope.values())
    scoped_gold_link_count = len(gold_link_keys)
    true_positive_count = len(tp_link_keys)

    wrong_target_occurrences: list[dict] = []
    missing_occurrences: list[dict] = []

    for occ_key in sorted(scoped_occurrences):
        gold_links = gold_by_occurrence[occ_key]
        predicted_links = predicted_in_scope.get(occ_key, [])
        gold_targets = {row["target_document_id"] for row in gold_links}
        predicted_targets = {row["target_document_id"] for row in predicted_links}

        if not predicted_links:
            missing_occurrences.append(
                {
                    "source_document_id": occ_key[0],
                    "raw_start": occ_key[1],
                    "gold_targets": sorted(gold_targets),
                }
            )
            continue

        if gold_targets != predicted_targets:
            wrong_target_occurrences.append(
                {
                    "source_document_id": occ_key[0],
                    "raw_start": occ_key[1],
                    "gold_targets": sorted(gold_targets),
                    "predicted_targets": sorted(predicted_targets),
                }
            )

    exact_recall = true_positive_count / scoped_gold_link_count if scoped_gold_link_count else 0.0
    scoped_precision = (
        true_positive_count / scoped_predicted_link_count if scoped_predicted_link_count else 0.0
    )
    scoped_recall = exact_recall
    scoped_f1 = (
        2 * scoped_precision * scoped_recall / (scoped_precision + scoped_recall)
        if (scoped_precision + scoped_recall)
        else 0.0
    )

    by_target_source = Counter(
        (
            Path(row["target_document_path"]).parent.name
            if row.get("target_document_path")
            else "unknown"
        )
        for row in gold_rows
    )
    by_link_method = Counter(row.get("link_method") or "unknown" for row in gold_rows)

    result = {
        "predicted_path": args.predicted,
        "gold_paths": args.gold,
        "gold_link_count": scoped_gold_link_count,
        "scoped_predicted_link_count": scoped_predicted_link_count,
        "true_positive_link_count": true_positive_count,
        "exact_recall": exact_recall,
        "scoped_precision": scoped_precision,
        "scoped_recall": scoped_recall,
        "scoped_f1": scoped_f1,
        "wrong_target_occurrence_count": len(wrong_target_occurrences),
        "missing_occurrence_count": len(missing_occurrences),
        "gold_by_target_source": dict(sorted(by_target_source.items())),
        "gold_by_link_method": dict(sorted(by_link_method.items())),
        "missing_occurrences": missing_occurrences[:100],
        "wrong_target_occurrences": wrong_target_occurrences[:100],
    }

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.md_output:
        md_path = Path(args.md_output)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Document Reference Link Evaluation",
            "",
            f"- predicted: `{args.predicted}`",
            f"- gold link rows: `{scoped_gold_link_count}`",
            f"- scoped predicted rows: `{scoped_predicted_link_count}`",
            f"- true positive rows: `{true_positive_count}`",
            f"- exact recall: `{exact_recall:.3f}`",
            f"- scoped precision: `{scoped_precision:.3f}`",
            f"- scoped recall: `{scoped_recall:.3f}`",
            f"- scoped F1: `{scoped_f1:.3f}`",
            f"- wrong-target occurrences: `{len(wrong_target_occurrences)}`",
            f"- missing occurrences: `{len(missing_occurrences)}`",
            "",
            "## Gold By Target Source",
            "",
        ]
        for source, count in sorted(by_target_source.items()):
            lines.append(f"- `{source}`: `{count}`")
        lines.extend(["", "## Gold By Link Method", ""])
        for method, count in sorted(by_link_method.items()):
            lines.append(f"- `{method}`: `{count}`")
        if missing_occurrences:
            lines.extend(["", "## Missing Occurrences", ""])
            for row in missing_occurrences[:20]:
                lines.append(
                    f"- `{row['source_document_id']}@{row['raw_start']}` missing target(s) `{', '.join(row['gold_targets'])}`"
                )
        if wrong_target_occurrences:
            lines.extend(["", "## Wrong-Target Occurrences", ""])
            for row in wrong_target_occurrences[:20]:
                lines.append(
                    f"- `{row['source_document_id']}@{row['raw_start']}` gold `{', '.join(row['gold_targets'])}` predicted `{', '.join(row['predicted_targets'])}`"
                )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LINK EVALUATION COMPLETE ---")
    print(f"Gold link rows:         {scoped_gold_link_count}")
    print(f"Scoped predicted rows:  {scoped_predicted_link_count}")
    print(f"True positive rows:     {true_positive_count}")
    print(f"Exact recall:           {exact_recall:.3f}")
    print(f"Scoped precision:       {scoped_precision:.3f}")
    print(f"Scoped recall:          {scoped_recall:.3f}")
    print(f"Scoped F1:              {scoped_f1:.3f}")
    if args.json_output:
        print(f"JSON output:            {args.json_output}")
    if args.md_output:
        print(f"Markdown output:        {args.md_output}")


if __name__ == "__main__":
    main()
