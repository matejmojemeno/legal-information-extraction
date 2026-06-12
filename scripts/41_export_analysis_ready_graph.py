#!/usr/bin/env python3
"""Official production export: write analysis-ready citation edges from a final run.

This is a thin downstream export step. It reads linked document-reference
outputs from a completed production run and writes a simplified edge list for
graph-oriented analysis outside the main run directory structure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Official production export: convert linked document references from "
            "a final run into a simplified analysis-ready edge JSONL."
        )
    )
    parser.add_argument(
        "--run-root",
        required=True,
        help="Path to a final run root created by scripts/40_run_final_pipeline.py.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output JSONL path. Defaults to <run-root>/analysis/document_reference_edges.jsonl.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root)
    linked_input = run_root / "document_references" / "document_reference_links.jsonl"
    if not linked_input.exists():
        raise SystemExit(f"Missing linked document-reference file: {linked_input}")

    output = Path(args.output) if args.output else run_root / "analysis" / "document_reference_edges.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with linked_input.open("r", encoding="utf-8") as src, output.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            edge = {
                "edge_type": "document_reference",
                "source_document_id": row["source_document_id"],
                "source_document_source": row["source_document_source"],
                "target_document_id": row["target_document_id"],
                "target_document_source": row["target_source"],
                "target_match_scope": row.get("target_match_scope"),
                "target_proceeding_key": row.get("target_proceeding_key"),
                "reference_text": row.get("reference_text"),
                "reference_type": row.get("reference_type"),
                "raw_start": row.get("raw_start"),
                "raw_end": row.get("raw_end"),
                "link_method": row.get("link_method"),
            }
            dst.write(json.dumps(edge, ensure_ascii=False) + "\n")
            count += 1

    print("--- ANALYSIS-READY EXPORT COMPLETE ---")
    print(f"Edges:  {count}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
