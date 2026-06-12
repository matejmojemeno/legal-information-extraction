#!/usr/bin/env python3
"""
Step 2: Build the global alias dictionary (Pass 1).

Runs alias_extractor on all documents in a corpus folder, aggregates
results with majority voting, and writes data/dicts/global_aliases.json.
"""

import argparse
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.global_aggregator import build_global_dictionary


def main():
    parser = argparse.ArgumentParser(
        description="Build global alias dictionary from a corpus of extracted .txt files."
    )
    parser.add_argument(
        "corpus_folder",
        nargs="+",
        help="One or more folders containing .txt documents.",
    )
    parser.add_argument(
        "--output",
        default="data/dicts/global_aliases.json",
        help="Output path for the global aliases JSON (default: data/dicts/global_aliases.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional: limit number of documents to process.",
    )
    parser.add_argument(
        "--verbose-aliases",
        action="store_true",
        help="Print every alias mapping found (debug only, very noisy on large corpora).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Emit progress every N documents (default: 1000).",
    )
    args = parser.parse_args()

    build_global_dictionary(
        folders=args.corpus_folder,
        output_json=args.output,
        limit=args.limit,
        verbose_aliases=args.verbose_aliases,
        progress_every=args.progress_every,
    )


if __name__ == "__main__":
    main()
