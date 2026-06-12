#!/usr/bin/env python3
"""
Step 10: Finalize manual annotation tasks into span-accurate gold annotations.

Supports input:
- single combined task JSON
- one per-document task JSON
- directory with per-document task JSON files

Outputs:
- per-document gold JSON files
- optional aggregate gold JSONL index
- JSONL error report
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

VALID_CLASSIFICATIONS = {
    "czech_resolved",
    "czech_unresolved",
    "foreign_law",
    "non_citation",
}
VALID_CITATION_TYPES = {"section", "article", "other_normative"}
LAW_ID_RE = re.compile(r"^\d{1,4}/(?:\d{2}|\d{4})\sSb\.$")


def _all_occurrence_starts(haystack: str, needle: str) -> list[int]:
    starts: list[int] = []
    if not needle:
        return starts
    cursor = 0
    while True:
        idx = haystack.find(needle, cursor)
        if idx == -1:
            break
        starts.append(idx)
        cursor = idx + 1
    return starts


def _infer_citation_type(text: str) -> str:
    t = text.strip().lower()
    if t.startswith("§"):
        return "section"
    if t.startswith("čl.") or t.startswith("článek"):
        return "article"
    return "other_normative"


def _normalize_law_id(law_id: str) -> str:
    return re.sub(r"\s+", " ", law_id).strip()


def _slugify_doc_id(doc_id: str) -> str:
    slug = re.sub(r"\s+", "_", doc_id.strip())
    slug = re.sub(r"[^\w.\-]+", "_", slug, flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("._")
    return slug or "document"


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"Task file is not JSON object: {path}")
    return payload


def _iter_task_payloads(input_path: str) -> list[tuple[str, dict[str, Any]]]:
    if os.path.isdir(input_path):
        rows: list[tuple[str, dict[str, Any]]] = []
        for name in sorted(os.listdir(input_path)):
            if not name.endswith(".json"):
                continue
            full = os.path.join(input_path, name)
            payload = _load_json(full)
            if isinstance(payload.get("tasks"), list):
                rows.append((full, payload))
        return rows

    payload = _load_json(input_path)
    return [(input_path, payload)]


def _extract_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [t for t in tasks if isinstance(t, dict)]


def _resolve_snippet_doc_start(
    task: dict[str, Any],
    doc_text: str,
) -> tuple[int | None, list[str]]:
    warnings: list[str] = []
    snippet_text = str(task.get("snippet_text", ""))
    if not snippet_text:
        return None, ["empty_snippet_text"]

    raw_start = task.get("snippet_doc_start")
    if isinstance(raw_start, int):
        if doc_text[raw_start : raw_start + len(snippet_text)] == snippet_text:
            return raw_start, warnings
        warnings.append("snippet_start_mismatch")

    starts = _all_occurrence_starts(doc_text, snippet_text)
    if not starts:
        return None, warnings + ["snippet_not_found_in_document"]
    if len(starts) > 1:
        if "snippet_occurrence_index_in_doc" in task:
            idx = int(task["snippet_occurrence_index_in_doc"])
            if idx < 1 or idx > len(starts):
                return None, warnings + ["snippet_occurrence_index_out_of_range"]
            warnings.append("snippet_ambiguous_resolved_by_index")
            return starts[idx - 1], warnings
        return None, warnings + ["snippet_ambiguous_multiple_matches"]

    return starts[0], warnings


def _write_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _write_json(path: str, payload: dict[str, Any]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize manually edited task files into gold annotations."
    )
    parser.add_argument(
        "--input",
        default="data/annotations/tasks/by_document",
        help="Input task JSON file or directory of per-document task JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/annotations/gold/by_document",
        help="Output directory for per-document gold JSON files.",
    )
    parser.add_argument(
        "--output-index",
        default="data/annotations/gold/gold_annotations_v1.jsonl",
        help="Aggregate gold JSONL index output.",
    )
    parser.add_argument(
        "--errors-output",
        default="data/annotations/gold/gold_annotations_v1.errors.jsonl",
        help="Error report JSONL.",
    )
    parser.add_argument(
        "--include-unlabeled",
        action="store_true",
        help=(
            "Include findings with empty classification as czech_unresolved. "
            "Default behavior skips unlabeled findings."
        ),
    )
    args = parser.parse_args()

    payload_rows = _iter_task_payloads(args.input)
    if not payload_rows:
        raise SystemExit(f"No task JSON files found in: {args.input}")

    doc_text_cache: dict[str, str] = {}
    output_by_doc: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    total_tasks = 0
    total_findings = 0
    exported_findings = 0
    skipped_findings = 0

    for source_path, payload in payload_rows:
        tasks = _extract_tasks(payload)
        total_tasks += len(tasks)

        for task in tasks:
            task_id = str(task.get("task_id", ""))
            doc_id = str(task.get("document_id", ""))
            source = str(task.get("source", ""))
            doc_path = str(task.get("document_path", ""))
            if not doc_path:
                errors.append(
                    {
                        "source_task_file": source_path,
                        "task_id": task_id,
                        "document_id": doc_id,
                        "error": "missing_document_path",
                    }
                )
                continue

            resolved_path = doc_path
            if not os.path.isabs(resolved_path):
                resolved_path = os.path.join(os.getcwd(), resolved_path)
            if not os.path.exists(resolved_path):
                errors.append(
                    {
                        "source_task_file": source_path,
                        "task_id": task_id,
                        "document_id": doc_id,
                        "error": "document_path_not_found",
                        "document_path": resolved_path,
                    }
                )
                continue

            if resolved_path not in doc_text_cache:
                with open(resolved_path, "r", encoding="utf-8") as f:
                    doc_text_cache[resolved_path] = f.read()
            doc_text = doc_text_cache[resolved_path]

            snippet_start, snippet_warnings = _resolve_snippet_doc_start(task, doc_text)
            snippet_text = str(task.get("snippet_text", ""))
            if snippet_start is None:
                errors.append(
                    {
                        "source_task_file": source_path,
                        "task_id": task_id,
                        "document_id": doc_id,
                        "error": "cannot_resolve_snippet_start",
                        "warnings": snippet_warnings,
                    }
                )
                continue

            annotations = task.get("annotations", [])
            if not isinstance(annotations, list):
                errors.append(
                    {
                        "source_task_file": source_path,
                        "task_id": task_id,
                        "document_id": doc_id,
                        "error": "annotations_not_list",
                    }
                )
                continue

            doc_key = f"{doc_id}::{resolved_path}"
            if doc_key not in output_by_doc:
                output_by_doc[doc_key] = {
                    "schema_version": "gold_annotations_doc_v1",
                    "document_id": doc_id,
                    "source": source,
                    "document_path": resolved_path,
                    "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                    "citations": [],
                }

            for ann in annotations:
                total_findings += 1
                if not isinstance(ann, dict):
                    skipped_findings += 1
                    errors.append(
                        {
                            "source_task_file": source_path,
                            "task_id": task_id,
                            "document_id": doc_id,
                            "error": "annotation_not_object",
                        }
                    )
                    continue

                finding_id = str(ann.get("finding_id", ""))
                finding_text = str(ann.get("finding_text", ""))
                if not finding_text:
                    skipped_findings += 1
                    errors.append(
                        {
                            "source_task_file": source_path,
                            "task_id": task_id,
                            "finding_id": finding_id,
                            "document_id": doc_id,
                            "error": "empty_finding_text",
                        }
                    )
                    continue

                classification = str(ann.get("classification", "")).strip()
                if not classification:
                    if args.include_unlabeled:
                        classification = "czech_unresolved"
                    else:
                        skipped_findings += 1
                        errors.append(
                            {
                                "source_task_file": source_path,
                                "task_id": task_id,
                                "finding_id": finding_id,
                                "document_id": doc_id,
                                "error": "unlabeled_finding_skipped",
                            }
                        )
                        continue

                if classification not in VALID_CLASSIFICATIONS:
                    skipped_findings += 1
                    errors.append(
                        {
                            "source_task_file": source_path,
                            "task_id": task_id,
                            "finding_id": finding_id,
                            "document_id": doc_id,
                            "error": "invalid_classification",
                            "classification": classification,
                        }
                    )
                    continue

                occurrence_index = int(ann.get("occurrence_index_in_snippet", 1))
                starts = _all_occurrence_starts(snippet_text, finding_text)
                if not starts:
                    skipped_findings += 1
                    errors.append(
                        {
                            "source_task_file": source_path,
                            "task_id": task_id,
                            "finding_id": finding_id,
                            "document_id": doc_id,
                            "error": "finding_text_not_in_snippet",
                            "finding_text": finding_text,
                        }
                    )
                    continue
                if occurrence_index < 1 or occurrence_index > len(starts):
                    skipped_findings += 1
                    errors.append(
                        {
                            "source_task_file": source_path,
                            "task_id": task_id,
                            "finding_id": finding_id,
                            "document_id": doc_id,
                            "error": "occurrence_index_out_of_range",
                            "occurrence_index_in_snippet": occurrence_index,
                            "occurrence_count_in_snippet": len(starts),
                        }
                    )
                    continue

                local_start = starts[occurrence_index - 1]
                local_end = local_start + len(finding_text)
                global_start = snippet_start + local_start
                global_end = snippet_start + local_end

                citation_type = str(ann.get("citation_type", "")).strip()
                if not citation_type:
                    citation_type = _infer_citation_type(finding_text)
                if citation_type not in VALID_CITATION_TYPES:
                    citation_type = "other_normative"

                law_id = ann.get("law_id")
                if isinstance(law_id, str):
                    law_id = _normalize_law_id(law_id)
                if law_id == "":
                    law_id = None

                if classification == "czech_resolved":
                    if not isinstance(law_id, str) or not LAW_ID_RE.match(law_id):
                        skipped_findings += 1
                        errors.append(
                            {
                                "source_task_file": source_path,
                                "task_id": task_id,
                                "finding_id": finding_id,
                                "document_id": doc_id,
                                "error": "resolved_without_valid_law_id",
                                "law_id": law_id,
                            }
                        )
                        continue
                else:
                    law_id = None

                confidence_raw = ann.get("confidence")
                confidence = None
                if confidence_raw is not None:
                    try:
                        confidence = float(confidence_raw)
                    except (TypeError, ValueError):
                        confidence = None

                citation_row = {
                    "task_id": task_id,
                    "finding_id": finding_id,
                    "citation_text": finding_text,
                    "citation_type": citation_type,
                    "start_char": global_start,
                    "end_char": global_end,
                    "law_id": law_id,
                    "law_name_text": ann.get("law_name_text") or None,
                    "declared_alias_text": ann.get("declared_alias_text") or None,
                    "classification": classification,
                    "confidence": confidence,
                    "detail_number": ann.get("detail_number") or None,
                    "detail_odst": ann.get("detail_odst") or [],
                    "detail_pism": ann.get("detail_pism") or [],
                    "note": ann.get("note") or "",
                    "snippet_warnings": snippet_warnings,
                }
                output_by_doc[doc_key]["citations"].append(citation_row)
                exported_findings += 1

    # Sort citations by offsets for each document.
    aggregate_rows: list[dict[str, Any]] = []
    os.makedirs(args.output_dir, exist_ok=True)
    for doc in output_by_doc.values():
        doc["citations"].sort(
            key=lambda c: (
                int(c.get("start_char", 0)),
                int(c.get("end_char", 0)),
                str(c.get("citation_text", "")),
            )
        )
        aggregate_rows.append(doc)

    processed_doc_ids = {str(row.get("document_id", "")) for row in aggregate_rows}
    existing_aggregate = _load_jsonl(args.output_index)
    preserved_aggregate = [
        row
        for row in existing_aggregate
        if str(row.get("document_id", "")) not in processed_doc_ids
    ]
    aggregate_rows = preserved_aggregate + aggregate_rows
    aggregate_rows.sort(key=lambda x: str(x.get("document_id", "")))

    # Write per-document gold files.
    for doc in aggregate_rows:
        file_name = f"{_slugify_doc_id(str(doc['document_id']))}.json"
        path = os.path.join(args.output_dir, file_name)
        _write_json(path, doc)

    existing_errors = _load_jsonl(args.errors_output)
    preserved_errors = [
        row
        for row in existing_errors
        if str(row.get("document_id", "")) not in processed_doc_ids
    ]
    _write_jsonl(args.output_index, aggregate_rows)
    _write_jsonl(args.errors_output, preserved_errors + errors)

    print("--- FINALIZED MANUAL ANNOTATIONS ---")
    print(f"Task files loaded:         {len(payload_rows)}")
    print(f"Tasks seen:                {total_tasks}")
    print(f"Total findings seen:       {total_findings}")
    print(f"Exported findings:         {exported_findings}")
    print(f"Skipped findings:          {skipped_findings}")
    print(f"Per-doc gold folder:       {args.output_dir}")
    print(f"Gold aggregate index:      {args.output_index}")
    print(f"Errors output:             {args.errors_output}")


if __name__ == "__main__":
    main()
