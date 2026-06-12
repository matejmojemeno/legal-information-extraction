"""App-side wrapper for law-reference BRL review.

This module is part of the Bounded Review Layer (BRL) in the thesis
terminology. It keeps the implementation name ``law_llm`` because the wrapper
specifically calls the configured Gemini model for routed hard cases.
"""

from __future__ import annotations

import importlib
import json
import os
import time
from functools import lru_cache
from typing import Any, Callable

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from src.alias_loader import load_runtime_aliases
from src.production_paths import (
    PRODUCTION_AUDITED_ALIASES_PATH,
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_GLOBAL_ALIASES_PATH,
    PRODUCTION_SEED_ALIASES_PATH,
)


ProgressCallback = Callable[[dict[str, Any]], None]


class ResolutionDecision(BaseModel):
    classification: str = Field()
    resolved_law_id: str | None = Field(default=None)
    missing_candidate_guess_raw: str = Field(default="")
    missing_candidate_guess_normalized: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    rationale: str = Field(min_length=1, max_length=300)


@lru_cache(maxsize=1)
def _script_module():
    return importlib.import_module("scripts.06_llm_resolve_references")


def _primary_alias_map(alias_map: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for alias, payload in alias_map.items():
        if not isinstance(alias, str):
            continue
        if isinstance(payload, str):
            out[alias] = payload
            continue
        if isinstance(payload, dict):
            law_id = payload.get("law_id")
            if isinstance(law_id, str):
                out[alias] = law_id
                continue
            law_ids = payload.get("law_ids")
            if isinstance(law_ids, list):
                first = next((item for item in law_ids if isinstance(item, str) and item.strip()), None)
                if first:
                    out[alias] = first
    return out


def _build_zero_candidate_prompt(module, target_reference: str, context_block: str) -> str:
    return module._build_prompt(target_reference, context_block, [])


def _coerce_result_from_response(response: Any) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump()
        if isinstance(parsed, dict):
            return parsed
    text = getattr(response, "text", "") or "{}"
    try:
        return json.loads(text)
    except Exception:
        return {
            "classification": "unresolved",
            "resolved_law_id": None,
            "missing_candidate_guess_raw": "",
            "missing_candidate_guess_normalized": "",
            "confidence": 0.0,
            "rationale": "response_parse_error",
        }


@lru_cache(maxsize=1)
def _retrieval_resources():
    module = _script_module()
    canonical_laws = module._load_json(PRODUCTION_CANONICAL_LAWS_PATH)
    retriever = module.CanonicalLawRetriever(canonical_laws)
    alias_payload, _ = load_runtime_aliases(
        audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
        global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
        seeded_path=PRODUCTION_SEED_ALIASES_PATH,
    )
    alias_map = _primary_alias_map(alias_payload)
    alias_matchers = module._build_alias_matchers(alias_map)
    return module, retriever, alias_matchers


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set.")
    return genai.Client()


def enrich_law_anomalies(
    anomalies: list[dict[str, Any]],
    *,
    model: str | None = None,
    progress_callback: ProgressCallback | None = None,
    timeout_ms: int = 20000,
    retries: int = 1,
) -> list[dict[str, Any]]:
    module, retriever, alias_matchers = _retrieval_resources()
    client = _client()
    model_name = model or os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    rows: list[dict[str, Any]] = []

    for anomaly in anomalies:
        bundle = module._collect_candidates(
            entry=anomaly,
            retriever=retriever,
            alias_matchers=alias_matchers,
            top_k_lexical=10,
            max_candidates=12,
            min_lexical_score=0.8,
            max_lexical_additions=4,
        )
        candidate_laws = [(law_id, retriever.get_name(law_id)) for law_id in bundle.law_ids]
        target_reference = str(anomaly.get("target_reference") or "").strip()
        context_block = str(anomaly.get("context_block") or "").strip()
        prompt = (
            module._build_prompt(target_reference, context_block, candidate_laws)
            if candidate_laws
            else _build_zero_candidate_prompt(module, target_reference, context_block)
        )

        result: dict[str, Any]
        try:
            response = None
            last_exc = None
            for attempt in range(retries + 1):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": ResolutionDecision,
                            "temperature": 0.1,
                            "http_options": genai_types.HttpOptions(timeout=timeout_ms),
                        },
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= retries:
                        raise
                    time.sleep(min(2.0, 0.5 * (attempt + 1)))
            if response is None and last_exc is not None:
                raise last_exc
            result = _coerce_result_from_response(response)
        except Exception as exc:
            result = {
                "classification": "unresolved",
                "resolved_law_id": None,
                "missing_candidate_guess_raw": "",
                "missing_candidate_guess_normalized": "",
                "confidence": 0.0,
                "rationale": f"api_error: {exc}",
            }

        if result["classification"] == "czech_resolved":
            if result["resolved_law_id"] not in bundle.law_ids:
                result["classification"] = "unresolved"
                result["resolved_law_id"] = None
                result["confidence"] = min(float(result["confidence"]), 0.5)
                result["rationale"] = (
                    "Post-validation rejected ID outside shortlist. " + result["rationale"]
                )
        else:
            result["resolved_law_id"] = None

        row = {
            "entry_id": anomaly.get("entry_id"),
            "raw_start": anomaly.get("raw_start"),
            "raw_end": anomaly.get("raw_end"),
            "citation_type": anomaly.get("citation_type"),
            "source": anomaly.get("source"),
            "document_id": anomaly.get("document_id"),
            "route_reason": anomaly.get("route_reason"),
            "result": result,
            "candidate_laws": [
                {
                    "law_id": law_id,
                    "law_name": retriever.get_name(law_id),
                    "sources": bundle.sources.get(law_id, []),
                }
                for law_id in bundle.law_ids
            ],
            "model": model_name,
            "timestamp_unix": int(time.time()),
        }
        rows.append(row)
        if progress_callback is not None:
            progress_callback(row)

    return rows
