#!/usr/bin/env python3
"""Run Gemini over LLM-assisted gold-audit tasks.

This script is resumable: existing audit_task_id rows in the output JSONL are
skipped. The model result is an audit opinion, not a replacement annotation.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LLM gold-audit tasks.")
    parser.add_argument(
        "--input",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_tasks.jsonl",
        help="Audit-task JSONL.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/joint_reference_gold_v1/llm_gold_audit/gold_audit_results.jsonl",
        help="Audit result JSONL.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model name.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of new tasks to run.")
    parser.add_argument("--task-types", nargs="+", choices=["law", "docref_span", "docref_link"], default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Write synthetic uncertain results without API calls.")
    return parser.parse_args()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _processed_ids(path: str | Path) -> set[str]:
    out = Path(path)
    if not out.exists():
        return set()
    ids: set[str] = set()
    with out.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("audit_task_id"):
                ids.add(str(row["audit_task_id"]))
    return ids


def _system_prompt(task_type: str) -> str:
    base = (
        "You are performing a conservative consistency audit of gold annotations "
        "for Czech legal-reference extraction. You are not a human annotator and "
        "your answer will be used only to route disagreements for manual review. "
        "Use only the provided JSON payload. Do not use internet access or outside "
        "case knowledge. Prefer uncertain when the evidence is insufficient. "
        "Return strict JSON only."
    )
    if task_type == "law":
        return base + (
            " For law tasks, verify whether the current_gold classification and "
            "law_id are supported by the citation, context, and visible law-title "
            "or alias evidence."
        )
    if task_type == "docref_span":
        return base + (
            " For document-reference span tasks, verify whether the span is an "
            "in-scope reference to another decision or case file and whether the "
            "boundaries are plausible."
        )
    return base + (
        " For document-reference link tasks, verify whether the current target "
        "availability and target_document_id are supported by the local context "
        "and candidate_targets. Choose targets only from candidate_targets. "
        "Use label_specific_hints when present; these hints explain the current "
        "gold label and known corpus-representation issues. "
        "If current_gold_target_metadata is present, use it to audit the current "
        "gold target even when candidate_targets is empty. An empty candidate "
        "list means automatic shortlist retrieval failed for this audit task; "
        "it is not by itself evidence that the target is outside the corpus. "
        "A unique candidate is not enough for exact_in_corpus. If the cited "
        "identifier is more specific than the candidate identifier, the correct "
        "label can still be same_proceeding_only. For UOHS, a reference such as "
        "ÚOHS-S49/2013/VZ-10502/2013/532/MKn is more specific than a candidate "
        "identifier such as S0049/2013; treat that as same_proceeding_only "
        "unless candidate metadata contains the same full decision-tail "
        "identifier. Do not upgrade a proceeding-root candidate to an exact "
        "target just because it is the only candidate. When multiple candidate "
        "document ids share the same identifier and decision date, treat them as "
        "duplicate corpus records; a gold target chosen from that duplicate "
        "group can still support exact_in_corpus."
    )


def _user_prompt(task: dict[str, Any]) -> str:
    payload = {
        "task": task,
        "required_output": {
            "audit_decision": "agree | disagree | uncertain",
            "issue_type": (
                "none | span_boundary | false_positive | missing_context | "
                "wrong_classification | wrong_law_id | wrong_target | "
                "availability_disagreement | insufficient_evidence | other"
            ),
            "suggested_classification": "optional law/docref classification",
            "suggested_law_id": "optional Czech law id",
            "suggested_target_availability": "optional exact_in_corpus | same_proceeding_only | not_in_corpus | unknown",
            "suggested_target_document_id": "optional target_document_id from candidate_targets",
            "confidence": "0.0 to 1.0",
            "evidence": "short explanation grounded in the provided text",
            "manual_review_note": "what a human reviewer should check next",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    tasks = _load_jsonl(args.input)
    if args.task_types:
        allowed = set(args.task_types)
        tasks = [task for task in tasks if task.get("audit_task_type") in allowed]

    done = _processed_ids(args.output)
    tasks = [task for task in tasks if str(task.get("audit_task_id")) not in done]
    if args.limit > 0:
        tasks = tasks[: args.limit]

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

        class AuditDecision(BaseModel):
            audit_decision: Literal["agree", "disagree", "uncertain"]
            issue_type: Literal[
                "none",
                "span_boundary",
                "false_positive",
                "missing_context",
                "wrong_classification",
                "wrong_law_id",
                "wrong_target",
                "availability_disagreement",
                "insufficient_evidence",
                "other",
            ]
            suggested_classification: Optional[str] = None
            suggested_law_id: Optional[str] = None
            suggested_target_availability: Optional[
                Literal["exact_in_corpus", "same_proceeding_only", "not_in_corpus", "unknown"]
            ] = None
            suggested_target_document_id: Optional[str] = None
            confidence: float = Field(ge=0.0, le=1.0)
            evidence: str = Field(min_length=1, max_length=900)
            manual_review_note: str = Field(min_length=1, max_length=900)

        client = genai.Client()

    stats = Counter()
    print(f"Tasks to run: {len(tasks)}")
    print(f"Mode: {'dry-run' if args.dry_run else 'Gemini'}")
    print(f"Model: {args.model}")

    for index, task in enumerate(tasks, start=1):
        task_id = str(task["audit_task_id"])
        task_type = str(task["audit_task_type"])
        if args.dry_run:
            result = {
                "audit_decision": "uncertain",
                "issue_type": "insufficient_evidence",
                "suggested_classification": None,
                "suggested_law_id": None,
                "suggested_target_availability": None,
                "suggested_target_document_id": None,
                "confidence": 0.0,
                "evidence": "dry_run_no_llm",
                "manual_review_note": "dry_run_no_llm",
            }
            api_error = None
        else:
            response = None
            last_exc: Exception | None = None
            for attempt in range(args.retries + 1):
                try:
                    response = client.models.generate_content(
                        model=args.model,
                        contents=[
                            {"role": "user", "parts": [{"text": _system_prompt(task_type)}]},
                            {"role": "user", "parts": [{"text": _user_prompt(task)}]},
                        ],
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": AuditDecision,
                            "temperature": args.temperature,
                            "http_options": genai_types.HttpOptions(timeout=args.timeout_ms),
                        },
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= args.retries:
                        break
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))

            if response is None:
                api_error = str(last_exc)
                result = {
                    "audit_decision": "uncertain",
                    "issue_type": "insufficient_evidence",
                    "suggested_classification": None,
                    "suggested_law_id": None,
                    "suggested_target_availability": None,
                    "suggested_target_document_id": None,
                    "confidence": 0.0,
                    "evidence": f"api_error: {api_error}",
                    "manual_review_note": "Rerun this task or inspect manually.",
                }
            else:
                api_error = None
                parsed = getattr(response, "parsed", None)
                if parsed is not None:
                    result = parsed.model_dump()
                else:
                    result = json.loads(getattr(response, "text", "") or "{}")

        row = {
            "audit_task_id": task_id,
            "audit_task_type": task_type,
            "document_id": task.get("document_id"),
            "source": task.get("source"),
            "reference": task.get("reference"),
            "current_gold": task.get("current_gold"),
            "result": result,
            "model": None if args.dry_run else args.model,
            "temperature": args.temperature,
            "timestamp_unix": int(time.time()),
            "api_error": api_error,
        }
        _append_jsonl(args.output, row)
        stats["processed"] += 1
        stats[f"type::{task_type}"] += 1
        stats[f"decision::{result.get('audit_decision', 'missing')}"] += 1
        print(
            f"[{index}/{len(tasks)}] {task_type} {task_id} -> "
            f"{result.get('audit_decision')} / {result.get('issue_type')}"
        )

    print("\n--- LLM GOLD AUDIT RUN COMPLETE ---")
    print(f"Processed: {stats['processed']}")
    for key, value in sorted(stats.items()):
        if key != "processed":
            print(f"- {key}: {value}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
