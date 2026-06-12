#!/usr/bin/env python3
"""
Prepare a larger reviewed benchmark slice for route-based LLM fallback tests.

The benchmark mixes:
- link disambiguation cases with reviewed exact and ambiguous outcomes
- link normalization / target recovery cases with reviewed unresolved and
  UOHS same-proceeding outcomes
- extraction presence checks with reviewed positive and negative outcomes
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


DISAMBIGUATION_EXACT_IDS = [
    "link::link_disambiguation::GetText.aspx_sz_1-1042-19_1.txt::6723",
    "link::link_disambiguation::GetText.aspx_sz_1-1042-19_1.txt::6735",
    "link::link_disambiguation::GetText.aspx_sz_1-1042-19_1.txt::6746",
]

DISAMBIGUATION_AMBIGUOUS = {
    "link::link_disambiguation::GetText.aspx_sz_1-1342-19_1.txt::2796": (
        "manual_v2:the shortened ÚS 31/04 citation follows II. ÚS 465/02 but does not carry a chamber for itself, and the remaining candidates stay genuinely ambiguous"
    ),
    "link::link_disambiguation::GetText.aspx_sz_1-1542-19_1.txt::4199": (
        "manual_v2:the cited ÚS 195/13 points to two duplicate IV. ÚS target documents, so the provided candidate list does not permit a unique document-id choice"
    ),
    "link::link_disambiguation::GetText.aspx_sz_1-1542-19_1.txt::5345": (
        "manual_v2:the shortened ÚS 17/19 list item could refer either to II. ÚS 17/19 or Pl. ÚS 17/19, with no safe tie-breaker in local context"
    ),
    "link::link_disambiguation::GetText.aspx_sz_1-1542-19_1.txt::6636": (
        "manual_v2:the shortened ÚS 457/05 citation has no chamber clue in local context, so multiple chamber candidates remain plausible"
    ),
    "link::link_disambiguation::GetText.aspx_sz_1-196-97.txt::6704": (
        "manual_v2:the shortened ÚS 85/95 citation is presented without chamber and local context does not identify which chamber decision is meant"
    ),
    "link::link_disambiguation::GetText.aspx_sz_1-196-97.txt::15147": (
        "manual_v2:the shortened ÚS 116/96 citation is listed without chamber, and the surrounding citations do not safely propagate a chamber numeral"
    ),
    "link::link_disambiguation::621985.txt::4496": (
        "manual_v2:the cited ÚS 50/03 lacks chamber designation and remains ambiguous among chamber and plenary candidates"
    ),
}

NORMALIZATION_UNRESOLVED_IDS = {
    "link::link_normalization_or_target_recovery::GetText.aspx_sz_1-562-03.txt::2575": (
        "manual_v2:with no candidate targets supplied, the current bounded recovery task should remain unresolved"
    ),
    "link::link_normalization_or_target_recovery::GetText.aspx_sz_2-107-02.txt::11321": (
        "manual_v2:with no candidate targets supplied, the current bounded recovery task should remain unresolved"
    ),
    "link::link_normalization_or_target_recovery::GetText.aspx_sz_3-3248-22_1.txt::4236": (
        "manual_v2:the cited NS insolvency docket is real, but the current route input has no candidate targets and should remain unresolved"
    ),
    "link::link_normalization_or_target_recovery::25 cdo 3226_2018_openElement.txt::9348": (
        "manual_v2:the cited NSČR docket is real, but without candidate targets the bounded recovery route should stay unresolved"
    ),
    "link::link_normalization_or_target_recovery::29 icdo 100_2018_openElement.txt::6212": (
        "manual_v2:the cited ICdo docket is real, but the current route input lacks candidate targets and should remain unresolved"
    ),
    "link::link_normalization_or_target_recovery::29 nscr 154_2022_openElement.txt::4145": (
        "manual_v2:the cited NSČR docket is in-scope, but the current bounded route has no candidate targets and should remain unresolved"
    ),
}

NORMALIZATION_SAME_PROCEEDING_KEYS = {
    ("2012_R303.txt", 4873): "manual_v2:the cited UOHS act belongs to proceeding S267/2012, but the reviewed target is a different published decision from the same proceeding",
    ("2018_R42-43-44.txt", 56614): "manual_v2:the cited UOHS act belongs to proceeding S0694/2016, and the reviewed target is the same proceeding rather than the exact cited act",
    ("2012_R243.txt", 3271): "manual_v2:the cited UOHS act belongs to proceeding S280/2012, so the correct bounded recovery output is same_proceeding",
    ("2014_S184_1.txt", 1423): "manual_v2:the cited UOHS act belongs to proceeding S184/2014, and the reviewed target is the corresponding same-proceeding document",
}

EXTRACTION_POSITIVE_KEYS = {
    ("GetText.aspx_sz_1-1654-19_1.txt", 2025): (
        "spisova_znacka",
        "manual_v2:real bare NS citation in a citation list"
    ),
    ("GetText.aspx_sz_3-3248-22_1.txt", 4236): (
        "spisova_znacka",
        "manual_v2:real NS insolvency docket cited inside a multi-part reference"
    ),
    ("GetText.aspx_sz_4-581-23_1.txt", 3334): (
        "spisova_znacka",
        "manual_v2:real shorthand ÚS citation in a list of Constitutional Court cases"
    ),
    ("GetText.aspx_sz_4-581-23_1.txt", 3421): (
        "spisova_znacka",
        "manual_v2:real shorthand ÚS citation in a list of Constitutional Court cases"
    ),
    ("GetText.aspx_sz_2-107-02.txt", 2443): (
        "cislo_jednaci",
        "manual_v2:real missed prefixed administrative identifier whose span should end before the trailing prose"
    ),
}

EXTRACTION_NEGATIVE_KEYS = {
    ("GetText.aspx_sz_2-610-04.txt", 4129): "manual_v2:reporter citation R 58/2001 is out of scope for document-reference extraction",
    ("GetText.aspx_sz_1-1629-19_1.txt", 2093): "manual_v2:reporter citation R 81/2013 is out of scope for document-reference extraction",
    ("560414.txt", 9073): "manual_v2:Sb. NSS collection citation is out of scope for the document-reference task",
    ("32 odo 1114_2006_openElement.txt", 19210): "manual_v2:this is the current document header/self identifier, not a reference to another document",
    ("533225.txt", 9): "manual_v2:this is a page/header self identifier rather than a cited external document",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a larger reviewed LLM-route benchmark for document references."
    )
    parser.add_argument(
        "--queue",
        default="data/processed/document_reference_llm_queue_v1.jsonl",
        help="Deterministic LLM queue JSONL.",
    )
    parser.add_argument(
        "--same-proceeding-gold",
        default="data/annotations/document_reference_links/gold/uohs_same_proceeding_link_gold_v1.jsonl",
        help="Reviewed UOHS same-proceeding gold JSONL.",
    )
    parser.add_argument(
        "--extraction-gap-review",
        default="data/annotations/document_references/review/extraction_gap_candidates_v1.jsonl",
        help="Reviewed extraction-gap JSONL.",
    )
    parser.add_argument(
        "--output-input",
        default="data/annotations/document_reference_links/eval/document_reference_llm_route_benchmark_v2.jsonl",
        help="Output JSONL input batch for the LLM runner.",
    )
    parser.add_argument(
        "--output-gold",
        default="data/annotations/document_reference_links/gold/document_reference_llm_route_gold_v2.jsonl",
        help="Output reviewed gold JSONL.",
    )
    parser.add_argument(
        "--summary-output",
        default="data/annotations/document_reference_links/eval/document_reference_llm_route_benchmark_v2.md",
        help="Output Markdown summary.",
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


def _uohs_proceeding_identifier(reference_text: str) -> str:
    match = re.search(r"\b([SR]\d{1,4}/\d{4}(?:/[A-Z]{1,4})?)\b", reference_text.replace(" ", ""))
    if match:
        return match.group(1)
    match = re.search(r"\b([SR]\d{1,4}/\d{4})\b", reference_text.replace(" ", ""))
    if match:
        return match.group(1)
    return "same proceeding root"


def _source_from_path(path: str) -> str:
    return Path(path).parent.name


def _queue_entry(queue_by_id: dict[str, dict], entry_id: str) -> dict:
    if entry_id not in queue_by_id:
        raise KeyError(f"Missing queue entry: {entry_id}")
    return json.loads(json.dumps(queue_by_id[entry_id], ensure_ascii=False))


def _gold_row_from_queue(entry: dict, expected_decision: str, *, expected_target_document_id: str | None = None, expected_reference_type: str | None = None, review_note: str) -> dict:
    return {
        "entry_id": entry["entry_id"],
        "llm_route": entry["llm_route"],
        "source": entry.get("source"),
        "source_document_id": entry.get("source_document_id"),
        "expected_decision": expected_decision,
        "expected_target_document_id": expected_target_document_id,
        "expected_reference_type": expected_reference_type,
        "review_note": review_note,
    }


def main() -> None:
    args = parse_args()

    queue_rows = _load_jsonl(args.queue)
    queue_by_id = {row["entry_id"]: row for row in queue_rows}

    same_proceeding_rows = _load_jsonl(args.same_proceeding_gold)
    same_proceeding_by_key = {
        (row["source_document_id"], int(row["raw_start"])): row for row in same_proceeding_rows
    }

    extraction_review_rows = _load_jsonl(args.extraction_gap_review)
    extraction_review_by_key = {
        (row["source_document_id"], int(row["raw_start"])): row for row in extraction_review_rows
    }

    input_entries: list[dict] = []
    gold_rows: list[dict] = []

    for entry_id in DISAMBIGUATION_EXACT_IDS:
        entry = _queue_entry(queue_by_id, entry_id)
        expected_target_document_id = entry["candidate_targets"][2]["target_document_id"] if entry_id.endswith("6723") else None
        if entry_id.endswith("6735"):
            expected_target_document_id = "4-30-02.txt"
        elif entry_id.endswith("6746"):
            expected_target_document_id = "4-255-05.txt"
        elif entry_id.endswith("6723"):
            expected_target_document_id = "4-130-98.txt"
        input_entries.append(entry)
        gold_rows.append(
            _gold_row_from_queue(
                entry,
                "exact_target",
                expected_target_document_id=expected_target_document_id,
                review_note="manual_v2:the chamber designation IV. ÚS is inherited across the citation list",
            )
        )

    for entry_id, review_note in DISAMBIGUATION_AMBIGUOUS.items():
        entry = _queue_entry(queue_by_id, entry_id)
        input_entries.append(entry)
        gold_rows.append(
            _gold_row_from_queue(
                entry,
                "ambiguous",
                review_note=review_note,
            )
        )

    for entry_id, review_note in NORMALIZATION_UNRESOLVED_IDS.items():
        entry = _queue_entry(queue_by_id, entry_id)
        input_entries.append(entry)
        gold_rows.append(
            _gold_row_from_queue(
                entry,
                "unresolved",
                review_note=review_note,
            )
        )

    for key, review_note in NORMALIZATION_SAME_PROCEEDING_KEYS.items():
        row = same_proceeding_by_key[key]
        reference_text = row["reference_text"]
        candidate_identifier = _uohs_proceeding_identifier(reference_text)
        entry_id = f"routev2::same_proceeding::{row['source_document_id']}::{int(row['raw_start'])}"
        entry = {
            "entry_id": entry_id,
            "entry_type": "linking",
            "llm_route": "link_normalization_or_target_recovery",
            "source_document_id": row["source_document_id"],
            "source_document_path": row["source_document_path"],
            "source": _source_from_path(row["source_document_path"]),
            "target_reference": row["reference_text"],
            "reference_body": row.get("reference_body"),
            "reference_prefix": row.get("reference_prefix"),
            "reference_type": "cislo_jednaci",
            "raw_start": row["raw_start"],
            "raw_end": row["raw_end"],
            "context_block": Path(row["source_document_path"]).read_text(encoding="utf-8")[
                max(0, int(row["raw_start"]) - 180): min(
                    len(Path(row["source_document_path"]).read_text(encoding="utf-8")),
                    int(row["raw_end"]) + 180,
                )
            ].replace("\n", " "),
            "candidate_targets": [
                {
                    "target_document_id": row["target_document_id"],
                    "target_document_path": row["target_document_path"],
                    "target_identifier": candidate_identifier,
                    "target_source": _source_from_path(row["target_document_path"]),
                    "target_identifier_kind": "spisova_znacka",
                    "target_origin": "reviewed_same_proceeding_gold_v1",
                    "target_source_iri": None,
                }
            ],
        }
        input_entries.append(entry)
        gold_rows.append(
            {
                "entry_id": entry_id,
                "llm_route": "link_normalization_or_target_recovery",
                "source": entry["source"],
                "source_document_id": entry["source_document_id"],
                "expected_decision": "same_proceeding",
                "expected_target_document_id": row["target_document_id"],
                "expected_reference_type": None,
                "review_note": review_note,
            }
        )

    for key, (expected_reference_type, review_note) in EXTRACTION_POSITIVE_KEYS.items():
        row = extraction_review_by_key[key]
        entry_id = f"routev2::extract::{row['source_document_id']}::{int(row['raw_start'])}"
        entry = {
            "entry_id": entry_id,
            "entry_type": "extraction",
            "llm_route": "extraction_presence_check",
            "source_document_id": row["source_document_id"],
            "source_document_path": row["source_document_path"],
            "source": row.get("source_source") or _source_from_path(row["source_document_path"]),
            "candidate_text": row["candidate_text"],
            "candidate_category": row.get("candidate_category"),
            "raw_start": row["raw_start"],
            "raw_end": row["raw_end"],
            "context_block": row["source_context"],
        }
        input_entries.append(entry)
        gold_rows.append(
            {
                "entry_id": entry_id,
                "llm_route": "extraction_presence_check",
                "source": entry["source"],
                "source_document_id": entry["source_document_id"],
                "expected_decision": "is_reference",
                "expected_target_document_id": None,
                "expected_reference_type": expected_reference_type,
                "review_note": review_note,
            }
        )

    for key, review_note in EXTRACTION_NEGATIVE_KEYS.items():
        row = extraction_review_by_key[key]
        entry_id = f"routev2::extract::{row['source_document_id']}::{int(row['raw_start'])}"
        entry = {
            "entry_id": entry_id,
            "entry_type": "extraction",
            "llm_route": "extraction_presence_check",
            "source_document_id": row["source_document_id"],
            "source_document_path": row["source_document_path"],
            "source": row.get("source_source") or _source_from_path(row["source_document_path"]),
            "candidate_text": row["candidate_text"],
            "candidate_category": row.get("candidate_category"),
            "raw_start": row["raw_start"],
            "raw_end": row["raw_end"],
            "context_block": row["source_context"],
        }
        input_entries.append(entry)
        gold_rows.append(
            {
                "entry_id": entry_id,
                "llm_route": "extraction_presence_check",
                "source": entry["source"],
                "source_document_id": entry["source_document_id"],
                "expected_decision": "not_reference",
                "expected_target_document_id": None,
                "expected_reference_type": "unknown",
                "review_note": review_note,
            }
        )

    output_input = Path(args.output_input)
    output_input.parent.mkdir(parents=True, exist_ok=True)
    with output_input.open("w", encoding="utf-8") as f:
        for row in input_entries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    output_gold = Path(args.output_gold)
    output_gold.parent.mkdir(parents=True, exist_ok=True)
    with output_gold.open("w", encoding="utf-8") as f:
        for row in gold_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_output = Path(args.summary_output)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    route_counts = Counter(row["llm_route"] for row in gold_rows)
    decision_counts = Counter(row["expected_decision"] for row in gold_rows)
    lines = [
        "# Document Reference LLM Route Benchmark V2",
        "",
        f"- input rows: `{len(input_entries)}`",
        f"- gold rows: `{len(gold_rows)}`",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted(route_counts.items()):
        lines.append(f"- `{route}`: `{count}`")
    lines.extend(["", "## Expected Decision Counts", ""])
    for decision, count in sorted(decision_counts.items()):
        lines.append(f"- `{decision}`: `{count}`")
    summary_output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LLM ROUTE BENCHMARK V2 READY ---")
    print(f"Input rows:      {len(input_entries)}")
    print(f"Gold rows:       {len(gold_rows)}")
    print(f"Input output:    {output_input}")
    print(f"Gold output:     {output_gold}")
    print(f"Summary output:  {summary_output}")


if __name__ == "__main__":
    main()
