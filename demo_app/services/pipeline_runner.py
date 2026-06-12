"""Service layer that runs the thesis extraction pipeline for the demo app."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from demo_app.config import DEMO_METADATA_PATH
from demo_app.services.linker_cache import build_uploaded_reference_llm_tasks, link_uploaded_references
from demo_app.services.span_builder import build_render_segments
from demo_app.services.view_model import (
    build_document_items_with_metadata,
    build_law_alias_index,
    build_law_items,
    build_stats,
)
from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import extract_citation_occurrences, occurrences_to_resolved_and_anomalies
from src.document_metadata import load_document_dates_index, load_law_timelines
from src.document_reference_extractor import extract_document_references
from src.production_paths import PRODUCTION_CANONICAL_LAWS_PATH

ProgressCallback = Callable[[str, int], None]


@lru_cache(maxsize=1)
def _canonical_law_names() -> dict[str, str]:
    path = Path(PRODUCTION_CANONICAL_LAWS_PATH)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for law_id, value in raw.items():
            if isinstance(value, list) and value:
                first = next((item for item in value if isinstance(item, str) and item.strip()), None)
                if first:
                    out[law_id] = first
            elif isinstance(value, str) and value.strip():
                out[law_id] = value
    return out


@lru_cache(maxsize=1)
def _runtime_aliases() -> dict[str, object]:
    aliases, _ = load_runtime_aliases()
    return aliases


@lru_cache(maxsize=1)
def _document_dates_index():
    return load_document_dates_index(str(DEMO_METADATA_PATH))


@lru_cache(maxsize=1)
def _law_timelines() -> dict[str, dict]:
    return load_law_timelines()


def _law_ai_entry_id(document_name: str, row: dict[str, Any]) -> str | None:
    raw_span = row.get("raw_span") or {}
    raw_start = raw_span.get("start")
    raw_end = raw_span.get("end")
    citation_type = row.get("citation_type")
    if raw_start is None or raw_end is None or not citation_type:
        return None
    return f"uploaded::{document_name}::{int(raw_start)}:{int(raw_end)}:{citation_type}"


def _apply_law_ai_results(
    law_occurrences: list[dict[str, Any]],
    document_name: str,
    ai_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not ai_rows:
        return [dict(row) for row in law_occurrences]

    ai_by_entry_id = {
        str(row.get("entry_id")): row
        for row in ai_rows
        if isinstance(row, dict) and row.get("entry_id")
    }

    enriched_rows: list[dict[str, Any]] = []
    for row in law_occurrences:
        current = dict(row)
        entry_id = _law_ai_entry_id(document_name, current)
        ai_row = ai_by_entry_id.get(entry_id or "")
        if ai_row:
            result = ai_row.get("result") if isinstance(ai_row.get("result"), dict) else {}
            classification = str(result.get("classification") or current.get("predicted_classification") or "unknown")
            resolved_law_id = result.get("resolved_law_id")
            resolved_law_id_str = (
                str(resolved_law_id)
                if isinstance(resolved_law_id, str) and resolved_law_id.strip()
                else None
            )
            allowed_candidates = [
                value for value in (current.get("candidate_law_ids") or []) if isinstance(value, str)
            ]
            if (
                classification == "czech_resolved"
                and resolved_law_id_str is not None
                and allowed_candidates
                and resolved_law_id_str not in allowed_candidates
            ):
                current["predicted_classification"] = current.get("predicted_classification") or "czech_unresolved"
                current["resolved_law_id"] = current.get("resolved_law_id")
                current["confidence"] = min(float(result.get("confidence") or 0.0), 0.5)
                current["resolver_stage"] = "BRL review rejected"
                current["ai_rationale"] = (
                    "BRL result rejected outside occurrence shortlist. "
                    + str(result.get("rationale") or "")
                ).strip()
            else:
                current["predicted_classification"] = classification
                current["resolved_law_id"] = resolved_law_id_str
                current["confidence"] = float(result.get("confidence") or 0.0)
                current["resolver_stage"] = "BRL review"
            current["ai_assisted"] = True
            current["ai_model"] = ai_row.get("model")
            if "ai_rationale" not in current:
                current["ai_rationale"] = result.get("rationale")
        enriched_rows.append(current)
    return enriched_rows


def prepare_demo_state(
    text: str,
    document_name: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, object]:
    def report(phase: str, progress: int) -> None:
        if progress_callback is not None:
            progress_callback(phase, progress)

    report("extract_local_aliases", 52)
    local_aliases = extract_local_aliases(text)
    report("load_runtime_aliases", 56)
    runtime_aliases = _runtime_aliases()
    report("extract_document_references", 61)
    document_references = extract_document_references(text)
    report("extract_law_occurrences", 68)
    law_occurrence_objs = extract_citation_occurrences(
        text=text,
        local_aliases=local_aliases,
        global_aliases=runtime_aliases,
        document_metadata=None,
        law_timelines=_law_timelines(),
    )

    report("derive_law_anomalies", 73)
    law_occurrences = [occ.to_dict() for occ in law_occurrence_objs]
    _, law_anomalies = occurrences_to_resolved_and_anomalies(
        law_occurrence_objs,
        text,
        include_low_confidence_anomalies=True,
        document_id=document_name,
        document_source="uploaded",
    )

    report("link_document_references", 79)
    document_occurrences = [occ.to_dict() for occ in document_references]
    linked_by_index = link_uploaded_references(document_references, document_name)
    report("build_document_ai_tasks", 84)
    document_ai_tasks = build_uploaded_reference_llm_tasks(
        document_references,
        document_name,
        linked_by_index,
    )
    report("load_document_metadata", 88)
    document_metadata_index = _document_dates_index()
    linked_metadata_by_document = {
        (str(link.get("target_source") or ""), str(link.get("target_document_id") or "")): (
            document_metadata_index.get(
                (str(link.get("target_source") or ""), str(link.get("target_document_id") or ""))
            )
            or {}
        )
        for link in linked_by_index.values()
    }
    for task in document_ai_tasks:
        for candidate in task.get("candidate_targets", []):
            key = (str(candidate.get("target_source") or ""), str(candidate.get("target_document_id") or ""))
            if key not in linked_metadata_by_document:
                linked_metadata_by_document[key] = document_metadata_index.get(key) or {}

    report("build_law_alias_index", 92)
    law_alias_index = build_law_alias_index(local_aliases, runtime_aliases)
    return {
        "document_name": document_name,
        "text": text,
        "law_occurrences": law_occurrences,
        "law_anomalies": law_anomalies,
        "document_occurrences": document_occurrences,
        "linked_by_index": linked_by_index,
        "linked_metadata_by_document": linked_metadata_by_document,
        "document_ai_tasks": document_ai_tasks,
        "law_alias_index": law_alias_index,
    }


def build_demo_result(
    state: dict[str, object],
    *,
    ai_rows: list[dict[str, Any]] | None = None,
    document_ai_rows: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    text = str(state["text"])
    document_name = str(state["document_name"])
    law_alias_index = dict(state["law_alias_index"])
    law_occurrences = _apply_law_ai_results(
        list(state["law_occurrences"]),
        document_name,
        ai_rows,
    )
    law_items = build_law_items(text, law_occurrences, _canonical_law_names(), law_alias_index)
    document_items = build_document_items_with_metadata(
        list(state["document_occurrences"]),
        dict(state["linked_by_index"]),
        dict(state["linked_metadata_by_document"]),
        ai_rows=document_ai_rows,
    )
    all_items = sorted([*law_items, *document_items], key=lambda item: (item.start, item.end, item.kind))

    return {
        "document_name": document_name,
        "text": text,
        "law_items": law_items,
        "document_items": document_items,
        "all_items": all_items,
        "render_segments": build_render_segments(text, all_items),
        "stats": build_stats(law_items, document_items),
        "debug": {
            "law_occurrences": law_occurrences,
            "law_anomalies": list(state["law_anomalies"]),
            "document_occurrences": list(state["document_occurrences"]),
            "linked_document_references": dict(state["linked_by_index"]),
            "law_ai_rows": list(ai_rows or []),
            "document_ai_rows": list(document_ai_rows or []),
            "document_ai_tasks": list(state.get("document_ai_tasks") or []),
        },
    }


def run_demo_pipeline(text: str, document_name: str) -> dict[str, object]:
    state = prepare_demo_state(text=text, document_name=document_name)
    return build_demo_result(state)
