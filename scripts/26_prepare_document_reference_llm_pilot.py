#!/usr/bin/env python3
"""
Prepare a small reviewed pilot set for future LLM-assisted document-reference work.

This script does not run an LLM. It packages already-reviewed hard cases into a
compact batch that can later be used for prompting and manual comparison.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a document-reference LLM pilot batch.")
    parser.add_argument(
        "--unresolved-review",
        default="data/annotations/document_reference_links/review/unresolved_hard_cases_v1.jsonl",
        help="Reviewed unresolved-link hard-case JSONL.",
    )
    parser.add_argument(
        "--extraction-gap-review",
        default="data/annotations/document_references/review/extraction_gap_candidates_v1.jsonl",
        help="Reviewed extraction-gap hard-case JSONL.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/document_reference_links/eval/document_reference_llm_pilot_v1.json",
        help="Output JSON batch path.",
    )
    parser.add_argument(
        "--summary-output",
        default="data/annotations/document_reference_links/eval/document_reference_llm_pilot_v1.md",
        help="Output Markdown summary path.",
    )
    parser.add_argument("--ambiguous-count", type=int, default=5)
    parser.add_argument("--linker-miss-count", type=int, default=5)
    parser.add_argument("--extraction-count", type=int, default=5)
    return parser.parse_args()


def _load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _entry_id(prefix: str, row: dict, ordinal: int) -> str:
    document_id = str(row.get("source_document_id") or "unknown_document")
    start = row.get("raw_start")
    start_text = "na" if start is None else str(start)
    return f"{prefix}::{document_id}::{start_text}::{ordinal}"


def _unresolved_entry(row: dict, ordinal: int, route: str) -> dict:
    target_reference = row.get("reference_text") or row.get("reference_body") or ""
    expected = {
        "review_label": row.get("review_label"),
        "review_note": row.get("review_note"),
        "suggested_action": row.get("suggested_action"),
    }
    return {
        "entry_id": _entry_id("unresolved", row, ordinal),
        "pilot_group": "linking",
        "llm_route": route,
        "document_id": row.get("source_document_id"),
        "source": row.get("source_source"),
        "document_path": row.get("source_document_path"),
        "target_reference": target_reference,
        "reference_body": row.get("reference_body"),
        "reference_type": row.get("reference_type"),
        "reference_prefix": row.get("reference_prefix"),
        "context_block": row.get("source_context"),
        "raw_start": row.get("raw_start"),
        "raw_end": row.get("raw_end"),
        "deterministic_status": "deterministic_extracted_but_unlinked",
        "expected_outcome": expected,
    }


def _gap_entry(row: dict, ordinal: int) -> dict:
    return {
        "entry_id": _entry_id("gap", row, ordinal),
        "pilot_group": "extraction",
        "llm_route": "extraction_presence_check",
        "document_id": row.get("source_document_id"),
        "source": row.get("source_source"),
        "document_path": row.get("source_document_path"),
        "target_reference": row.get("candidate_text"),
        "candidate_category": row.get("candidate_category"),
        "context_block": row.get("source_context"),
        "raw_start": row.get("raw_start"),
        "raw_end": row.get("raw_end"),
        "deterministic_status": "deterministic_missed_candidate",
        "expected_outcome": {
            "review_label": row.get("review_label"),
            "review_note": row.get("review_note"),
            "suggested_action": row.get("suggested_action"),
        },
    }


def _take(rows: list[dict], limit: int) -> list[dict]:
    return rows[:limit]


def main() -> None:
    args = parse_args()

    unresolved_rows = _load_jsonl(args.unresolved_review)
    gap_rows = _load_jsonl(args.extraction_gap_review)

    ambiguous_rows = [
        row
        for row in unresolved_rows
        if row.get("review_label") == "ambiguous_target"
    ]
    linker_miss_rows = [
        row
        for row in unresolved_rows
        if row.get("review_label") == "linker_miss_in_corpus"
    ]
    extraction_rows = [
        row
        for row in gap_rows
        if row.get("review_label") == "should_have_been_extracted"
        and row.get("candidate_category") in {"bare_court_case", "ustavni_soud", "prefixed_anchor"}
    ]

    entries: list[dict] = []
    ordinal = 0

    for row in _take(ambiguous_rows, args.ambiguous_count):
        ordinal += 1
        entries.append(_unresolved_entry(row, ordinal, "link_disambiguation"))

    for row in _take(linker_miss_rows, args.linker_miss_count):
        ordinal += 1
        route = "link_normalization_or_target_recovery"
        entries.append(_unresolved_entry(row, ordinal, route))

    for row in _take(extraction_rows, args.extraction_count):
        ordinal += 1
        entries.append(_gap_entry(row, ordinal))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    batch = {
        "schema_version": "document_reference_llm_pilot_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Small reviewed hard-case batch for future LLM-assisted document-reference "
            "extraction and linking experiments."
        ),
        "source_reviews": [
            str(Path(args.unresolved_review).resolve()),
            str(Path(args.extraction_gap_review).resolve()),
        ],
        "entry_count": len(entries),
        "route_counts": dict(sorted(Counter(entry["llm_route"] for entry in entries).items())),
        "entries": entries,
    }
    output_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    by_route = Counter(entry["llm_route"] for entry in entries)
    by_source = Counter(entry.get("source") or "unknown" for entry in entries)
    lines = [
        "# Document Reference LLM Pilot V1",
        "",
        "- purpose: reviewed hard-case pilot for future LLM-assisted document-reference extraction/linking",
        f"- entries: `{len(entries)}`",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted(by_route.items()):
        lines.append(f"- `{route}`: `{count}`")
    lines.extend(["", "## Source Counts", ""])
    for source, count in sorted(by_source.items()):
        lines.append(f"- `{source}`: `{count}`")
    lines.extend(
        [
            "",
            "## Intended Use",
            "",
            "- `link_disambiguation`: the reference is real, but deterministic linking cannot choose safely among plausible targets.",
            "- `link_normalization_or_target_recovery`: the reference is real, but deterministic linking likely needs stronger normalization or better target identification.",
            "- `extraction_presence_check`: the reviewed candidate looks like a real missed reference and is suitable for a bounded extraction-presence prompt.",
        ]
    )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LLM PILOT READY ---")
    print(f"Entries:         {len(entries)}")
    print(f"JSON batch:      {output_path}")
    print(f"Markdown summary:{summary_path}")


if __name__ == "__main__":
    main()
