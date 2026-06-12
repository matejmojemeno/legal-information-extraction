#!/usr/bin/env python3
"""Build a global alias dictionary from the imported parent-project corpus."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.global_aggregator import build_global_dictionary


DEFAULT_IMPORT_ROOT = "data/imports/parent_bucket_texts"
DEFAULT_OUTPUT = "data/dicts/global_aliases_full.json"


def discover_source_folders(import_root: str) -> list[str]:
    root = Path(import_root)
    if not root.exists():
        return []
    folders = []
    for name in ("ns", "nss", "uohs", "nalus"):
        path = root / name
        if path.is_dir():
            folders.append(str(path))
    return folders


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a global alias dictionary from the imported full corpus.",
    )
    parser.add_argument(
        "--import-root",
        default=DEFAULT_IMPORT_ROOT,
        help=f"Root directory of imported texts (default: {DEFAULT_IMPORT_ROOT}).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of documents processed.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5000,
        help="Emit progress every N documents (default: 5000).",
    )
    parser.add_argument(
        "--verbose-aliases",
        action="store_true",
        help="Print every found alias mapping (debug only).",
    )
    args = parser.parse_args()

    folders = discover_source_folders(args.import_root)
    if not folders:
        raise SystemExit(f"No source folders found under {args.import_root}")

    print("Building full-corpus alias dictionary from:")
    for folder in folders:
        print(f"  - {folder}")

    build_global_dictionary(
        folders=folders,
        output_json=args.output,
        limit=args.limit,
        verbose_aliases=args.verbose_aliases,
        progress_every=args.progress_every,
    )

    conflict_output = str(Path(args.output).with_name("conflict_aliases_full.json"))
    default_conflict = str(Path(args.output).with_name("conflict_aliases.json"))
    if Path(default_conflict).exists() and default_conflict != conflict_output:
        Path(default_conflict).replace(conflict_output)
        print(f"Moved conflict aliases to: {conflict_output}")


if __name__ == "__main__":
    main()
