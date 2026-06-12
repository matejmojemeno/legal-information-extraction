#!/usr/bin/env python3
"""Prepare bounded-passage LLM extraction baseline tasks.

The baseline asks an LLM to extract law and document references directly from
short passages. Tasks are generated from the final joint benchmark documents,
but the prompts do not include gold annotations.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare LLM passage baseline tasks.")
    parser.add_argument(
        "--law-gold",
        default="data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl",
    )
    parser.add_argument(
        "--docref-gold",
        default="data/annotations/joint_reference_gold_v1/document_reference_gold/joint_document_reference_gold_v1.jsonl",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_tasks.jsonl",
    )
    parser.add_argument("--context-chars", type=int, default=900)
    parser.add_argument("--max-window-chars", type=int, default=2800)
    parser.add_argument("--negative-windows-per-doc", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1337)
    return parser.parse_args()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_gold_occurrences(law_gold: Path, docref_gold: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    docs: dict[str, dict[str, Any]] = {}
    occs_by_doc: dict[str, list[dict[str, Any]]] = {}

    for doc in _load_jsonl(law_gold):
        document_id = str(doc["document_id"])
        docs[document_id] = {
            "document_id": document_id,
            "source": doc.get("source"),
            "document_path": doc.get("document_path"),
        }
        for cit in doc.get("citations", []):
            if cit.get("classification") not in {"czech_resolved", "czech_unresolved", "foreign_law"}:
                continue
            occs_by_doc.setdefault(document_id, []).append(
                {
                    "kind": "law_reference",
                    "start": int(cit["start_char"]),
                    "end": int(cit["end_char"]),
                }
            )

    for row in _load_jsonl(docref_gold):
        if row.get("classification") != "document_reference":
            continue
        document_id = str(row["document_id"])
        docs.setdefault(
            document_id,
            {
                "document_id": document_id,
                "source": row.get("source"),
                "document_path": row.get("document_path"),
            },
        )
        occs_by_doc.setdefault(document_id, []).append(
            {
                "kind": "document_reference",
                "start": int(row["start_char"]),
                "end": int(row["end_char"]),
            }
        )

    return docs, occs_by_doc


def _positive_windows(
    occurrences: list[dict[str, Any]],
    text_len: int,
    context_chars: int,
    max_window_chars: int,
) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for occ in sorted(occurrences, key=lambda item: (item["start"], item["end"])):
        start = max(0, int(occ["start"]) - context_chars)
        end = min(text_len, int(occ["end"]) + context_chars)
        if end - start > max_window_chars:
            mid = (int(occ["start"]) + int(occ["end"])) // 2
            half = max_window_chars // 2
            start = max(0, mid - half)
            end = min(text_len, start + max_window_chars)
            start = max(0, end - max_window_chars)

        if intervals and start <= intervals[-1][1] and max(end, intervals[-1][1]) - intervals[-1][0] <= max_window_chars:
            prev_start, prev_end = intervals[-1]
            intervals[-1] = (prev_start, max(prev_end, end))
        else:
            intervals.append((start, end))
    return intervals


def _overlaps_any(start: int, end: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start < b and end > a for a, b in intervals)


def _negative_windows(
    rng: random.Random,
    text_len: int,
    positive: list[tuple[int, int]],
    count: int,
    max_window_chars: int,
) -> list[tuple[int, int]]:
    if count <= 0 or text_len <= 0:
        return []
    window = min(max_window_chars, text_len)
    candidates: list[tuple[int, int]] = []
    for _ in range(200):
        start = rng.randint(0, max(0, text_len - window))
        end = min(text_len, start + window)
        if not _overlaps_any(start, end, positive):
            candidates.append((start, end))
        if len(candidates) >= count:
            break
    return candidates


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    docs, occs_by_doc = _load_gold_occurrences(Path(args.law_gold), Path(args.docref_gold))

    tasks: list[dict[str, Any]] = []
    for document_id in sorted(docs):
        meta = docs[document_id]
        document_path = Path(str(meta["document_path"]))
        text = document_path.read_text(encoding="utf-8")
        positive = _positive_windows(
            occs_by_doc.get(document_id, []),
            len(text),
            args.context_chars,
            args.max_window_chars,
        )
        negative = _negative_windows(
            rng,
            len(text),
            positive,
            args.negative_windows_per_doc,
            args.max_window_chars,
        )
        for index, (start, end) in enumerate(positive + negative, start=1):
            task_kind = "positive_window" if index <= len(positive) else "negative_window"
            tasks.append(
                {
                    "task_id": f"{document_id}::passage::{start}:{end}",
                    "schema_version": "llm_passage_baseline_task_v1",
                    "task_kind": task_kind,
                    "document_id": document_id,
                    "source": meta.get("source"),
                    "document_path": str(document_path),
                    "passage_start": start,
                    "passage_end": end,
                    "passage_text": text[start:end],
                }
            )

    _write_jsonl(args.output, tasks)
    positive_count = sum(1 for task in tasks if task["task_kind"] == "positive_window")
    negative_count = len(tasks) - positive_count
    print("--- LLM PASSAGE BASELINE TASKS PREPARED ---")
    print(f"Documents: {len(docs)}")
    print(f"Tasks: {len(tasks)}")
    print(f"Positive windows: {positive_count}")
    print(f"Negative windows: {negative_count}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
