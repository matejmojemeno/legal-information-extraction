#!/usr/bin/env python3
"""
Step 9: Prepare manual annotation tasks with context snippets.

Output mode:
- per-document task JSON files (clean dataset workspace)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

# Ensure project root is importable when script is run directly.
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.citation_extractor import SECTION_PATTERN

LAW_ID_MENTION_PATTERN = re.compile(r"\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.")


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


def _slugify_doc_id(doc_id: str) -> str:
    slug = re.sub(r"\s+", "_", doc_id.strip())
    slug = re.sub(r"[^\w.\-]+", "_", slug, flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("._")
    return slug or "document"


def _load_manifest_docs(manifest_path: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            doc_id = row.get("document_id")
            path = row.get("path")
            source = row.get("source", "")
            if isinstance(doc_id, str) and isinstance(path, str):
                rows.append(
                    {
                        "document_id": doc_id,
                        "path": path,
                        "source": source if isinstance(source, str) else "",
                    }
                )
    return rows


def _discover_docs(processed_root: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in sorted(os.listdir(processed_root)):
        source_path = os.path.join(processed_root, source)
        if not os.path.isdir(source_path):
            continue
        for name in sorted(os.listdir(source_path)):
            if not name.endswith(".txt"):
                continue
            rows.append(
                {
                    "document_id": name,
                    "path": os.path.join(source_path, name),
                    "source": source,
                }
            )
    return rows


def _iter_candidates(text: str, include_law_id_mentions: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for m in SECTION_PATTERN.finditer(text):
        items.append(
            {
                "candidate_type": "section_or_article",
                "match_start": m.start(),
                "match_end": m.end(),
                "target_reference": m.group(0),
            }
        )

    if include_law_id_mentions:
        for m in LAW_ID_MENTION_PATTERN.finditer(text):
            items.append(
                {
                    "candidate_type": "law_id_mention",
                    "match_start": m.start(),
                    "match_end": m.end(),
                    "target_reference": m.group(0),
                }
            )

    items.sort(key=lambda x: (int(x["match_start"]), int(x["match_end"])))
    return items


def _make_task(
    doc: dict[str, str],
    text: str,
    candidate: dict[str, Any],
    context_radius: int,
    task_index: int,
    empty_finding_text: bool,
) -> dict[str, Any]:
    start = int(candidate["match_start"])
    end = int(candidate["match_end"])
    target_reference = str(candidate["target_reference"])

    snippet_start = max(0, start - context_radius)
    snippet_end = min(len(text), end + context_radius)
    snippet_text = text[snippet_start:snippet_end]

    local_start = start - snippet_start
    occurrence_index = _occurrence_index_for_position(
        snippet_text=snippet_text,
        finding_text=target_reference,
        finding_local_start=local_start,
    )

    task_id = f"{doc['document_id']}::{candidate['candidate_type']}::{start}::{task_index}"
    annotation_template = {
        "finding_id": "1",
        "finding_text": "" if empty_finding_text else target_reference,
        "occurrence_index_in_snippet": 1 if empty_finding_text else occurrence_index,
        "citation_type": "",
        "classification": "",
        "law_id": None,
        "law_name_text": "",
        "declared_alias_text": "",
        "detail_number": "",
        "detail_odst": [],
        "detail_pism": [],
        "confidence": None,
        "note": "",
    }

    return {
        "task_id": task_id,
        "document_id": doc["document_id"],
        "source": doc.get("source", ""),
        "document_path": doc["path"],
        "candidate_type": candidate["candidate_type"],
        "target_reference": target_reference,
        "snippet_doc_start": snippet_start,
        "snippet_doc_end": snippet_end,
        "snippet_text": snippet_text,
        "annotations": [annotation_template],
    }


def _write_json(path: str, payload: dict[str, Any]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare manual annotation tasks (snippet + empty fields)."
    )
    parser.add_argument(
        "--manifest",
        default="data/annotations/manifests/dataset_manifest.jsonl",
        help=(
            "Optional manifest JSONL with document_id/path/source. "
            "If missing, documents are discovered under --processed-root."
        ),
    )
    parser.add_argument(
        "--processed-root",
        default="data/processed",
        help="Root folder with source subfolders and .txt documents.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/annotations/tasks/by_document",
        help="Output directory for per-document task files.",
    )
    parser.add_argument(
        "--index-output",
        default="data/annotations/tasks/tasks_index.jsonl",
        help="Index JSONL listing generated per-document task files.",
    )
    parser.add_argument(
        "--context-radius",
        type=int,
        default=260,
        help="Characters before/after candidate to include in snippet.",
    )
    parser.add_argument(
        "--limit-docs",
        type=int,
        default=None,
        help="Process only first N documents.",
    )
    parser.add_argument(
        "--max-candidates-per-doc",
        type=int,
        default=None,
        help="Optional cap on candidates exported per document.",
    )
    parser.add_argument(
        "--include-law-id-mentions",
        action="store_true",
        help="Also include direct NN/NNNN Sb. mentions as candidates.",
    )
    parser.add_argument(
        "--empty-finding-text",
        action="store_true",
        help="Leave annotation.finding_text empty in templates.",
    )
    args = parser.parse_args()

    docs: list[dict[str, str]]
    if args.manifest and os.path.exists(args.manifest):
        docs = _load_manifest_docs(args.manifest)
    else:
        docs = _discover_docs(args.processed_root)

    if args.limit_docs:
        docs = docs[: args.limit_docs]

    all_tasks: list[dict[str, Any]] = []
    per_doc_tasks: dict[str, list[dict[str, Any]]] = {}
    doc_meta: dict[str, dict[str, str]] = {}
    task_index = 0
    skipped_docs = 0

    for doc in docs:
        path = doc["path"]
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        if not os.path.exists(path):
            skipped_docs += 1
            continue

        normalized_doc = {
            "document_id": doc["document_id"],
            "path": path,
            "source": doc.get("source", ""),
        }
        doc_meta[doc["document_id"]] = normalized_doc

        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        candidates = _iter_candidates(
            text=text,
            include_law_id_mentions=args.include_law_id_mentions,
        )
        if args.max_candidates_per_doc is not None:
            candidates = candidates[: args.max_candidates_per_doc]

        tasks_for_doc: list[dict[str, Any]] = []
        for candidate in candidates:
            task_index += 1
            task = _make_task(
                doc=normalized_doc,
                text=text,
                candidate=candidate,
                context_radius=args.context_radius,
                task_index=task_index,
                empty_finding_text=args.empty_finding_text,
            )
            all_tasks.append(task)
            tasks_for_doc.append(task)

        per_doc_tasks[doc["document_id"]] = tasks_for_doc

    exported_docs = len(docs) - skipped_docs

    os.makedirs(args.output_dir, exist_ok=True)
    index_out_dir = os.path.dirname(args.index_output)
    if index_out_dir:
        os.makedirs(index_out_dir, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    for doc_id in sorted(per_doc_tasks):
        tasks_for_doc = per_doc_tasks[doc_id]
        meta = doc_meta[doc_id]
        file_name = f"{_slugify_doc_id(doc_id)}.json"
        file_path = os.path.join(args.output_dir, file_name)
        payload = {
            "schema_version": "manual_annotation_tasks_doc_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "context_radius": args.context_radius,
            "include_law_id_mentions": bool(args.include_law_id_mentions),
            "document_id": doc_id,
            "source": meta.get("source", ""),
            "document_path": meta["path"],
            "task_count": len(tasks_for_doc),
            "tasks": tasks_for_doc,
        }
        _write_json(file_path, payload)
        index_rows.append(
            {
                "document_id": doc_id,
                "source": meta.get("source", ""),
                "document_path": meta["path"],
                "task_file": file_path,
                "task_count": len(tasks_for_doc),
            }
        )

    with open(args.index_output, "w", encoding="utf-8") as f:
        for row in index_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("--- TASK FILES CREATED (PER DOCUMENT) ---")
    print(f"Documents exported: {exported_docs}")
    print(f"Tasks exported:     {len(all_tasks)}")
    print(f"Task folder:        {args.output_dir}")
    print(f"Task index:         {args.index_output}")


if __name__ == "__main__":
    main()
