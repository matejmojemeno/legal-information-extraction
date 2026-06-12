#!/usr/bin/env python3
"""
Build a stratified document sample manifest for annotation.

Example:
  python3 scripts/04_sample_annotation_set.py --per-source 12
"""

from __future__ import annotations

import argparse
import json
import os
import random


def _list_txt_files(folder: str) -> list[str]:
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.endswith(".txt")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a stratified annotation sample manifest."
    )
    parser.add_argument(
        "--processed-root",
        default="data/processed",
        help="Root directory containing source subfolders with .txt files.",
    )
    parser.add_argument(
        "--per-source",
        type=int,
        default=10,
        help="How many files to sample from each source folder.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/manifests/annotation_seed_manifest.jsonl",
        help="Output JSONL path.",
    )
    args = parser.parse_args()

    random.seed(args.seed)

    if not os.path.isdir(args.processed_root):
        raise SystemExit(f"Missing folder: {args.processed_root}")

    source_dirs = [
        os.path.join(args.processed_root, d)
        for d in sorted(os.listdir(args.processed_root))
        if os.path.isdir(os.path.join(args.processed_root, d))
    ]
    if not source_dirs:
        raise SystemExit(f"No source folders in {args.processed_root}")

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    rows: list[dict] = []
    for source_dir in source_dirs:
        source = os.path.basename(source_dir)
        files = _list_txt_files(source_dir)
        if not files:
            continue
        n = min(args.per_source, len(files))
        sampled = random.sample(files, n)
        sampled.sort()
        for path in sampled:
            rows.append(
                {
                    "source": source,
                    "document_id": os.path.basename(path),
                    "path": path,
                }
            )

    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Saved {len(rows)} sampled documents to {args.output}")


if __name__ == "__main__":
    main()
