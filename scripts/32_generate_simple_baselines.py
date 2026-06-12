#!/usr/bin/env python3
"""
Generate lightweight baseline predictions for the joint reference benchmark.

These baselines are intentionally limited. They are evaluation artifacts used to
show what the full Structured Precision Pipeline adds beyond local pattern
matching and strict identifier lookup.
"""

from __future__ import annotations

import json
import os
import re
import sys
import gzip
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alias_loader import load_runtime_aliases
from src.document_reference_extractor import extract_document_references
from src.document_reference_linker import _compact_key
from src.production_paths import (
    PRODUCTION_AUDITED_ALIASES_PATH,
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH,
    PRODUCTION_GLOBAL_ALIASES_PATH,
    PRODUCTION_SEED_ALIASES_PATH,
)


LAW_GOLD_PATH = Path("data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl")
DOCREF_GOLD_PATH = Path(
    "data/annotations/joint_reference_gold_v1/document_reference_gold/joint_document_reference_gold_v1.jsonl"
)
OUTPUT_DIR = Path("data/annotations/joint_reference_gold_v1/simple_baselines")

LAW_PREDICTIONS_PATH = OUTPUT_DIR / "simple_law_baseline_predictions.jsonl"
DOCREF_PREDICTIONS_PATH = OUTPUT_DIR / "simple_docref_baseline_predictions.jsonl"
DOCREF_LINKS_PATH = OUTPUT_DIR / "simple_docref_baseline_links.jsonl"
SUMMARY_PATH = OUTPUT_DIR / "simple_baseline_generation_summary.json"

