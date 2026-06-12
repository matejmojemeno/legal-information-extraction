#!/usr/bin/env python3
"""Run Gemini direct-extraction baseline over bounded passages.

The script is resumable: existing task_id rows in the output JSONL are skipped.
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
    parser = argparse.ArgumentParser(description="Run LLM passage extraction baseline.")
    parser.add_argument(
        "--input",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_tasks.jsonl",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_results.jsonl",
    )
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
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
            if row.get("task_id") and not row.get("api_error"):
                ids.add(str(row["task_id"]))
    return ids


def _normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        return {"references": result}
    if isinstance(result, dict):
        refs = result.get("references")
        if isinstance(refs, list):
            return {"references": refs}
        required = result.get("required_output")
        if isinstance(required, dict) and isinstance(required.get("references"), list):
            return {"references": required["references"]}
    return {"references": []}


def _system_prompt() -> str:
    return (
        "You extract legal references from bounded passages of Czech judicial "
        "decisions. Return only references whose exact text appears in the "
        "passage. Extract two types: law_reference for references to legal acts "
        "or provisions, and document_reference for references to court or "
        "administrative decisions, case files, or proceeding identifiers. Use "
        "character offsets relative to the passage_text string. The end offset "
        "is exclusive. Prefer exact span boundaries over broad surrounding "
        "phrases. For law references, prefer the concrete citation anchor, such "
        "as § 112, § 120 odst. 1 písm. a), čl. 36 Listiny, zákon č. 137/2006 "
        "Sb., or 137/2006 Sb.; do not extract ordinary standalone words such as "
        "zákon or správní řád unless they are part of a visible citation phrase. "
        "Document-reference markers include strings such as č. j. "
        "2 Afs 198/2006-69, sp. zn. Pl. ÚS 33/97, sen. zn. 29 ICdo 2/2022, "
        "and ÚOHS-S49/2013/VZ-10502/2013/532/MKn. Do not extract public "
        "procurement registry numbers such as ev. č. zakázky, contract numbers, "
        "paragraph numbers, bod references, or internal article references such "
        "as čl. 5.5 unless they identify a cited decision or proceeding. If no "
        "in-scope references are present, return an empty list. "
        "Do not use external knowledge. Return strict JSON only."
    )


def _user_prompt(task: dict[str, Any]) -> str:
    payload = {
        "document_id": task["document_id"],
        "source": task.get("source"),
        "passage_text": task["passage_text"],
        "required_output": {
            "references": [
                {
                    "reference_type": "law_reference | document_reference",
                    "exact_text": "substring copied exactly from passage_text",
                    "start_offset": "integer offset in passage_text",
                    "end_offset": "exclusive integer offset in passage_text",
                    "law_id": "optional Czech law id such as 99/1963 Sb.; null if unknown or not a law reference",
                    "target_identifier": "optional document/case identifier; null if unknown or not a document reference",
                    "confidence": "0.0 to 1.0",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    tasks = _load_jsonl(args.input)
    done = _processed_ids(args.output)
    tasks = [task for task in tasks if str(task["task_id"]) not in done]
    if args.limit > 0:
        tasks = tasks[: args.limit]

    if not args.dry_run:
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError:
            raise SystemExit("Missing dependency. Install: pip install google-genai")
        if not os.environ.get("GEMINI_API_KEY"):
            raise SystemExit("GEMINI_API_KEY is not set. Use --dry-run or set the API key.")

        client = genai.Client()

    stats = Counter()
    print(f"Tasks to run: {len(tasks)}")
    print(f"Mode: {'dry-run' if args.dry_run else 'Gemini'}")
    print(f"Model: {args.model}")

    for index, task in enumerate(tasks, start=1):
        if args.dry_run:
            result = {"references": []}
            api_error = None
        else:
            response = None
            last_exc: Exception | None = None
            for attempt in range(args.retries + 1):
                try:
                    response = client.models.generate_content(
                        model=args.model,
                        contents=[
                            {"role": "user", "parts": [{"text": _system_prompt()}]},
                            {"role": "user", "parts": [{"text": _user_prompt(task)}]},
                        ],
                        config={
                            "response_mime_type": "application/json",
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
                    time.sleep(min(10.0, 1.5 * (attempt + 1)))

            if response is None:
                api_error = str(last_exc)
                result = {"references": []}
            else:
                api_error = None
                raw_text = getattr(response, "text", "") or "{\"references\": []}"
                result = _normalize_result(json.loads(raw_text))

        refs = result.get("references") if isinstance(result, dict) else []
        row = {
            "task_id": task["task_id"],
            "document_id": task["document_id"],
            "source": task.get("source"),
            "document_path": task.get("document_path"),
            "passage_start": task.get("passage_start"),
            "passage_end": task.get("passage_end"),
            "task_kind": task.get("task_kind"),
            "result": result,
            "model": None if args.dry_run else args.model,
            "temperature": args.temperature,
            "timestamp_unix": int(time.time()),
            "api_error": api_error,
        }
        _append_jsonl(args.output, row)
        stats["processed"] += 1
        stats["references"] += len(refs) if isinstance(refs, list) else 0
        if api_error:
            print(f"[{index}/{len(tasks)}] {task['task_id']} -> API ERROR: {api_error}")
        else:
            print(f"[{index}/{len(tasks)}] {task['task_id']} -> {len(refs) if isinstance(refs, list) else 0} refs")

    print("\n--- LLM PASSAGE BASELINE RUN COMPLETE ---")
    print(f"Processed: {stats['processed']}")
    print(f"Extracted references: {stats['references']}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
