#!/usr/bin/env python3
"""
Run the standard reviewed document-reference link benchmark.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = [
    "data/annotations/document_reference_links/gold/nalus_link_gold_v1.jsonl",
    "data/annotations/document_reference_links/gold/uohs_link_gold_v1.jsonl",
    "data/annotations/document_reference_links/gold/ns_link_gold_v1.jsonl",
    "data/annotations/document_reference_links/gold/nss_link_gold_v1.jsonl",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the standard document-reference link regression benchmark."
    )
    parser.add_argument(
        "--predicted",
        default="data/processed/document_reference_links_with_parent_ids_v2.jsonl",
        help="Predicted link JSONL to score.",
    )
    parser.add_argument(
        "--gold",
        nargs="*",
        default=DEFAULT_GOLD,
        help="Gold JSONL files to include. Defaults to the standard four reviewed slices.",
    )
    parser.add_argument(
        "--tag",
        default="current",
        help="Tag for output file names under data/annotations/document_reference_links/eval/.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    eval_dir = PROJECT_ROOT / "data" / "annotations" / "document_reference_links" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    json_output = eval_dir / f"link_eval_{args.tag}.json"
    md_output = eval_dir / f"link_eval_{args.tag}.md"

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "21_evaluate_document_reference_links.py"),
        "--predicted",
        args.predicted,
        "--gold",
        *args.gold,
        "--json-output",
        str(json_output),
        "--md-output",
        str(md_output),
    ]

    env = os.environ.copy()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        return result.returncode

    print("--- STANDARD LINK REGRESSION COMPLETE ---")
    print(f"Predicted links:  {args.predicted}")
    print(f"Gold slices:      {len(args.gold)}")
    print(f"JSON report:      {json_output}")
    print(f"Markdown report:  {md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
