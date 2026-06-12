"""Helpers for linking uploaded document references against the corpus snapshot."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from demo_app.config import DEMO_METADATA_PATH, DEMO_SELF_ID_SNAPSHOT
from src.document_metadata import load_document_dates_index
from src.document_reference_extractor import DocumentReferenceOccurrence
from src.document_reference_linker import (
    DocumentSelfIdentifier,
    build_fuzzy_identifier_buckets,
    build_reference_candidate_targets,
    _identifier_keys,
    _reference_link_keys,
    link_document_reference,
)


_LOWER_COURT_HINT_PATTERN = re.compile(
    r"(?:vrchn[ií]|krajsk|městsk|mestsk|okresn|obvodn)",
    re.IGNORECASE,
)
_UOHS_REFERENCE_PATTERN = re.compile(
    r"(?:^|[\s./-])(?:úohs|uohs)(?:[-/\s]|$)",
    re.IGNORECASE,
)
_UOHS_HINT_PATTERN = re.compile(
    r"(?:předsed[ay]?\s+úřadu|predsed[ay]?\s+uradu|úohs|uohs)",
    re.IGNORECASE,
)


def _should_skip_uploaded_presence_check(reference: DocumentReferenceOccurrence) -> bool:
    court_hint = (reference.court_hint or "").strip()
    if court_hint and _LOWER_COURT_HINT_PATTERN.search(court_hint):
        return True

    reference_body = (reference.reference_body or "").strip()
    reference_text = (reference.reference_text or "").strip()
    if _UOHS_REFERENCE_PATTERN.search(reference_body) or _UOHS_REFERENCE_PATTERN.search(reference_text):
        return True

    if court_hint and _UOHS_HINT_PATTERN.search(court_hint):
        return True

    return False


@lru_cache(maxsize=1)
def _snapshot_path() -> Path:
    return DEMO_SELF_ID_SNAPSHOT


@lru_cache(maxsize=1)
def _metadata_index() -> dict[tuple[str, str], dict]:
    if not DEMO_METADATA_PATH.exists():
        return {}
    return load_document_dates_index(str(DEMO_METADATA_PATH))


def _identifier_from_row(row: dict) -> DocumentSelfIdentifier:
    source = str(row["source"])
    document_id = str(row["document_id"])
    metadata = _metadata_index().get((source, document_id)) or {}
    return DocumentSelfIdentifier(
        document_id=document_id,
        document_path=row["document_path"],
        source=source,
        identifier_text=row["identifier_text"],
        identifier_kind=row["identifier_kind"],
        origin=row["origin"],
        keys=tuple(row.get("keys", [])),
        source_iri=row.get("source_iri") or metadata.get("judicate_iri"),
        decision_date=row.get("decision_date") or metadata.get("decision_date"),
        decision_date_iso=row.get("decision_date_iso") or metadata.get("decision_date_iso"),
        decision_year=row.get("decision_year") or metadata.get("decision_year"),
        decision_date_precision=(
            row.get("decision_date_precision") or metadata.get("decision_date_precision")
        ),
        judicate_name=row.get("judicate_name") or metadata.get("judicate_name"),
        blob_name=row.get("blob_name") or metadata.get("blob_name"),
    )


@lru_cache(maxsize=16)
def _load_candidate_index_for_keyset(
    required_keys: tuple[str, ...],
) -> dict[str, list[DocumentSelfIdentifier]]:
    if not required_keys:
        return {}

    snapshot_path = _snapshot_path()
    if not snapshot_path.exists():
        return {}

    required_key_set = set(required_keys)
    index: dict[str, list[DocumentSelfIdentifier]] = {key: [] for key in required_keys}
    with snapshot_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            effective_keys = set(row.get("keys", []))
            source = row.get("source")
            identifier_text = row.get("identifier_text")
            if isinstance(source, str) and isinstance(identifier_text, str):
                effective_keys.update(_identifier_keys(identifier_text, source))
            row_keys = tuple(key for key in effective_keys if key in required_key_set)
            if not row_keys:
                continue
            identifier = _identifier_from_row(row)
            for key in row_keys:
                index.setdefault(key, []).append(identifier)
    return index


@lru_cache(maxsize=1)
def _load_all_identifiers() -> tuple[DocumentSelfIdentifier, ...]:
    snapshot_path = _snapshot_path()
    if not snapshot_path.exists():
        return ()

    identifiers: list[DocumentSelfIdentifier] = []
    with snapshot_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            identifiers.append(_identifier_from_row(row))
    return tuple(identifiers)


@lru_cache(maxsize=1)
def _load_fuzzy_buckets() -> dict[str, list[dict[str, object]]]:
    return build_fuzzy_identifier_buckets(list(_load_all_identifiers()))


def link_uploaded_references(
    references: Iterable[DocumentReferenceOccurrence],
    source_document_id: str,
) -> dict[int, dict]:
    refs = list(references)
    required_keys = tuple(sorted({
        key
        for reference in refs
        for _, key in _reference_link_keys(reference)
        if key
    }))
    candidate_index = _load_candidate_index_for_keyset(required_keys)
    linked_by_position: dict[int, dict] = {}
    for idx, reference in enumerate(refs):
        linked = link_document_reference(
            source_document_path=f"/virtual_uploads/{source_document_id}",
            source_document_source="uploaded",
            source_document_id=source_document_id,
            reference=reference,
            self_id_index=candidate_index,
        )
        if linked is not None:
            linked_by_position[idx] = linked.to_dict()
    return linked_by_position


def build_uploaded_reference_llm_tasks(
    references: Iterable[DocumentReferenceOccurrence],
    source_document_id: str,
    linked_by_position: dict[int, dict],
    *,
    max_candidates: int = 12,
) -> list[dict]:
    refs = list(references)
    unresolved_positions = [idx for idx in range(len(refs)) if idx not in linked_by_position]
    required_keys = tuple(
        sorted(
            {
                key
                for idx in unresolved_positions
                for _, key in _reference_link_keys(refs[idx])
                if key
            }
        )
    )
    candidate_index = _load_candidate_index_for_keyset(required_keys)
    tasks: list[dict] = []
    all_identifiers = list(_load_all_identifiers())
    fuzzy_buckets = _load_fuzzy_buckets()

    for idx in unresolved_positions:
        reference = refs[idx]
        source_path = f"/virtual_uploads/{source_document_id}"
        candidate_info = build_reference_candidate_targets(
            reference=reference,
            source_document_path=source_path,
            source_document_source="uploaded",
            self_id_index=candidate_index,
            all_identifiers=all_identifiers,
            fuzzy_buckets=fuzzy_buckets,
            max_candidates=max_candidates,
        )
        if candidate_info.get("llm_route") == "filtered_out_of_scope":
            continue
        candidate_targets = list(candidate_info["candidate_targets"])

        if candidate_targets:
            task = {
                "entry_id": f"uploaded-docref::{source_document_id}::{idx}",
                "reference_index": idx,
                "llm_route": candidate_info["llm_route"],
                "source": "uploaded",
                "document_id": source_document_id,
                "document_path": source_path,
                "target_reference": reference.reference_text,
                "reference_body": reference.reference_body,
                "reference_type_hint": reference.reference_type,
                "context_block": reference.context,
                "candidate_targets": candidate_targets,
            }
        else:
            if _should_skip_uploaded_presence_check(reference):
                continue
            task = {
                "entry_id": f"uploaded-docref::{source_document_id}::{idx}",
                "reference_index": idx,
                "llm_route": "extraction_presence_check",
                "source": "uploaded",
                "document_id": source_document_id,
                "document_path": source_path,
                "candidate_text": reference.reference_text,
                "target_reference": reference.reference_text,
                "reference_body": reference.reference_body,
                "reference_type_hint": reference.reference_type,
                "context_block": reference.context,
                "candidate_targets": [],
            }
        tasks.append(task)

    return tasks
