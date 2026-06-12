"""App-side wrapper for document-reference BRL review.

This module is part of the Bounded Review Layer (BRL) in the thesis
terminology. It keeps the implementation name ``docref_llm`` because the
wrapper specifically calls the configured Gemini model for routed hard cases.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from functools import lru_cache
from typing import Any, Callable

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field
from src.document_reference_llm_prompts import prompt_for_entry, system_prompt
from typing_extensions import Literal


ProgressCallback = Callable[[dict[str, Any]], None]


class LinkDisambiguationDecision(BaseModel):
    decision: Literal["exact_target", "ambiguous", "unresolved"]
    target_document_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    rationale: str = Field(min_length=1, max_length=300)


class LinkRecoveryDecision(BaseModel):
    decision: Literal["exact_target", "same_proceeding", "unresolved"]
    target_document_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    rationale: str = Field(min_length=1, max_length=300)


class ExtractionDecision(BaseModel):
    decision: Literal["is_reference", "not_reference", "uncertain"]
    reference_type: Literal["spisova_znacka", "cislo_jednaci", "unknown"]
    normalized_body: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    rationale: str = Field(min_length=1, max_length=300)


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set.")
    return genai.Client()


def enrich_document_reference_tasks(
    tasks: list[dict[str, Any]],
    *,
    model: str | None = None,
    prompt_version: str = "v2",
    timeout_ms: int = 10000,
    retries: int = 3,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    client = _client()
    model_name = model or os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    schema_by_route = {
        "link_disambiguation": LinkDisambiguationDecision,
        "link_normalization_or_target_recovery": LinkRecoveryDecision,
        "extraction_presence_check": ExtractionDecision,
    }

    rows: list[dict[str, Any]] = []
    for entry in tasks:
        route, prompt = prompt_for_entry(entry, prompt_version)
        schema = schema_by_route[route]
        result: dict[str, Any]
        try:
            response = None
            last_exc = None
            for attempt in range(retries + 1):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[
                            {"role": "user", "parts": [{"text": system_prompt(route, prompt_version)}]},
                            {"role": "user", "parts": [{"text": prompt}]},
                        ],
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": schema,
                            "temperature": 0.0,
                            "http_options": genai_types.HttpOptions(timeout=timeout_ms),
                        },
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= retries:
                        break
                    time.sleep(min(2.0, 0.5 * (attempt + 1)))

            if response is None:
                result = {
                    "decision": "unresolved" if route != "extraction_presence_check" else "uncertain",
                    "confidence": 0.0,
                    "rationale": f"api_error: {last_exc}",
                }
            else:
                parsed = getattr(response, "parsed", None)
                if parsed is not None:
                    result = parsed.model_dump()
                else:
                    result = json.loads(getattr(response, "text", "") or "{}")
        except Exception as exc:
            result = {
                "decision": "unresolved" if route != "extraction_presence_check" else "uncertain",
                "confidence": 0.0,
                "rationale": f"api_error: {exc}",
            }

        candidate_ids = {
            item.get("target_document_id")
            for item in entry.get("candidate_targets", [])
            if item.get("target_document_id")
        }
        if route in {"link_disambiguation", "link_normalization_or_target_recovery"}:
            chosen = result.get("target_document_id")
            if chosen is not None and chosen not in candidate_ids:
                result = {
                    "decision": "unresolved",
                    "target_document_id": None,
                    "confidence": min(float(result.get("confidence", 0.0) or 0.0), 0.5),
                    "rationale": "post_validation_rejected_target_outside_candidates",
                }
            if result.get("decision") in {"ambiguous", "unresolved"}:
                result["target_document_id"] = None

        row = {
            "entry_id": entry.get("entry_id"),
            "reference_index": entry.get("reference_index"),
            "llm_route": route,
            "source": entry.get("source"),
            "document_id": entry.get("document_id"),
            "document_path": entry.get("document_path"),
            "target_reference": entry.get("target_reference"),
            "reference_body": entry.get("reference_body"),
            "reference_type_hint": entry.get("reference_type_hint"),
            "prompt": prompt,
            "result": result,
            "candidate_targets": entry.get("candidate_targets", []),
            "timestamp_unix": int(time.time()),
            "model": model_name,
            "prompt_version": prompt_version,
        }
        rows.append(row)
        if progress_callback is not None:
            progress_callback(row)

    return _apply_extraction_presence_consistency(rows)


def _normalize_reference_body(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.upper()
    normalized = re.sub(r"[^A-Z0-9]+", "", normalized)
    return normalized


def _apply_extraction_presence_consistency(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("llm_route") != "extraction_presence_check":
            continue
        doc_id = str(row.get("document_id") or "")
        body_key = _normalize_reference_body(row.get("reference_body") or row.get("target_reference"))
        if not doc_id or not body_key:
            continue
        groups[(doc_id, body_key)].append(row)

    for group_rows in groups.values():
        positive = [
            row for row in group_rows
            if isinstance(row.get("result"), dict) and row["result"].get("decision") == "is_reference"
        ]
        if not positive:
            continue

        propagated_type = next(
            (
                row["result"].get("reference_type")
                for row in positive
                if row["result"].get("reference_type") in {"spisova_znacka", "cislo_jednaci"}
            ),
            None,
        )
        if propagated_type is None:
            propagated_type = next(
                (
                    row.get("reference_type_hint")
                    for row in group_rows
                    if row.get("reference_type_hint") in {"spisova_znacka", "cislo_jednaci"}
                ),
                "unknown",
            )

        for row in group_rows:
            result = row.get("result")
            if not isinstance(result, dict):
                continue
            if result.get("decision") == "is_reference":
                continue
            row["result"] = {
                **result,
                "decision": "is_reference",
                "reference_type": propagated_type,
                "normalized_body": result.get("normalized_body") or str(row.get("reference_body") or ""),
                "confidence": max(float(result.get("confidence") or 0.0), 0.55),
                "rationale": "duplicate_body_consistency_promoted_from_positive_occurrence",
            }
    return rows
