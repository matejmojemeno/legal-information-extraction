#!/usr/bin/env python3
"""
Prepare a deterministic LLM-routing queue for document-reference hard cases.

This script combines:
- extracted but unresolved references that match known hard-linking families
- likely missed extraction candidates from a bounded second-pass candidate miner
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.document_reference_linker import (
    DocumentSelfIdentifier,
    _add_duplicate_candidate_hints,
    _candidate_target_row,
    _compact_key,
    _nalus_reference_without_chamber_body,
    _nalus_short_year_body,
    _reference_link_keys,
    _uohs_proceeding_key,
    build_document_self_id_index,
)
from src.document_reference_extractor import DocumentReferenceOccurrence


_PREFIX_BODY = r"(?:sp\.?\s*zn\.?|sen\.?\s*zn\.?|č\.?\s*j\.?|čj\.?)"
_PREFIX_PATTERN = re.compile(
    rf"(?<!\w)(?P<prefix>{_PREFIX_BODY})\s*:?\s*(?P<body>[^\n\]\);]{{4,140}})",
    re.IGNORECASE,
)
_BODY_CUTOFF_PATTERN = re.compile(
    r"("
    r"(?:,?\s+ze\s+dne\b)|"
    r"(?:,?\s+(?:a|či|nebo)\s+ze\s+dne\b)|"
    r"(?:,?\s+kter(?:ý|á|é|ou|ým|ého|ém|ých)\b)|"
    r"(?:,?\s+vydan(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+veden(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+ve\s+věci\b)|"
    rf"(?:\s+(?:a|či|nebo)\s+{_PREFIX_BODY})|"
    r"(?:[;\)\]\n])"
    r")",
    re.IGNORECASE,
)
_BARE_US_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>("
    r"(?:Pl\.?\s*ÚS(?:-st\.)?|\b[IVXLCDM]+\.\s*ÚS|\bÚS)\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?"
    r"))",
    re.IGNORECASE,
)
_BARE_COURT_CASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare deterministic LLM queue for document references.")
    parser.add_argument(
        "--processed-root",
        default="data/processed",
        help="Processed source corpus root.",
    )
    parser.add_argument(
        "--unresolved-input",
        default="data/processed/document_reference_unresolved_with_parent_ids_v15.jsonl",
        help="Current unresolved document-reference links.",
    )
    parser.add_argument(
        "--linked-input",
        default="data/processed/document_reference_links_with_parent_ids_v15.jsonl",
        help="Current linked document-reference rows.",
    )
    parser.add_argument(
        "--external-self-id-metadata",
        default="data/metadata/document_dates.jsonl.gz",
        help="External self-identifier metadata dump.",
    )
    parser.add_argument(
        "--output",
        default="data/processed/document_reference_llm_queue_v1.jsonl",
        help="Output JSONL queue path.",
    )
    parser.add_argument(
        "--summary-output",
        default="data/processed/document_reference_llm_queue_v1.md",
        help="Output Markdown summary path.",
    )
    parser.add_argument(
        "--max-extraction-gap-per-source",
        type=int,
        default=25,
        help="Maximum extraction-gap queue entries per source.",
    )
    return parser.parse_args()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text: str, start: int, end: int, radius: int = 180) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return _normalize_spaces(text[lo:hi])


def _load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _identifier_candidates_for_key(
    self_id_index: dict[str, list[DocumentSelfIdentifier]],
    key: str,
    source_document_path: str,
) -> list[DocumentSelfIdentifier]:
    source_path_resolved = str(Path(source_document_path).resolve())
    return [
        item
        for item in self_id_index.get(key, [])
        if item.document_path != source_path_resolved
    ]


def _build_link_route_entry(
    row: dict,
    llm_route: str,
    candidates: list[DocumentSelfIdentifier],
) -> dict:
    entry_id = (
        f"link::{llm_route}::{row['source_document_id']}::{int(row['raw_start'])}"
    )
    return {
        "entry_id": entry_id,
        "entry_type": "linking",
        "llm_route": llm_route,
        "source_document_id": row["source_document_id"],
        "source_document_path": row["source_document_path"],
        "source": row["source_document_source"],
        "target_reference": row["reference_text"],
        "reference_body": row.get("reference_body"),
        "reference_prefix": row.get("reference_prefix"),
        "reference_type": row.get("reference_type"),
        "raw_start": row["raw_start"],
        "raw_end": row["raw_end"],
        "context_block": _snippet(
            Path(row["source_document_path"]).read_text(encoding="utf-8"),
            int(row["raw_start"]),
            int(row["raw_end"]),
        ),
        "candidate_targets": _add_duplicate_candidate_hints([
            _candidate_target_row(candidate, match_method="deterministic_queue")
            for candidate in candidates[:8]
        ]),
    }


def _build_extraction_route_entry(
    source_document_id: str,
    source_document_path: str,
    source: str,
    candidate_text: str,
    candidate_category: str,
    raw_start: int,
    raw_end: int,
    context_block: str,
) -> dict:
    entry_id = f"extract::{source_document_id}::{raw_start}"
    return {
        "entry_id": entry_id,
        "entry_type": "extraction",
        "llm_route": "extraction_presence_check",
        "source_document_id": source_document_id,
        "source_document_path": source_document_path,
        "source": source,
        "candidate_text": candidate_text,
        "candidate_category": candidate_category,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "context_block": context_block,
    }


def _route_unresolved_linking(
    unresolved_rows: list[dict],
    self_id_index: dict[str, list[DocumentSelfIdentifier]],
) -> list[dict]:
    routed: list[dict] = []
    seen: set[tuple[str, int, str]] = set()

    for row in unresolved_rows:
        body = str(row.get("reference_body") or "").strip()
        if not body:
            continue

        route = None
        candidates: list[DocumentSelfIdentifier] = []

        chamberless = _nalus_reference_without_chamber_body(body)
        if chamberless:
            key = _compact_key(chamberless)
            if key:
                candidates = _identifier_candidates_for_key(
                    self_id_index, key, row["source_document_path"]
                )
                unique_docs = {candidate.document_path for candidate in candidates}
                if len(unique_docs) > 1:
                    route = "link_disambiguation"

        if route is None:
            short_year = _nalus_short_year_body(body)
            if short_year:
                key = _compact_key(short_year)
                if key:
                    candidates = _identifier_candidates_for_key(
                        self_id_index, key, row["source_document_path"]
                    )
                    unique_docs = {candidate.document_path for candidate in candidates}
                    if len(unique_docs) >= 1:
                        route = "link_normalization_or_target_recovery"

        if route is None and re.search(r"\b(?:29\s+NSČR|29\s+ICDO)\b", body.upper()):
            route = "link_normalization_or_target_recovery"
            candidates = []

        if route is None and re.search(r"\b[IVXLCDM]+\.\s*ÚS\s+\d+/\d{4}\b", body):
            route = "link_normalization_or_target_recovery"
            candidates = []

        if route is None:
            continue

        dedupe_key = (row["source_document_id"], int(row["raw_start"]), route)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        routed.append(_build_link_route_entry(row, route, candidates))

    return routed


def _load_extracted_spans(paths: list[str]) -> dict[str, list[tuple[int, int]]]:
    spans_by_doc: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for path in paths:
        jsonl_path = Path(path)
        if not jsonl_path.exists():
            continue
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                source_path = str(Path(row["source_document_path"]).resolve())
                spans_by_doc[source_path].append((int(row["raw_start"]), int(row["raw_end"])))
    return spans_by_doc


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for other_start, other_end in spans:
        if start < other_end and other_start < end:
            return True
    return False


def _cut_prefixed_body(body: str) -> str:
    match = _BODY_CUTOFF_PATTERN.search(body)
    if match:
        body = body[: match.start()]
    return body.rstrip(" \t\r\n,.:;")


def _find_gap_candidates(text: str) -> list[tuple[int, int, str, str]]:
    candidates: list[tuple[int, int, str, str]] = []

    for match in _PREFIX_PATTERN.finditer(text):
        prefix = match.group("prefix")
        body = _cut_prefixed_body(match.group("body"))
        body = _normalize_spaces(body)
        if len(body) < 5 or not re.search(r"\d", body):
            continue
        full_text = _normalize_spaces(f"{prefix} {body}")
        category = "prefixed_anchor"
        candidates.append((match.start(), match.start() + len(match.group(0)), full_text, category))

    for match in _BARE_US_PATTERN.finditer(text):
        body = _normalize_spaces(match.group("body"))
        candidates.append((match.start("body"), match.end("body"), body, "ustavni_soud"))

    for match in _BARE_COURT_CASE_PATTERN.finditer(text):
        body = _normalize_spaces(match.group("body"))
        candidates.append((match.start("body"), match.end("body"), body, "bare_court_case"))

    dedup: dict[tuple[int, int, str], tuple[int, int, str, str]] = {}
    for start, end, candidate_text, category in candidates:
        dedup[(start, end, candidate_text)] = (start, end, candidate_text, category)
    return sorted(dedup.values(), key=lambda item: (item[0], item[1], item[2]))


def _route_extraction_gaps(
    processed_root: str,
    linked_input: str,
    unresolved_input: str,
    max_per_source: int,
) -> list[dict]:
    spans_by_doc = _load_extracted_spans([linked_input, unresolved_input])
    routed: list[dict] = []
    per_source_counts: Counter[str] = Counter()

    for source_dir in sorted(Path(processed_root).iterdir()):
        if not source_dir.is_dir() or source_dir.name not in {"nalus", "ns", "nss", "uohs"}:
            continue
        for path in sorted(source_dir.glob("*.txt")):
            source = source_dir.name
            if per_source_counts[source] >= max_per_source:
                break
            text = path.read_text(encoding="utf-8")
            spans = spans_by_doc.get(str(path.resolve()), [])
            local_kept: list[tuple[int, int]] = []
            for start, end, candidate_text, category in _find_gap_candidates(text):
                if category not in {"bare_court_case", "ustavni_soud"}:
                    continue
                if _overlaps(start, end, spans) or _overlaps(start, end, local_kept):
                    continue
                local_kept.append((start, end))
                routed.append(
                    _build_extraction_route_entry(
                        source_document_id=path.name,
                        source_document_path=str(path.resolve()),
                        source=source,
                        candidate_text=candidate_text,
                        candidate_category=category,
                        raw_start=start,
                        raw_end=end,
                        context_block=_snippet(text, start, end),
                    )
                )
                per_source_counts[source] += 1
                if per_source_counts[source] >= max_per_source:
                    break

    return routed


def main() -> None:
    args = parse_args()

    document_paths = [
        str(path.resolve())
        for path in sorted(Path(args.processed_root).rglob("*.txt"))
    ]
    self_id_index, _ = build_document_self_id_index(
        document_paths=document_paths,
        external_metadata_path=args.external_self_id_metadata,
        processed_root=args.processed_root,
    )

    unresolved_rows = _load_jsonl(args.unresolved_input)
    linking_entries = _route_unresolved_linking(unresolved_rows, self_id_index)
    extraction_entries = _route_extraction_gaps(
        processed_root=args.processed_root,
        linked_input=args.linked_input,
        unresolved_input=args.unresolved_input,
        max_per_source=args.max_extraction_gap_per_source,
    )

    all_entries = linking_entries + extraction_entries

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in all_entries:
            row["created_at_utc"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_path = Path(args.summary_output)
    by_route = Counter(row["llm_route"] for row in all_entries)
    by_source = Counter(row["source"] for row in all_entries)
    lines = [
        "# Document Reference LLM Queue V1",
        "",
        f"- total routed entries: `{len(all_entries)}`",
        "",
        "## Route Counts",
        "",
    ]
    for route, count in sorted(by_route.items()):
        lines.append(f"- `{route}`: `{count}`")
    lines.extend(["", "## Source Counts", ""])
    for source, count in sorted(by_source.items()):
        lines.append(f"- `{source}`: `{count}`")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("--- DOCUMENT REFERENCE LLM QUEUE READY ---")
    print(f"Routed entries: {len(all_entries)}")
    print(f"Queue output:   {output_path}")
    print(f"Summary output: {summary_path}")


if __name__ == "__main__":
    main()
