#!/usr/bin/env python3
"""
Prepare annotation-ready task files for the LLM challenge batch.

This keeps the LLM challenge review separate from the main 17-document
deterministic benchmark. Tasks are grouped per document and prefilled from the
current deterministic output so review can focus on correcting the hard cases.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slugify_doc_id(doc_id: str) -> str:
    slug = re.sub(r"\s+", "_", doc_id.strip())
    slug = re.sub(r"[^\w.\-]+", "_", slug, flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("._")
    return slug or "document"


def _all_occurrence_starts(haystack: str, needle: str) -> list[int]:
    starts: list[int] = []
    if not needle:
        return starts
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        starts.append(idx)
        start = idx + 1
    return starts


def _occurrence_index_for_position(
    snippet_text: str,
    finding_text: str,
    finding_local_start: int,
) -> int:
    starts = _all_occurrence_starts(snippet_text, finding_text)
    for i, s in enumerate(starts, start=1):
        if s == finding_local_start:
            return i
    return 1


def _write_json(path: str, payload: dict[str, Any]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_batch(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"Invalid challenge batch format: {path}")
    return payload


def _annotation_from_entry(entry: dict[str, Any], occurrence_index: int) -> dict[str, Any]:
    predicted_classification = str(entry.get("predicted_classification") or "")
    resolved_law_id = entry.get("resolved_law_id")
    parsed_detail = entry.get("parsed_detail") or {}
    route = str(entry.get("llm_route") or "")

    if predicted_classification == "foreign_law":
        law_id = None
        law_name_text = ""
    else:
        law_id = resolved_law_id
        law_name_text = ""

    return {
        "finding_id": "1",
        "finding_text": str(entry.get("target_reference") or ""),
        "occurrence_index_in_snippet": occurrence_index,
        "citation_type": str(entry.get("citation_type") or ""),
        "classification": predicted_classification,
        "law_id": law_id,
        "law_name_text": law_name_text,
        "declared_alias_text": "",
        "detail_number": str(parsed_detail.get("number") or ""),
        "detail_odst": list(parsed_detail.get("odst") or []),
        "detail_pism": list(parsed_detail.get("pism") or []),
        "confidence": entry.get("confidence"),
        "note": f"assistant_prefill_v1:llm_challenge:{route}",
    }


def _task_from_entry(
    entry: dict[str, Any],
    text: str,
    context_radius: int,
    task_index: int,
) -> dict[str, Any]:
    start = int(entry["start_char"])
    end = int(entry["end_char"])
    target_reference = str(entry["target_reference"])

    snippet_start = max(0, start - context_radius)
    snippet_end = min(len(text), end + context_radius)
    snippet_text = text[snippet_start:snippet_end]
    local_start = start - snippet_start
    occurrence_index = _occurrence_index_for_position(
        snippet_text=snippet_text,
        finding_text=target_reference,
        finding_local_start=local_start,
    )

    task_id = f"{entry['document_id']}::{entry['citation_type']}::{start}::{task_index}"
    task = {
        "task_id": task_id,
        "document_id": entry["document_id"],
        "source": entry["source"],
        "document_path": entry["document_path"],
        "candidate_type": "llm_challenge_candidate",
        "target_reference": target_reference,
        "snippet_doc_start": snippet_start,
        "snippet_doc_end": snippet_end,
        "snippet_text": snippet_text,
        "annotations": [_annotation_from_entry(entry, occurrence_index)],
        "challenge_metadata": {
            "entry_id": entry["entry_id"],
            "llm_route": entry.get("llm_route"),
            "route_reason": entry.get("route_reason"),
            "deterministic_status": entry.get("deterministic_status"),
            "resolver_stage": entry.get("resolver_stage"),
            "candidate_law_ids": list(entry.get("candidate_law_ids") or []),
        },
    }
    return task


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare per-document LLM challenge task files.")
    parser.add_argument(
        "--input",
        default="data/annotations/eval/llm_challenge_batch_v1.json",
        help="Curated LLM challenge batch JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/annotations/llm_challenge_tasks/by_document",
        help="Output folder for per-document challenge task files.",
    )
    parser.add_argument(
        "--index-output",
        default="data/annotations/llm_challenge_tasks/tasks_index.jsonl",
        help="Index JSONL for generated challenge task files.",
    )
    parser.add_argument(
        "--context-radius",
        type=int,
        default=260,
        help="Characters before/after anchor to include in snippets.",
    )
    args = parser.parse_args()

    batch = _load_batch(args.input)
    entries = list(batch["entries"])

    per_doc_tasks: dict[str, list[dict[str, Any]]] = {}
    doc_meta: dict[str, dict[str, str]] = {}

    task_index = 0
    for entry in entries:
        document_id = str(entry["document_id"])
        document_path = str(entry["document_path"])
        if not os.path.isabs(document_path):
            document_path = os.path.join(os.getcwd(), document_path)
        text = Path(document_path).read_text(encoding="utf-8")

        task_index += 1
        task = _task_from_entry(
            entry=entry,
            text=text,
            context_radius=args.context_radius,
            task_index=task_index,
        )
        per_doc_tasks.setdefault(document_id, []).append(task)
        doc_meta[document_id] = {
            "source": str(entry.get("source") or ""),
            "document_path": document_path,
        }

    os.makedirs(args.output_dir, exist_ok=True)
    index_dir = os.path.dirname(args.index_output)
    if index_dir:
        os.makedirs(index_dir, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    for document_id in sorted(per_doc_tasks):
        tasks = per_doc_tasks[document_id]
        meta = doc_meta[document_id]
        file_name = f"{_slugify_doc_id(document_id)}.json"
        file_path = os.path.join(args.output_dir, file_name)
        payload = {
            "schema_version": "manual_annotation_tasks_doc_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "context_radius": args.context_radius,
            "challenge_batch": os.path.abspath(args.input),
            "document_id": document_id,
            "source": meta["source"],
            "document_path": meta["document_path"],
            "task_count": len(tasks),
            "tasks": tasks,
        }
        _write_json(file_path, payload)
        index_rows.append(
            {
                "document_id": document_id,
                "source": meta["source"],
                "document_path": meta["document_path"],
                "task_file": file_path,
                "task_count": len(tasks),
            }
        )

    with open(args.index_output, "w", encoding="utf-8") as f:
        for row in index_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("--- LLM CHALLENGE TASK FILES CREATED ---")
    print(f"Documents exported: {len(index_rows)}")
    print(f"Tasks exported:     {len(entries)}")
    print(f"Task folder:        {args.output_dir}")
    print(f"Task index:         {args.index_output}")


if __name__ == "__main__":
    main()
