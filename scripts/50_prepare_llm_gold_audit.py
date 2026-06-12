#!/usr/bin/env python3
"""Prepare LLM-assisted consistency-audit tasks for the joint gold set.

The output is intentionally an audit queue, not a second human annotation.
Each task contains the current gold decision, local source context, and, for
document links, the reviewed candidate/metadata layer that was available during
link adjudication.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare LLM gold-audit tasks.")
    parser.add_argument(
        "--law-gold",
        default="data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl",
        help="Joint law-reference gold JSONL.",
    )
    parser.add_argument(
        "--docref-gold",
        default="data/annotations/joint_reference_gold_v1/document_reference_gold/joint_document_reference_gold_v1.jsonl",
        help="Joint document-reference gold JSONL.",
    )
    parser.add_argument(
        "--docref-link-review",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_review_v1/joint_document_reference_link_review_v1.jsonl",
        help="Reviewed document-reference link/availability JSONL.",
    )
    parser.add_argument(
        "--metadata",
        default="data/metadata/document_metadata.jsonl.gz",
        help="Corpus metadata JSONL/JSONL.GZ used to enrich candidate targets.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_tasks.jsonl",
        help="Output audit-task JSONL.",
    )
    parser.add_argument(
        "--law-before",
        type=int,
        default=2200,
        help="Characters before a law citation to include.",
    )
    parser.add_argument(
        "--law-after",
        type=int,
        default=900,
        help="Characters after a law citation to include.",
    )
    parser.add_argument(
        "--docref-before",
        type=int,
        default=1500,
        help="Characters before a document reference to include when no reviewed context exists.",
    )
    parser.add_argument(
        "--docref-after",
        type=int,
        default=900,
        help="Characters after a document reference to include when no reviewed context exists.",
    )
    parser.add_argument(
        "--task-types",
        nargs="+",
        choices=["law", "docref_span", "docref_link"],
        default=["law", "docref_span", "docref_link"],
        help="Task families to include.",
    )
    return parser.parse_args()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_metadata_indexes(
    path: str | Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    metadata_path = Path(path)
    if not metadata_path.exists() or metadata_path.is_dir():
        return {}
    opener = gzip.open if metadata_path.suffix == ".gz" else open
    index: dict[tuple[str, str], dict[str, Any]] = {}
    by_document_id: dict[str, list[dict[str, Any]]] = {}
    with opener(metadata_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            source = row.get("source")
            document_id = row.get("document_id")
            if source and document_id:
                index[(str(source), str(document_id))] = row
                by_document_id.setdefault(str(document_id), []).append(row)
    return index, by_document_id


def _write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _context(text: str, start: int, end: int, before: int, after: int) -> dict[str, Any]:
    lo = max(0, start - before)
    hi = min(len(text), end + after)
    return {
        "context_start": lo,
        "context_end": hi,
        "before_context": text[lo:start],
        "reference_text_from_document": text[start:end],
        "after_context": text[end:hi],
        "compact_context": _normalize_spaces(text[lo:hi]),
    }


def _doc_header(text: str, limit: int = 1200) -> str:
    return _normalize_spaces(text[:limit])


def _load_law_tasks(path: str, before: int, after: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for doc in _load_jsonl(path):
        text = _read_text(str(doc["document_path"]))
        header = _doc_header(text)
        for citation in doc.get("citations", []):
            start = int(citation["start_char"])
            end = int(citation["end_char"])
            task = {
                "audit_task_id": f"law::{doc['document_id']}::{start}::{citation.get('citation_type')}",
                "audit_task_type": "law",
                "document_id": doc["document_id"],
                "source": doc.get("source"),
                "document_path": doc["document_path"],
                "document_header": header,
                "reference": {
                    "citation_text": citation.get("citation_text"),
                    "citation_type": citation.get("citation_type"),
                    "start_char": start,
                    "end_char": end,
                    "detail_number": citation.get("detail_number"),
                    "detail_odst": citation.get("detail_odst") or [],
                    "detail_pism": citation.get("detail_pism") or [],
                },
                "current_gold": {
                    "classification": citation.get("classification"),
                    "law_id": citation.get("law_id"),
                    "law_name_text": citation.get("law_name_text"),
                    "declared_alias_text": citation.get("declared_alias_text"),
                    "note": citation.get("note"),
                },
                "context": _context(text, start, end, before, after),
                "audit_instructions": [
                    "Review whether the current gold label is supported by the visible text.",
                    "Use the local context and document header; do not invent missing law identifiers.",
                    "For czech_resolved, verify that the cited provision is reasonably attached to the given law_id.",
                    "For non_citation, verify that the span is not a legal citation in this context.",
                    "For unresolved/foreign labels, verify that the text does not support a safe Czech law_id.",
                ],
            }
            tasks.append(task)
    return tasks


def _load_docref_span_tasks(path: str, before: int, after: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in _load_jsonl(path):
        text = _read_text(str(row["document_path"]))
        start = int(row["start_char"])
        end = int(row["end_char"])
        tasks.append(
            {
                "audit_task_id": f"docref_span::{row['document_id']}::{start}",
                "audit_task_type": "docref_span",
                "document_id": row["document_id"],
                "source": row.get("source"),
                "document_path": row["document_path"],
                "document_header": _doc_header(text),
                "reference": {
                    "reference_text": row.get("reference_text"),
                    "start_char": start,
                    "end_char": end,
                    "reference_type": row.get("reference_type"),
                    "reference_prefix": row.get("reference_prefix"),
                    "reference_body": row.get("reference_body"),
                    "decision_kind_hint": row.get("decision_kind_hint"),
                    "court_hint": row.get("court_hint"),
                },
                "current_gold": {
                    "classification": row.get("classification"),
                    "note": row.get("note"),
                },
                "context": _context(text, start, end, before, after),
                "audit_instructions": [
                    "Review whether this span is a real reference to another decision or case file.",
                    "Reporter citations, collection citations, and the current document's own identifier are out of scope.",
                    "A reference can be valid even when no exact target is available in the corpus.",
                    "Check whether the span boundaries include the complete identifier but avoid unrelated prose.",
                ],
            }
        )
    return tasks


def _enrich_candidate_targets(
    row: dict[str, Any],
    metadata_index: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [dict(candidate) for candidate in row.get("candidate_targets") or []]
    reference_body = str(row.get("reference_body") or row.get("reference_text") or "")
    compact_reference = re.sub(r"[^A-Za-z0-9]+", "", reference_body).upper()

    for candidate in candidates:
        target_source = str(candidate.get("target_source") or "")
        target_document_id = str(candidate.get("target_document_id") or "")
        metadata = metadata_index.get((target_source, target_document_id), {})
        for source_key, target_key in [
            ("decision_date", "target_decision_date"),
            ("decision_date_iso", "target_decision_date_iso"),
            ("decision_year", "target_decision_year"),
            ("decision_date_precision", "target_decision_date_precision"),
            ("judicate_name", "target_judicate_name"),
            ("judicate_iri", "target_judicate_iri"),
            ("blob_name", "target_blob_name"),
        ]:
            if metadata.get(source_key) is not None and candidate.get(target_key) is None:
                candidate[target_key] = metadata.get(source_key)

        target_identifier = str(
            candidate.get("target_identifier_text")
            or candidate.get("target_identifier")
            or candidate.get("target_judicate_name")
            or ""
        )
        compact_target = re.sub(r"[^A-Za-z0-9]+", "", target_identifier).upper()
        match_method = str(candidate.get("match_method") or "")
        if "same_proceeding" in match_method:
            candidate["audit_candidate_granularity"] = "proceeding_root"
            candidate["audit_granularity_reason"] = (
                "candidate was retrieved through a same-proceeding/proceeding-root key"
            )
        elif compact_target and compact_target == compact_reference:
            candidate["audit_candidate_granularity"] = "exact_identifier"
            candidate["audit_granularity_reason"] = "candidate identifier matches the cited identifier exactly after compact normalization"
        elif compact_target and compact_reference.startswith(compact_target):
            candidate["audit_candidate_granularity"] = "less_specific_than_reference"
            candidate["audit_granularity_reason"] = "candidate identifier is shorter/less specific than the cited reference"
        else:
            candidate["audit_candidate_granularity"] = "candidate_identifier"

    duplicate_groups: dict[tuple[str, str, str], list[str]] = {}
    for candidate in candidates:
        source = str(candidate.get("target_source") or "")
        identifier = str(candidate.get("target_judicate_name") or candidate.get("target_identifier_text") or "")
        date = str(candidate.get("target_decision_date_iso") or candidate.get("target_decision_date") or "")
        key = (source, re.sub(r"[^A-Za-z0-9]+", "", identifier).upper(), date)
        if source and identifier and date:
            duplicate_groups.setdefault(key, []).append(str(candidate.get("target_document_id") or ""))
    for candidate in candidates:
        source = str(candidate.get("target_source") or "")
        identifier = str(candidate.get("target_judicate_name") or candidate.get("target_identifier_text") or "")
        date = str(candidate.get("target_decision_date_iso") or candidate.get("target_decision_date") or "")
        key = (source, re.sub(r"[^A-Za-z0-9]+", "", identifier).upper(), date)
        ids = sorted(doc_id for doc_id in duplicate_groups.get(key, []) if doc_id)
        if len(ids) > 1:
            candidate["audit_duplicate_group_document_ids"] = ids
            candidate["audit_duplicate_group_size"] = len(ids)
            candidate["audit_duplicate_note"] = (
                "these candidate document ids share the same source, identifier, and decision date; "
                "they should be treated as duplicate corpus records for audit purposes"
            )
    return candidates


def _target_metadata_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_source": metadata.get("source"),
        "target_document_id": metadata.get("document_id"),
        "target_document_path": metadata.get("processed_rel_path"),
        "target_decision_date": metadata.get("decision_date"),
        "target_decision_date_iso": metadata.get("decision_date_iso"),
        "target_decision_year": metadata.get("decision_year"),
        "target_decision_date_precision": metadata.get("decision_date_precision"),
        "target_judicate_name": metadata.get("judicate_name"),
        "target_judicate_iri": metadata.get("judicate_iri"),
        "target_blob_name": metadata.get("blob_name"),
    }


def _docref_link_hints(row: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    availability = row.get("target_availability_gold")
    target_document_id = row.get("target_document_id_gold")
    if availability == "same_proceeding_only":
        hints.append(
            "The current gold label is same_proceeding_only. This means the cited text is more specific than the available corpus target, or only a proceeding-level representative is available."
        )
        if any(candidate.get("audit_candidate_granularity") == "proceeding_root" for candidate in candidates):
            hints.append(
                "At least one candidate was retrieved through a same-proceeding/proceeding-root key. Do not upgrade this to exact_in_corpus unless the candidate metadata contains the same full cited decision identifier."
            )
    if availability == "exact_in_corpus" and target_document_id:
        duplicate_ids = set()
        for candidate in candidates:
            duplicate_ids.update(candidate.get("audit_duplicate_group_document_ids") or [])
        if target_document_id in duplicate_ids:
            hints.append(
                "The current gold target is part of a duplicate candidate group with the same identifier and decision date. Treat this as compatible with exact_in_corpus unless the context points to a different legal target."
            )
        if not candidates:
            hints.append(
                "The candidate_targets list is empty because the automatic candidate builder did not retrieve a shortlist for this reviewed row. Do not treat an empty candidate list alone as proof of not_in_corpus when current_gold_target_metadata is provided."
            )
    return hints


def _load_docref_link_tasks(path: str, metadata_path: str) -> list[dict[str, Any]]:
    metadata_index, metadata_by_document_id = _load_metadata_indexes(metadata_path)
    tasks: list[dict[str, Any]] = []
    for row in _load_jsonl(path):
        start = int(row["start_char"])
        candidate_targets = _enrich_candidate_targets(row, metadata_index)
        gold_target_metadata: dict[str, Any] | None = None
        gold_target_id = row.get("target_document_id_gold")
        if gold_target_id:
            matches = metadata_by_document_id.get(str(gold_target_id), [])
            if len(matches) == 1:
                gold_target_metadata = _target_metadata_payload(matches[0])
            elif len(matches) > 1:
                gold_target_metadata = {
                    "ambiguous_document_id_matches": [
                        _target_metadata_payload(match) for match in matches
                    ]
                }
        tasks.append(
            {
                "audit_task_id": f"docref_link::{row['document_id']}::{start}",
                "audit_task_type": "docref_link",
                "document_id": row["document_id"],
                "source": row.get("source"),
                "document_path": row["document_path"],
                "reference": {
                    "reference_text": row.get("reference_text"),
                    "start_char": start,
                    "end_char": row.get("end_char"),
                    "reference_type": row.get("reference_type"),
                    "reference_prefix": row.get("reference_prefix"),
                    "reference_body": row.get("reference_body"),
                    "decision_kind_hint": row.get("decision_kind_hint"),
                    "court_hint": row.get("court_hint"),
                },
                "current_gold": {
                    "target_availability": row.get("target_availability_gold"),
                    "link_scope": row.get("link_scope_gold"),
                    "target_document_id": row.get("target_document_id_gold"),
                    "review_note": row.get("review_note"),
                },
                "system_link": row.get("system_link"),
                "system_status": row.get("system_status"),
                "candidate_targets": candidate_targets,
                "current_gold_target_metadata": gold_target_metadata,
                "label_specific_hints": _docref_link_hints(row, candidate_targets),
                "context": {
                    "compact_context": row.get("source_context") or "",
                },
                "audit_instructions": [
                    "Review whether the current link availability label is supported by the context and candidates.",
                    "exact_in_corpus means the reference identifies one concrete target document in candidate_targets.",
                    "same_proceeding_only means the reference points only to a broader proceeding-level target under the current corpus representation.",
                    "A unique candidate is not enough for exact_in_corpus. If the cited identifier is more specific than the candidate identifier, the correct label can still be same_proceeding_only.",
                    "For UOHS references, a citation such as ÚOHS-S49/2013/VZ-10502/2013/532/MKn is more specific than a candidate identifier such as S0049/2013. Treat that as same_proceeding_only unless candidate metadata contains the same full decision-tail identifier.",
                    "Do not upgrade same_proceeding_only to exact_in_corpus merely because candidate_targets contains one proceeding-root candidate.",
                    "When multiple candidate document ids share the same source, identifier, and decision date, treat them as duplicate corpus records. A canonical target chosen from that duplicate group can still support exact_in_corpus.",
                    "If current_gold_target_metadata is present, use it to audit the current gold target even when candidate_targets is empty.",
                    "An empty candidate_targets list means automatic shortlist retrieval failed for this audit task; it is not by itself evidence that the target is outside the corpus.",
                    "not_in_corpus means the cited target is outside the corpus sources or unavailable in candidate_targets.",
                    "unknown means the available context and candidates are still insufficient after search.",
                    "Choose a concrete target only from candidate_targets.",
                ],
            }
        )
    return tasks


def main() -> None:
    args = parse_args()
    tasks: list[dict[str, Any]] = []
    if "law" in args.task_types:
        tasks.extend(_load_law_tasks(args.law_gold, args.law_before, args.law_after))
    if "docref_span" in args.task_types:
        tasks.extend(_load_docref_span_tasks(args.docref_gold, args.docref_before, args.docref_after))
    if "docref_link" in args.task_types:
        tasks.extend(_load_docref_link_tasks(args.docref_link_review, args.metadata))

    tasks.sort(key=lambda row: (row["audit_task_type"], str(row["document_id"]), int(row["reference"].get("start_char") or 0)))
    _write_jsonl(args.output, tasks)

    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["audit_task_type"]] = counts.get(task["audit_task_type"], 0) + 1
    print("--- LLM GOLD AUDIT TASKS PREPARED ---")
    print(f"Output: {args.output}")
    print(f"Tasks:  {len(tasks)}")
    for key, value in sorted(counts.items()):
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
