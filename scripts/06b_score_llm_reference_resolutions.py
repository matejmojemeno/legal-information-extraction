#!/usr/bin/env python3
"""
Score Step 6 LLM resolution outputs against an expected sample file.

The expected sample is a JSON list with:
- target_reference
- context_block
- expected_gold_classification
- expected_gold_law_id

The Step 6 output is a JSONL file produced by 06_llm_resolve_references.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _entry_id(target_reference: str, context_block: str) -> str:
    payload = f"{target_reference}\n{context_block}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Step 6 LLM smoke-test outputs.")
    parser.add_argument("--expected", required=True, help="Expected sample JSON.")
    parser.add_argument("--results", required=True, help="Step 6 output JSONL.")
    args = parser.parse_args()

    expected_rows = _load_json(args.expected)
    if not isinstance(expected_rows, list):
        raise SystemExit("Expected sample must be a JSON list.")

    expected_by_hash: dict[str, dict] = {}
    for row in expected_rows:
        if not isinstance(row, dict):
            continue
        target_reference = str(row.get("target_reference", "")).strip()
        context_block = str(row.get("context_block", "")).strip()
        if not target_reference or not context_block:
            continue
        expected_by_hash[_entry_id(target_reference, context_block)] = row

    result_rows = _load_jsonl(args.results)

    rows = 0
    class_match = 0
    law_id_exact_match = 0
    mismatches: list[dict] = []

    for row in result_rows:
        entry_id = row.get("entry_id")
        if not isinstance(entry_id, str):
            continue
        expected = expected_by_hash.get(entry_id)
        if not expected:
            continue

        rows += 1
        result = row.get("result", {})
        result_class = result.get("classification")
        result_law_id = result.get("resolved_law_id")
        gold_class = expected.get("expected_gold_classification")
        gold_law_id = expected.get("expected_gold_law_id")

        if result_class == gold_class:
            class_match += 1

        if gold_class == "czech_resolved" and result_class == "czech_resolved" and result_law_id == gold_law_id:
            law_id_exact_match += 1

        mismatch = False
        if result_class != gold_class:
            mismatch = True
        elif gold_class == "czech_resolved" and result_law_id != gold_law_id:
            mismatch = True

        if mismatch:
            mismatches.append(
                {
                    "expected_entry_id": expected.get("entry_id"),
                    "result_entry_hash": entry_id,
                    "result_class": result_class,
                    "result_law_id": result_law_id,
                    "gold_class": gold_class,
                    "gold_law_id": gold_law_id,
                    "rationale": result.get("rationale", ""),
                }
            )

    summary = {
        "rows": rows,
        "class_match": class_match,
        "law_id_exact_match": law_id_exact_match,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if mismatches:
        print("Mismatches:")
        print(json.dumps(mismatches, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
