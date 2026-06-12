#!/usr/bin/env python3
"""
Run a small route-aware Gemini pilot for document-reference hard cases.

This script is intentionally narrow:
- it consumes the reviewed pilot batch from scripts/26_prepare_document_reference_llm_pilot.py
- it uses route-specific prompts and strict post-validation
- it writes JSONL results for later inspection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Ensure project root is importable when the script is run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.document_reference_llm_prompts import prompt_for_entry, system_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small LLM pilot for document-reference hard cases.")
    parser.add_argument(
        "--input",
        default="data/annotations/document_reference_links/eval/document_reference_llm_pilot_v1.json",
        help="Pilot batch JSON input.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/document_reference_links/eval/document_reference_llm_pilot_v1.results.jsonl",
        help="Output JSONL results path.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model name.",
    )
    parser.add_argument(
        "--prompt-version",
        choices=["v1", "v2"],
        default="v2",
        help="Route prompt version to use.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not call the API.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of entries to run.")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    return parser.parse_args()


def _load_batch(path: str) -> dict:
    batch_path = Path(path)
    if batch_path.suffix == ".jsonl":
        entries = []
        with batch_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return {"entries": entries, "schema_version": "document_reference_llm_queue_jsonl"}

    with batch_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"Invalid pilot batch format: {path}")
    return payload


def _append_jsonl(path: str, row: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _prompt_for_entry(entry: dict, prompt_version: str) -> tuple[str, str]:
    return prompt_for_entry(entry, prompt_version)


def main() -> None:
    args = parse_args()
    batch = _load_batch(args.input)
    entries = list(batch["entries"])
    if args.limit > 0:
        entries = entries[: args.limit]

    processed_ids: set[str] = set()
    output_path = Path(args.output)
    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                processed_ids.add(str(row.get("entry_id")))

    if not args.dry_run:
        try:
            from google import genai
            from google.genai import types as genai_types
            from pydantic import BaseModel, Field
            from typing import Literal, Optional
        except ImportError:
            raise SystemExit("Missing dependencies. Install: pip install google-genai pydantic")

        if not os.environ.get("GEMINI_API_KEY"):
            raise SystemExit("GEMINI_API_KEY is not set. Use --dry-run or set API key.")

        class _LinkDisambiguationDecision(BaseModel):
            decision: Literal["exact_target", "ambiguous", "unresolved"]
            target_document_id: Optional[str] = None
            confidence: float = Field(ge=0.0, le=1.0)
            rationale: str = Field(min_length=1, max_length=300)

        class _LinkRecoveryDecision(BaseModel):
            decision: Literal["exact_target", "same_proceeding", "unresolved"]
            target_document_id: Optional[str] = None
            confidence: float = Field(ge=0.0, le=1.0)
            rationale: str = Field(min_length=1, max_length=300)

        class _ExtractionDecision(BaseModel):
            decision: Literal["is_reference", "not_reference", "uncertain"]
            reference_type: Literal["spisova_znacka", "cislo_jednaci", "unknown"]
            normalized_body: str = ""
            confidence: float = Field(ge=0.0, le=1.0)
            rationale: str = Field(min_length=1, max_length=300)

        client = genai.Client()
        schema_by_route = {
            "link_disambiguation": _LinkDisambiguationDecision,
            "link_normalization_or_target_recovery": _LinkRecoveryDecision,
            "extraction_presence_check": _ExtractionDecision,
        }

    stats = Counter()
    print(f"Pilot entries: {len(entries)}")
    print(f"Mode: {'dry-run' if args.dry_run else 'LLM'}")
    print(f"Model: {args.model}")

    for i, entry in enumerate(entries, start=1):
        entry_id = str(entry.get("entry_id"))
        if entry_id in processed_ids:
            stats["skipped_existing"] += 1
            continue

        route, prompt = _prompt_for_entry(entry, args.prompt_version)

        if args.dry_run:
            result = {"decision": "unresolved", "confidence": 0.0, "rationale": "dry_run_no_llm"}
        else:
            schema = schema_by_route[route]
            response = None
            last_exc = None
            for attempt in range(args.retries + 1):
                try:
                    response = client.models.generate_content(
                        model=args.model,
                        contents=[
                            {"role": "user", "parts": [{"text": system_prompt(route, args.prompt_version)}]},
                            {"role": "user", "parts": [{"text": prompt}]},
                        ],
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": schema,
                            "temperature": 0.0,
                            "http_options": genai_types.HttpOptions(timeout=args.timeout_ms),
                        },
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= args.retries:
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
                if result.get("decision") == "ambiguous":
                    result["target_document_id"] = None
                if result.get("decision") == "unresolved":
                    result["target_document_id"] = None

        row = {
            "entry_id": entry_id,
            "llm_route": route,
            "source": entry.get("source"),
            "document_id": entry.get("document_id") or entry.get("source_document_id"),
            "document_path": entry.get("document_path") or entry.get("source_document_path"),
            "prompt": prompt,
            "result": result,
            "expected_outcome": entry.get("expected_outcome"),
            "timestamp_unix": int(time.time()),
            "model": None if args.dry_run else args.model,
            "prompt_version": args.prompt_version,
        }
        _append_jsonl(args.output, row)

        stats["processed"] += 1
        stats[f"route::{route}"] += 1
        stats[f"decision::{result.get('decision', 'missing')}"] += 1
        print(f"[{i}/{len(entries)}] processed={stats['processed']} route={route} decision={result.get('decision')}")

    print("\n--- DOCUMENT REFERENCE LLM PILOT DONE ---")
    print(f"Processed now:    {stats['processed']}")
    print(f"Skipped existing: {stats['skipped_existing']}")
    print(f"Output:           {args.output}")


if __name__ == "__main__":
    main()
