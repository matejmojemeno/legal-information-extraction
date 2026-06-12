#!/usr/bin/env python3
"""
Best-effort prefill for manual annotation task files.

The goal is to reduce annotation effort, not to finalize the gold data automatically.
This script fills likely values using the current deterministic extractor and dictionaries,
while keeping the task files editable for manual review.

Example:
  python3 scripts/09b_prefill_manual_annotation_tasks.py \
    --manifest data/annotations/manifests/next_annotation_batch.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import (
    ANAPHORA_PATTERN,
    LAW_ID_PATTERN,
    extract_citation_occurrences,
)

LAW_ID_MENTION_PATTERN = re.compile(r"\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.")
FOREIGN_LAW_PATTERN = re.compile(
    r"\b("
    r"německ(?:ého|ý|á|ém)|slovensk(?:ého|ý|á|ém)|rakousk(?:ého|ý|á|ém)|"
    r"polsk(?:ého|ý|á|ém)|francouzsk(?:ého|ý|á|ém)|italsk(?:ého|ý|á|ém)|"
    r"unijn(?:ího|í)|evropsk(?:ého|ý|á|ém)"
    r")\b",
    re.IGNORECASE,
)
GENERIC_LAW_CUE_PATTERN = re.compile(
    r"\b(zákona|zákoníku|vyhlášky|nařízení|směrnice|listiny|ústavy|předpisu)\b",
    re.IGNORECASE,
)
RESOLVER_STAGE_ALIAS_PATTERN = re.compile(r"\('([^']+)'\)")


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_manifest(manifest_path: str) -> list[str]:
    doc_ids: list[str] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("document_id"), str):
                doc_ids.append(row["document_id"])
    return doc_ids


def _primary_name(canonical_laws: dict[str, list[str] | str], law_id: str) -> str:
    value = canonical_laws.get(law_id)
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, str) else ""
    if isinstance(value, str):
        return value
    return ""


def _task_abs_start(task: dict[str, Any]) -> int | None:
    snippet_text = str(task.get("snippet_text", ""))
    target_reference = str(task.get("target_reference", ""))
    snippet_doc_start = task.get("snippet_doc_start")
    occurrence_index = 1

    anns = task.get("annotations")
    if isinstance(anns, list) and anns:
        occurrence_index = int(anns[0].get("occurrence_index_in_snippet", 1) or 1)

    if not isinstance(snippet_doc_start, int) or not snippet_text or not target_reference:
        return None

    starts: list[int] = []
    start = 0
    while True:
        idx = snippet_text.find(target_reference, start)
        if idx == -1:
            break
        starts.append(idx)
        start = idx + 1

    if not starts:
        return None

    if occurrence_index < 1 or occurrence_index > len(starts):
        occurrence_index = 1
    return snippet_doc_start + starts[occurrence_index - 1]


def _resolver_stage_alias(stage: str) -> str | None:
    match = RESOLVER_STAGE_ALIAS_PATTERN.search(stage)
    return match.group(1) if match else None


def _law_name_for_occurrence(
    occurrence: Any,
    full_text: str,
    local_aliases: dict[str, str],
    canonical_laws: dict[str, list[str] | str],
) -> tuple[str, str]:
    """
    Returns: (law_name_text, declared_alias_text)
    """
    resolved_id = occurrence.resolved_law_id
    if not resolved_id:
        return ("", "")

    canonical_name = _primary_name(canonical_laws, resolved_id)
    forward_context = occurrence.context[len(occurrence.citation_text) :]

    anaphora_match = ANAPHORA_PATTERN.search(forward_context)
    if anaphora_match:
        declared_alias = ""
        aliases_for_law = sorted(
            alias for alias, law_id in local_aliases.items() if law_id == resolved_id
        )
        if len(aliases_for_law) == 1:
            declared_alias = aliases_for_law[0]
        return (anaphora_match.group(0), declared_alias)

    stage_alias = _resolver_stage_alias(occurrence.resolver_stage)
    if stage_alias:
        if stage_alias in local_aliases:
            return (stage_alias, stage_alias)
        return (stage_alias, "")

    return (canonical_name, "")


def _classify_unresolved(task: dict[str, Any], full_text: str) -> str:
    start = _task_abs_start(task)
    if start is None:
        return "czech_unresolved"

    target_reference = str(task.get("target_reference", ""))
    end = start + len(target_reference)
    ctx = full_text[end : min(len(full_text), end + 180)]

    if FOREIGN_LAW_PATTERN.search(ctx) and GENERIC_LAW_CUE_PATTERN.search(ctx):
        return "foreign_law"
    return "czech_unresolved"


def _prefill_law_id_mention(
    ann: dict[str, Any],
    task: dict[str, Any],
    canonical_laws: dict[str, list[str] | str],
) -> None:
    law_id = re.sub(r"\s+", " ", str(task.get("target_reference", ""))).strip()
    ann["citation_type"] = "other_normative"
    ann["classification"] = "czech_resolved"
    ann["law_id"] = law_id
    ann["law_name_text"] = _primary_name(canonical_laws, law_id)
    ann["declared_alias_text"] = ""
    ann["detail_number"] = ""
    ann["detail_odst"] = []
    ann["detail_pism"] = []
    ann["confidence"] = 0.99
    ann["note"] = "assistant_prefill_v1:law_id_mention"


def _prefill_section_like(
    ann: dict[str, Any],
    task: dict[str, Any],
    occurrence_map: dict[tuple[int, str], Any],
    full_text: str,
    local_aliases: dict[str, str],
    canonical_laws: dict[str, list[str] | str],
) -> None:
    abs_start = _task_abs_start(task)
    target_reference = str(task.get("target_reference", ""))
    occ = occurrence_map.get((abs_start or -1, target_reference))

    if occ is None:
        ann["citation_type"] = (
            "article"
            if target_reference.lower().startswith(("čl.", "článek"))
            else "section"
        )
        ann["classification"] = _classify_unresolved(task, full_text)
        ann["law_id"] = None
        ann["law_name_text"] = ""
        ann["declared_alias_text"] = ""
        ann["detail_number"] = ""
        ann["detail_odst"] = []
        ann["detail_pism"] = []
        ann["confidence"] = 0.2
        ann["note"] = "assistant_prefill_v1:no_occurrence_match"
        return

    ann["citation_type"] = occ.citation_type
    if occ.resolved_law_id:
        ann["classification"] = "czech_resolved"
        ann["law_id"] = occ.resolved_law_id
        law_name_text, declared_alias_text = _law_name_for_occurrence(
            occ,
            full_text=full_text,
            local_aliases=local_aliases,
            canonical_laws=canonical_laws,
        )
        ann["law_name_text"] = law_name_text
        ann["declared_alias_text"] = declared_alias_text
    else:
        ann["classification"] = _classify_unresolved(task, full_text)
        ann["law_id"] = None
        ann["law_name_text"] = ""
        ann["declared_alias_text"] = ""

    detail = occ.parsed_detail or {}
    ann["detail_number"] = str(detail.get("number", "") or "")
    ann["detail_odst"] = list(detail.get("odst", []) or [])
    ann["detail_pism"] = list(detail.get("pism", []) or [])
    ann["confidence"] = round(float(occ.confidence), 2)
    stage_slug = re.sub(r"[^a-z0-9]+", "_", occ.resolver_stage.lower()).strip("_")
    ann["note"] = f"assistant_prefill_v1:{stage_slug}"


def _should_fill(ann: dict[str, Any], force: bool) -> bool:
    if force:
        return True
    fields = [
        ann.get("citation_type"),
        ann.get("classification"),
        ann.get("law_id"),
        ann.get("law_name_text"),
        ann.get("note"),
    ]
    return not any(value not in ("", None, []) for value in fields)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Best-effort prefill for manual annotation task files."
    )
    parser.add_argument(
        "--task-dir",
        default="data/annotations/tasks/by_document",
        help="Directory containing per-document task JSON files.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional manifest JSONL restricting which documents to prefill.",
    )
    parser.add_argument(
        "--global-dict",
        default="data/dicts/global_aliases.json",
        help="Path to global aliases JSON.",
    )
    parser.add_argument(
        "--seeded-aliases",
        default="data/dicts/seed_aliases.json",
        help="Path to seeded aliases JSON.",
    )
    parser.add_argument(
        "--canonical-laws",
        default="data/dicts/canonical_laws.json",
        help="Path to canonical laws JSON.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing annotation fields.",
    )
    args = parser.parse_args()

    global_aliases, _ = load_runtime_aliases(
        global_path=args.global_dict,
        seeded_path=args.seeded_aliases,
    )
    canonical_laws = _load_json(args.canonical_laws)

    selected_doc_ids: set[str] | None = None
    if args.manifest:
        selected_doc_ids = set(_load_manifest(args.manifest))

    task_dir = Path(args.task_dir)
    paths = sorted(task_dir.glob("*.json"))
    updated_files = 0
    updated_tasks = 0

    for task_path in paths:
        payload = _load_json(str(task_path))
        if not isinstance(payload, dict):
            continue

        doc_id = payload.get("document_id")
        if not isinstance(doc_id, str):
            continue
        if selected_doc_ids is not None and doc_id not in selected_doc_ids:
            continue

        doc_path = payload.get("document_path")
        if not isinstance(doc_path, str):
            continue
        if not os.path.isabs(doc_path):
            doc_path = os.path.join(os.getcwd(), doc_path)
        if not os.path.exists(doc_path):
            continue

        with open(doc_path, "r", encoding="utf-8") as f:
            full_text = f.read()

        local_aliases = extract_local_aliases(full_text)
        occurrences = extract_citation_occurrences(full_text, local_aliases, global_aliases)
        occurrence_map = {
            (occ.raw_start, occ.citation_text): occ
            for occ in occurrences
        }

        changed = False
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            continue

        for task in tasks:
            if not isinstance(task, dict):
                continue
            anns = task.get("annotations")
            if not isinstance(anns, list) or not anns:
                continue
            ann = anns[0]
            if not isinstance(ann, dict) or not _should_fill(ann, args.force):
                continue

            if task.get("candidate_type") == "law_id_mention":
                _prefill_law_id_mention(ann, task, canonical_laws)
            else:
                _prefill_section_like(
                    ann,
                    task,
                    occurrence_map=occurrence_map,
                    full_text=full_text,
                    local_aliases=local_aliases,
                    canonical_laws=canonical_laws,
                )
            changed = True
            updated_tasks += 1

        if changed:
            with open(task_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            updated_files += 1

    print(f"Updated files: {updated_files}")
    print(f"Updated tasks: {updated_tasks}")


if __name__ == "__main__":
    main()
