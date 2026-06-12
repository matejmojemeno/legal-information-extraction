#!/usr/bin/env python3
"""Audit reviewed `not_in_corpus` document references against the full corpus.

This script is intentionally more aggressive than the deterministic linker:
- it works only on references already reviewed as `not_in_corpus`
- it searches the full production self-identifier layer
- it tries a few additional cleanup variants of the reference body
- it reports rows that deserve manual re-review
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.document_reference_extractor import DocumentReferenceOccurrence
from src.document_reference_linker import (
    DocumentSelfIdentifier,
    _NSS_AGENDA_CODES,
    _NS_AGENDA_CODES,
    _agenda_code_from_body,
    _fuzzy_score_reference_to_identifier,
    _normalize_fuzzy_text,
    _reference_link_keys,
    build_fuzzy_identifier_buckets,
)


_CONSTITUTIONAL_PATTERN = re.compile(
    r"\b(?:PL\.?\s*ÚS(?:-ST\.)?|[IVXLCDM]+\.\s*ÚS|ÚS)\b",
    re.IGNORECASE,
)
_UOHS_BODY_PATTERN = re.compile(
    r"\b(?:UOHS|VZ/S|[SR]\s*\d{1,4}(?:[A-Z])?(?:,\d{1,4})*/\d{2,4})",
    re.IGNORECASE,
)
_ADMIN_LOWER_CASE_PATTERN = re.compile(
    r"\b\d+\s+(?:A|AF|AS|AFS|ADS|AZS|AO|CA|CAD)\s+\d+/\d{2,4}\b",
    re.IGNORECASE,
)
_CIVIL_LOWER_CASE_PATTERN = re.compile(
    r"\b\d+\s+(?:C|CO|CM|CMO|T|TO|NT|NTD)\s+\d+/\d{2,4}\b",
    re.IGNORECASE,
)
_TRAILING_PAGE_PATTERN = re.compile(r"[\.,]?\s+\d{1,3}\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit reviewed document-reference rows labeled as not in corpus.",
    )
    parser.add_argument(
        "--reviewed",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_review_v1/joint_document_reference_link_review_v1.jsonl",
        help="Reviewed availability JSONL.",
    )
    parser.add_argument(
        "--self-identifiers",
        default="data/final_runs/thesis_final_v1/document_references/document_self_identifiers.jsonl",
        help="Full-corpus self-identifier JSONL.",
    )
    parser.add_argument(
        "--json-output",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_gold_v1/joint_document_reference_not_in_corpus_audit_fresh.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--md-output",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_gold_v1/joint_document_reference_not_in_corpus_audit_fresh.md",
        help="Markdown output path.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=30,
        help="Maximum number of detailed example rows in the Markdown report.",
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


def _load_self_identifiers(path: str) -> list[DocumentSelfIdentifier]:
    identifiers: list[DocumentSelfIdentifier] = []
    for row in _load_jsonl(path):
        identifiers.append(
            DocumentSelfIdentifier(
                document_id=str(row["document_id"]),
                document_path=str(row["document_path"]),
                source=str(row["source"]),
                identifier_text=str(row["identifier_text"]),
                identifier_kind=str(row.get("identifier_kind") or "unknown"),
                origin=str(row.get("origin") or "unknown"),
                keys=tuple(str(key) for key in row.get("keys", [])),
                source_iri=row.get("source_iri"),
            )
        )
    return identifiers


def _reference_from_review_row(row: dict[str, Any], *, body_override: str | None = None) -> DocumentReferenceOccurrence:
    body = body_override if body_override is not None else str(row.get("reference_body") or row.get("reference_text") or "")
    return DocumentReferenceOccurrence(
        reference_text=str(row.get("reference_text") or ""),
        reference_prefix=str(row.get("reference_prefix") or ""),
        reference_body=body,
        reference_type=str(row.get("reference_type") or "unknown"),
        raw_start=int(row.get("start_char", 0)),
        raw_end=int(row.get("end_char", 0)),
        decision_kind_hint=row.get("decision_kind_hint"),
        court_hint=row.get("court_hint"),
        context=str(row.get("source_context") or ""),
    )


def _clean_reference_body_variants(body: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", body).strip()
    if not normalized:
        return []

    variants = [normalized]

    without_page = _TRAILING_PAGE_PATTERN.sub("", normalized).strip()
    if without_page and without_page not in variants:
        variants.append(without_page)

    without_tail_word = re.sub(r"\bstr\.\s*\d+\s*$", "", normalized, flags=re.IGNORECASE).strip()
    if without_tail_word and without_tail_word not in variants:
        variants.append(without_tail_word)

    return variants


def _infer_likely_sources(row: dict[str, Any], reference: DocumentReferenceOccurrence) -> tuple[tuple[str, ...], str, list[str]]:
    body = _normalize_fuzzy_text(reference.reference_body)
    court_hint = (reference.court_hint or "").lower()
    reasons: list[str] = []
    sources: list[str] = []
    priority = "skip"

    agenda = _agenda_code_from_body(reference.reference_body or "")

    if _CONSTITUTIONAL_PATTERN.search(body) or "ústav" in court_hint:
        sources.append("nalus")
        reasons.append("constitutional-court pattern")
        priority = "high"

    if _UOHS_BODY_PATTERN.search(body) or "úřadu" in court_hint:
        sources.append("uohs")
        reasons.append("uohs-style body or court hint")
        priority = "high"

    if agenda in _NS_AGENDA_CODES or "nejvyššího soudu" in court_hint:
        sources.append("ns")
        reasons.append("ns agenda or court hint")
        priority = "high"

    if agenda in _NSS_AGENDA_CODES or "nejvyššího správního soudu" in court_hint:
        sources.append("nss")
        reasons.append("nss agenda or court hint")
        priority = "high"

    if not sources and _ADMIN_LOWER_CASE_PATTERN.search(body):
        sources.append("nss")
        reasons.append("lower administrative court style case number")
        priority = "medium"

    if not sources and _CIVIL_LOWER_CASE_PATTERN.search(body):
        sources.append("ns")
        reasons.append("lower civil/criminal court style case number")
        priority = "low"

    if not sources and row.get("source") == "uohs":
        sources.append("uohs")
        reasons.append("uohs source document fallback")
        priority = "low"

    deduped_sources = tuple(dict.fromkeys(sources))
    return deduped_sources, priority, reasons


def _build_exact_index(
    identifiers: list[DocumentSelfIdentifier],
) -> dict[str, list[DocumentSelfIdentifier]]:
    index: dict[str, list[DocumentSelfIdentifier]] = defaultdict(list)
    for identifier in identifiers:
        for key in identifier.keys:
            index[key].append(identifier)
    return dict(index)


def _collect_exact_candidates(
    row: dict[str, Any],
    likely_sources: tuple[str, ...],
    exact_index: dict[str, list[DocumentSelfIdentifier]],
) -> list[dict[str, Any]]:
    collected: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for body_variant in _clean_reference_body_variants(str(row.get("reference_body") or row.get("reference_text") or "")):
        reference = _reference_from_review_row(row, body_override=body_variant)
        for match_method, key in _reference_link_keys(reference):
            for identifier in exact_index.get(key, []):
                if likely_sources and identifier.source not in likely_sources:
                    continue
                dedupe_key = (identifier.source, identifier.document_id, match_method, key)
                collected[dedupe_key] = {
                    "source": identifier.source,
                    "document_id": identifier.document_id,
                    "identifier_text": identifier.identifier_text,
                    "identifier_kind": identifier.identifier_kind,
                    "origin": identifier.origin,
                    "document_path": identifier.document_path,
                    "source_iri": identifier.source_iri,
                    "match_method": match_method,
                    "match_key": key,
                    "body_variant": body_variant,
                }
    return sorted(
        collected.values(),
        key=lambda item: (item["source"], item["identifier_text"], item["document_id"]),
    )


def _collect_fuzzy_candidates(
    row: dict[str, Any],
    likely_sources: tuple[str, ...],
    all_identifiers: list[DocumentSelfIdentifier],
    fuzzy_buckets: dict[str, list[dict[str, object]]],
) -> list[dict[str, Any]]:
    if not likely_sources:
        return []

    best_scores: dict[tuple[str, str], dict[str, Any]] = {}
    for body_variant in _clean_reference_body_variants(str(row.get("reference_body") or row.get("reference_text") or "")):
        reference = _reference_from_review_row(row, body_override=body_variant)
        if len(_normalize_fuzzy_text(reference.reference_body)) < 8:
            continue
        for source in likely_sources:
            for candidate_row in fuzzy_buckets.get(source, []):
                identifier = candidate_row.get("identifier")
                if not isinstance(identifier, DocumentSelfIdentifier):
                    continue
                score = _fuzzy_score_reference_to_identifier(reference, candidate_row)
                if score <= 0.0:
                    continue
                row_key = (identifier.source, identifier.document_id)
                payload = {
                    "source": identifier.source,
                    "document_id": identifier.document_id,
                    "identifier_text": identifier.identifier_text,
                    "identifier_kind": identifier.identifier_kind,
                    "origin": identifier.origin,
                    "document_path": identifier.document_path,
                    "source_iri": identifier.source_iri,
                    "score": round(score, 4),
                    "body_variant": body_variant,
                }
                current = best_scores.get(row_key)
                if current is None or float(payload["score"]) > float(current["score"]):
                    best_scores[row_key] = payload

    return sorted(
        best_scores.values(),
        key=lambda item: (-float(item["score"]), item["source"], item["identifier_text"]),
    )[:8]


def _suspicion_label(exact_candidates: list[dict[str, Any]], fuzzy_candidates: list[dict[str, Any]], priority: str) -> str:
    if exact_candidates:
        unique_docs = {item["document_id"] for item in exact_candidates}
        unique_texts = {item["identifier_text"] for item in exact_candidates}
        if len(unique_docs) == 1:
            return "exact_candidate_unique"
        if len(unique_texts) == 1:
            return "exact_candidate_duplicate_copies"
        return "exact_candidate_multiple"
    if fuzzy_candidates:
        top_score = float(fuzzy_candidates[0]["score"])
        if top_score >= 0.9:
            return "strong_fuzzy_candidate"
        return "possible_fuzzy_candidate"
    if priority == "skip":
        return "outside_priority_scope"
    return "no_candidate_found"


def main() -> None:
    args = parse_args()

    review_rows = [
        row
        for row in _load_jsonl(args.reviewed)
        if row.get("target_availability_gold") == "not_in_corpus"
    ]
    identifiers = _load_self_identifiers(args.self_identifiers)
    exact_index = _build_exact_index(identifiers)
    fuzzy_buckets = build_fuzzy_identifier_buckets(identifiers)

    audited_rows: list[dict[str, Any]] = []
    suspicion_counts: Counter[str] = Counter()
    priority_counts: Counter[str] = Counter()
    likely_source_counts: Counter[str] = Counter()

    for row in review_rows:
        reference = _reference_from_review_row(row)
        likely_sources, priority, reasons = _infer_likely_sources(row, reference)
        for source in likely_sources:
            likely_source_counts[source] += 1
        priority_counts[priority] += 1

        exact_candidates = _collect_exact_candidates(row, likely_sources, exact_index)
        fuzzy_candidates = []
        if not exact_candidates and priority in {"high", "medium"}:
            fuzzy_candidates = _collect_fuzzy_candidates(row, likely_sources, identifiers, fuzzy_buckets)

        suspicion = _suspicion_label(exact_candidates, fuzzy_candidates, priority)
        suspicion_counts[suspicion] += 1

        audited_rows.append(
            {
                "task_id": row.get("task_id"),
                "source_document_id": row.get("document_id"),
                "source_document_source": row.get("source"),
                "reference_text": row.get("reference_text"),
                "reference_body": row.get("reference_body"),
                "court_hint": row.get("court_hint"),
                "decision_kind_hint": row.get("decision_kind_hint"),
                "review_note": row.get("review_note"),
                "likely_sources": list(likely_sources),
                "priority": priority,
                "reason_flags": reasons,
                "suspicion": suspicion,
                "exact_candidates": exact_candidates,
                "fuzzy_candidates": fuzzy_candidates,
            }
        )

    audited_rows.sort(
        key=lambda row: (
            {
                "exact_candidate_unique": 0,
                "exact_candidate_duplicate_copies": 1,
                "exact_candidate_multiple": 2,
                "strong_fuzzy_candidate": 3,
                "possible_fuzzy_candidate": 4,
                "no_candidate_found": 5,
                "outside_priority_scope": 6,
            }.get(str(row["suspicion"]), 99),
            str(row["task_id"]),
        )
    )

    result = {
        "reviewed_path": str(Path(args.reviewed).resolve()),
        "self_identifiers_path": str(Path(args.self_identifiers).resolve()),
        "not_in_corpus_row_count": len(review_rows),
        "priority_counts": dict(sorted(priority_counts.items())),
        "likely_source_counts": dict(sorted(likely_source_counts.items())),
        "suspicion_counts": dict(sorted(suspicion_counts.items())),
        "audited_rows": audited_rows,
    }

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# Not-In-Corpus Audit",
        "",
        f"- reviewed: `{result['reviewed_path']}`",
        f"- self-identifiers: `{result['self_identifiers_path']}`",
        f"- audited `not_in_corpus` rows: `{result['not_in_corpus_row_count']}`",
        "",
        "## Priority Counts",
        "",
    ]
    for key, value in sorted(priority_counts.items()):
        md_lines.append(f"- `{key}`: `{value}`")
    md_lines.extend(["", "## Likely Source Counts", ""])
    for key, value in sorted(likely_source_counts.items()):
        md_lines.append(f"- `{key}`: `{value}`")
    md_lines.extend(["", "## Suspicion Counts", ""])
    for key, value in sorted(suspicion_counts.items()):
        md_lines.append(f"- `{key}`: `{value}`")
    md_lines.extend(["", "## Top Re-review Rows", ""])

    example_rows = audited_rows[: args.max_examples]
    for row in example_rows:
        md_lines.append(
            f"- `{row['task_id']}` | `{row['reference_text']}` | priority=`{row['priority']}` "
            f"| likely={row['likely_sources']} | suspicion=`{row['suspicion']}`"
        )
        if row["exact_candidates"]:
            for candidate in row["exact_candidates"][:4]:
                md_lines.append(
                    f"  exact -> `{candidate['source']}:{candidate['document_id']}` "
                    f"`{candidate['identifier_text']}` via `{candidate['match_method']}`"
                )
        elif row["fuzzy_candidates"]:
            for candidate in row["fuzzy_candidates"][:3]:
                md_lines.append(
                    f"  fuzzy -> `{candidate['source']}:{candidate['document_id']}` "
                    f"`{candidate['identifier_text']}` score=`{candidate['score']}`"
                )
        else:
            md_lines.append(f"  note -> `{row['review_note']}`")

    md_path = Path(args.md_output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print("--- NOT-IN-CORPUS AUDIT COMPLETE ---")
    print(f"Audited rows:         {len(review_rows)}")
    print(f"JSON output:          {json_path}")
    print(f"Markdown output:      {md_path}")
    print(f"Suspicion counts:     {dict(sorted(suspicion_counts.items()))}")


if __name__ == "__main__":
    main()
