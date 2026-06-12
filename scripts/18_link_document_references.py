#!/usr/bin/env python3
"""Official production script: link mined document references to corpus documents.

This script reads the canonical corpus, builds a self-identifier index for the
documents, and links mined document-reference occurrences to in-corpus targets.
It can run in a memory-safe streaming mode and is suitable for standalone full-
corpus relinking when extraction outputs already exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.document_reference_linker import link_corpus_document_references, stream_corpus_document_references
from src.production_paths import PRODUCTION_CORPUS_ROOT, PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH


def _write_jsonl(path: str, rows: list[dict]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Official production linker: map mined document references to "
            "canonical corpus documents and write linked/unresolved/self-id outputs."
        )
    )
    parser.add_argument(
        "--processed-root",
        default=PRODUCTION_CORPUS_ROOT,
        help="Canonical corpus root with per-source .txt documents.",
    )
    parser.add_argument(
        "--linked-output",
        default="data/processed/document_reference_links.jsonl",
        help="Output JSONL for successfully linked reference occurrences.",
    )
    parser.add_argument(
        "--unresolved-output",
        default="data/processed/document_reference_unresolved.jsonl",
        help="Output JSONL for unresolved reference occurrences.",
    )
    parser.add_argument(
        "--self-id-output",
        default="data/processed/document_self_identifiers.jsonl",
        help="Output JSONL for the built corpus self-identifier index entries.",
    )
    parser.add_argument(
        "--external-self-id-metadata",
        default=PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH,
        help=(
            "Optional JSONL(.gz) with external self-identifiers keyed by "
            "source/document_id, e.g. parent export rows containing judicate_name."
        ),
    )
    parser.add_argument(
        "--checkpoint-path",
        default=None,
        help="Optional JSONL checkpoint with one processed-document row per completed linking unit.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Print linking progress every N processed documents.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream outputs incrementally instead of holding all links in memory; recommended for large runs.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a prior streaming run using --checkpoint-path.",
    )
    parser.add_argument(
        "--use-fuzzy-candidates",
        action="store_true",
        help=(
            "Enable fuzzy candidate generation for unresolved references. "
            "Disabled by default to keep deterministic production relinking faster."
        ),
    )
    args = parser.parse_args()

    if args.stream:
        checkpoint_path = args.checkpoint_path
        if checkpoint_path is None:
            checkpoint_path = os.path.join(
                os.path.dirname(args.linked_output) or ".",
                "document_reference_linking_checkpoint.jsonl",
            )
        summary = stream_corpus_document_references(
            processed_root=args.processed_root,
            linked_output_path=args.linked_output,
            unresolved_output_path=args.unresolved_output,
            self_id_output_path=args.self_id_output,
            external_metadata_path=args.external_self_id_metadata,
            checkpoint_path=checkpoint_path,
            progress_every_docs=args.progress_every,
            resume=args.resume,
            use_fuzzy_candidates=args.use_fuzzy_candidates,
        )
        print("--- DOCUMENT REFERENCE LINKING COMPLETE ---")
        print(f"Processed documents:    {summary['processed_documents']}/{summary['total_documents']}")
        print(f"Skipped documents:      {summary['skipped_documents']}")
        print(f"Linked references:      {summary['linked']}")
        print(f"Unresolved references:  {summary['unresolved']}")
        print(f"Self identifiers:       {summary['self_identifiers']}")
        print(f"Checkpoint:             {checkpoint_path}")
        print(f"Linked output:          {args.linked_output}")
        print(f"Unresolved output:      {args.unresolved_output}")
        print(f"Self-id output:         {args.self_id_output}")
    else:
        linked, unresolved, self_identifiers = link_corpus_document_references(
            processed_root=args.processed_root,
            external_metadata_path=args.external_self_id_metadata,
        )

        _write_jsonl(args.linked_output, [row.to_dict() for row in linked])
        _write_jsonl(args.unresolved_output, unresolved)
        _write_jsonl(
            args.self_id_output,
            [
                {
                    "document_id": row.document_id,
                    "document_path": row.document_path,
                    "source": row.source,
                    "identifier_text": row.identifier_text,
                    "identifier_kind": row.identifier_kind,
                    "origin": row.origin,
                    "keys": list(row.keys),
                    "source_iri": row.source_iri,
                }
                for row in self_identifiers
            ],
        )

        by_method = Counter(row.link_method for row in linked)
        print("--- DOCUMENT REFERENCE LINKING COMPLETE ---")
        print(f"Linked references:      {len(linked)}")
        print(f"Unresolved references:  {len(unresolved)}")
        print(f"Self identifiers:       {len(self_identifiers)}")
        print(f"Link methods:           {dict(sorted(by_method.items()))}")
        print(f"Linked output:          {args.linked_output}")
        print(f"Unresolved output:      {args.unresolved_output}")
        print(f"Self-id output:         {args.self_id_output}")


if __name__ == "__main__":
    main()