LAW_ID_RE = re.compile(r"(?<!\d)(?P<law_id>\d{1,4})/(?P<year>\d{2}|\d{4})\s*Sb\.", re.IGNORECASE)
SECTION_RE = re.compile(r"(?<![\w§])(?P<anchor>§{1,2}\s*(?P<number>\d+[a-zA-Z]?))")
ARTICLE_RE = re.compile(r"(?<!\w)(?P<anchor>(?:čl\.|článek)\s*(?P<number>\d+[a-zA-Z]?))", re.IGNORECASE)
WORD_CHAR_RE = re.compile(r"[0-9A-Za-zÁ-Žá-ž_]")
GENERIC_ALIAS_BLOCKLIST = {
    "zákon",
    "zákona",
    "zákonem",
    "zákoně",
    "zákonu",
    "zákony",
    "vyhláška",
    "vyhlášky",
    "nařízení",
    "předpis",
    "předpisu",
    "předpisy",
    "řád",
    "řádu",
    "řádem",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_match(text: str) -> str:
    return _normalize_spaces(text).lower()


def _normalize_law_id(text: str) -> str | None:
    match = LAW_ID_RE.fullmatch(_normalize_spaces(text))
    if not match:
        return None
    year = match.group("year")
    if len(year) == 2:
        year_int = int(year)
        year = f"20{year}" if year_int <= 30 else f"19{year}"
    return f"{int(match.group('law_id'))}/{year} Sb."


def _payload_law_ids(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        law_id = payload.get("law_id")
        if isinstance(law_id, str) and law_id.strip():
            return [law_id]
        law_ids = payload.get("law_ids")
        if isinstance(law_ids, list):
            return [str(item).strip() for item in law_ids if str(item).strip()]
    return []


def _alias_is_suitable(alias: str) -> bool:
    normalized = _normalize_for_match(alias).strip(".,;: ")
    if normalized in GENERIC_ALIAS_BLOCKLIST:
        return False
    if len(normalized) < 3:
        return False
    if " " not in normalized and "." not in normalized and len(normalized) < 4:
        return False
    return True


def _build_alias_entries() -> list[tuple[str, str]]:
    alias_map, _source = load_runtime_aliases(
        audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
        global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
        seeded_path=PRODUCTION_SEED_ALIASES_PATH,
        canonical_laws_path=PRODUCTION_CANONICAL_LAWS_PATH,
    )
    entries: list[tuple[str, str]] = []
    for alias, payload in alias_map.items():
        if not isinstance(alias, str) or not _alias_is_suitable(alias):
            continue
        law_ids = _payload_law_ids(payload)
        if len(set(law_ids)) != 1:
            continue
        normalized_alias = _normalize_for_match(alias)
        if normalized_alias:
            entries.append((normalized_alias, law_ids[0]))
    entries.sort(key=lambda item: (-len(item[0]), item[0]))
    return entries


def _contains_alias(normalized_window: str, normalized_alias: str) -> bool:
    start = 0
    while True:
        idx = normalized_window.find(normalized_alias, start)
        if idx < 0:
            return False
        before = normalized_window[idx - 1] if idx > 0 else " "
        after_idx = idx + len(normalized_alias)
        after = normalized_window[after_idx] if after_idx < len(normalized_window) else " "
        if not WORD_CHAR_RE.match(before) and not WORD_CHAR_RE.match(after):
            return True
        start = idx + 1


def _resolve_law_from_local_window(text: str, start: int, end: int, aliases: list[tuple[str, str]]) -> tuple[str | None, str]:
    window = text[max(0, start - 80) : min(len(text), end + 260)]
    for match in LAW_ID_RE.finditer(window):
        law_id = _normalize_law_id(match.group("law_id") + "/" + match.group("year") + " Sb.")
        if law_id:
            return law_id, "Simple baseline: nearby law-id"

    normalized_window = _normalize_for_match(window)
    for normalized_alias, law_id in aliases:
        if _contains_alias(normalized_window, normalized_alias):
            return law_id, "Simple baseline: nearby exact alias"

    return None, "Simple baseline: unresolved"


def _law_prediction(
    *,
    document: dict[str, Any],
    text: str,
    start: int,
    end: int,
    citation_type: str,
    number: str | None,
    resolved_law_id: str | None,
    resolver_stage: str,
) -> dict[str, Any]:
    return {
        "document_id": str(document["document_id"]),
        "source": str(document.get("source") or ""),
        "document_path": str(document["document_path"]),
        "citation_text": _normalize_spaces(text[start:end]),
        "citation_type": citation_type,
        "raw_start": start,
        "raw_end": end,
        "resolved_law_id": resolved_law_id,
        "predicted_classification": "czech_resolved" if resolved_law_id else "czech_unresolved",
        "resolver_stage": resolver_stage,
        "confidence": 0.95 if resolved_law_id else 0.0,
        "parsed_detail": {"number": number} if number else {},
    }


def generate_law_baseline_predictions(law_gold_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = _build_alias_entries()
    predictions: list[dict[str, Any]] = []

    for document in law_gold_docs:
        document_path = Path(str(document["document_path"]))
        text = document_path.read_text(encoding="utf-8")
        doc_predictions: list[dict[str, Any]] = []

        for match in SECTION_RE.finditer(text):
            law_id, stage = _resolve_law_from_local_window(text, match.start(), match.end(), aliases)
            doc_predictions.append(
                _law_prediction(
                    document=document,
                    text=text,
                    start=match.start(),
                    end=match.end(),
                    citation_type="section",
                    number=match.group("number"),
                    resolved_law_id=law_id,
                    resolver_stage=stage,
                )
            )

        for match in ARTICLE_RE.finditer(text):
            law_id, stage = _resolve_law_from_local_window(text, match.start(), match.end(), aliases)
            doc_predictions.append(
                _law_prediction(
                    document=document,
                    text=text,
                    start=match.start(),
                    end=match.end(),
                    citation_type="article",
                    number=match.group("number"),
                    resolved_law_id=law_id,
                    resolver_stage=stage,
                )
            )

        for match in LAW_ID_RE.finditer(text):
            law_id = _normalize_law_id(match.group(0))
            doc_predictions.append(
                _law_prediction(
                    document=document,
                    text=text,
                    start=match.start(),
                    end=match.end(),
                    citation_type="other_normative",
                    number=None,
                    resolved_law_id=law_id,
                    resolver_stage="Simple baseline: direct law-id",
                )
            )

        seen: set[tuple[int, str]] = set()
        for row in sorted(doc_predictions, key=lambda item: (item["raw_start"], item["raw_end"], item["citation_type"])):
            key = (int(row["raw_start"]), str(row["citation_type"]))
            if key in seen:
                continue
            seen.add(key)
            predictions.append(row)

    return predictions


def _document_rows_by_path(docref_gold_rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    by_path: dict[str, dict[str, Any]] = {}
    for row in docref_gold_rows:
        path = str(row["document_path"])
        by_path.setdefault(path, row)
    return sorted(by_path.items())


def generate_docref_baseline_predictions(docref_gold_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for document_path, sample_row in _document_rows_by_path(docref_gold_rows):
        text = Path(document_path).read_text(encoding="utf-8")
        for occurrence in extract_document_references(text):
            if not occurrence.reference_prefix:
                continue
            row = occurrence.to_dict()
            row.update(
                {
                    "document_id": str(sample_row["document_id"]),
                    "source": str(sample_row.get("source") or ""),
                    "document_path": document_path,
                }
            )
            predictions.append(row)
    predictions.sort(key=lambda item: (item["document_path"], item["raw_start"], item["raw_end"]))
    return predictions


def _load_simple_metadata_index() -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    with gzip.open(PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            judicate_name = str(row.get("judicate_name") or "").strip()
            if not judicate_name:
                continue
            key = _compact_key(judicate_name)
            if not key:
                continue
            index.setdefault(key, []).append(row)
    return index


def generate_strict_docref_links(docref_predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metadata_index = _load_simple_metadata_index()

    links: list[dict[str, Any]] = []
    for row in docref_predictions:
        reference_body = str(row.get("reference_body") or row.get("reference_text") or "")
        link_key = _compact_key(reference_body)
        if not link_key:
            continue
        source_path = str(Path(str(row["document_path"])).resolve())
        candidates = [
            candidate
            for candidate in metadata_index.get(link_key, [])
            if str(Path(str(candidate.get("processed_rel_path") or "")).resolve()) != source_path
        ]
        target_ids = {str(candidate.get("document_id") or "") for candidate in candidates}
        if len(target_ids) != 1:
            continue
        target = sorted(candidates, key=lambda item: str(item.get("processed_rel_path") or ""))[0]
        links.append(
            {
                "source_document_source": str(row.get("source") or ""),
                "source_document_id": str(row["document_id"]),
                "source_document_path": str(row["document_path"]),
                "target_source": str(target.get("source") or ""),
                "target_document_id": str(target.get("document_id") or ""),
                "target_document_path": str(Path(str(target.get("processed_rel_path") or "")).resolve()),
                "reference_text": row.get("reference_text"),
                "reference_prefix": row.get("reference_prefix"),
                "reference_body": row.get("reference_body"),
                "reference_type": row.get("reference_type"),
                "raw_start": int(row["raw_start"]),
                "raw_end": int(row["raw_end"]),
                "link_key": link_key,
                "link_method": "simple_baseline_body_exact",
                "target_match_scope": "exact_decision",
                "target_proceeding_key": None,
                "target_group_size": len(candidates),
                "decision_kind_hint": row.get("decision_kind_hint"),
                "court_hint": row.get("court_hint"),
            }
        )
    links.sort(key=lambda item: (item["source_document_id"], item["raw_start"], item["target_document_id"]))
    return links


def main() -> None:
    law_gold_docs = _load_jsonl(LAW_GOLD_PATH)
    docref_gold_rows = _load_jsonl(DOCREF_GOLD_PATH)

    law_predictions = generate_law_baseline_predictions(law_gold_docs)
    docref_predictions = generate_docref_baseline_predictions(docref_gold_rows)
    docref_links = generate_strict_docref_links(docref_predictions)

    _write_jsonl(LAW_PREDICTIONS_PATH, law_predictions)
    _write_jsonl(DOCREF_PREDICTIONS_PATH, docref_predictions)
    _write_jsonl(DOCREF_LINKS_PATH, docref_links)
    _write_json(
        SUMMARY_PATH,
        {
            "law_gold_documents": len(law_gold_docs),
            "law_predictions": len(law_predictions),
            "docref_gold_documents_with_positive_rows": len(_document_rows_by_path(docref_gold_rows)),
            "docref_predictions": len(docref_predictions),
            "docref_strict_links": len(docref_links),
            "outputs": {
                "law_predictions": str(LAW_PREDICTIONS_PATH),
                "docref_predictions": str(DOCREF_PREDICTIONS_PATH),
                "docref_links": str(DOCREF_LINKS_PATH),
            },
        },
    )

    print(f"Wrote law baseline predictions: {LAW_PREDICTIONS_PATH} ({len(law_predictions)} rows)")
    print(f"Wrote document-reference baseline predictions: {DOCREF_PREDICTIONS_PATH} ({len(docref_predictions)} rows)")
    print(f"Wrote document-reference baseline links: {DOCREF_LINKS_PATH} ({len(docref_links)} rows)")


if __name__ == "__main__":
    main()
