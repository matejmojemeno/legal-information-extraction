#!/usr/bin/env python3
"""
Step 3: Run the citation extractor (Pass 3).

Scans all .txt documents for citation candidates using both local aliases
and the global dictionary built in Step 2.
"""

import argparse
import json
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import (
    append_anomalies_to_file,
    append_occurrences_to_jsonl,
    extract_citation_occurrences,
    occurrences_to_resolved_and_anomalies,
)
from src.document_metadata import load_document_dates_index, load_law_timelines

SEEDED_ALIASES_PATH = "data/dicts/seed_aliases.json"
AUDITED_ALIASES_PATH = "data/dicts/audited_aliases.json"
ANOMALY_QUEUE_PATH = "data/processed/to_be_checked_by_llm.json"
CITATION_OCCURRENCES_PATH = "data/processed/citation_occurrences.jsonl"


def main():
    parser = argparse.ArgumentParser(
        description="Scan documents for suspicious § references."
    )
    parser.add_argument(
        "corpus_folder",
        help="Path to folder containing .txt document files.",
    )
    parser.add_argument(
        "--global-dict",
        default="data/dicts/global_aliases.json",
        help="Path to global aliases JSON (default: data/dicts/global_aliases.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max number of documents to process (default: 20).",
    )
    parser.add_argument(
        "--queue-low-confidence",
        action="store_true",
        help="Also queue only ambiguity-heavy low-confidence resolved citations for LLM review.",
    )
    args = parser.parse_args()

    global_aliases, alias_source = load_runtime_aliases(
        audited_path=AUDITED_ALIASES_PATH,
        global_path=args.global_dict,
        seeded_path=SEEDED_ALIASES_PATH,
    )
    if alias_source == "empty":
        print(f"Warning: no runtime aliases found. Using empty global dictionary.")
    else:
        print(f"Loaded runtime aliases ({alias_source})")

    # Process documents
    txt_files = [f for f in os.listdir(args.corpus_folder) if f.endswith(".txt")]
    txt_files = txt_files[: args.limit]

    print(f"\n--- CITATION EXTRACTION ---")
    print(f"Scanning {len(txt_files)} documents...\n")

    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()
    if document_dates_index:
        print(f"Loaded normalized document dates: {len(document_dates_index)}")
    if law_timelines:
        print(f"Loaded law timelines: {len(law_timelines)}")

    if os.path.exists(ANOMALY_QUEUE_PATH):
        os.remove(ANOMALY_QUEUE_PATH)
    if os.path.exists(CITATION_OCCURRENCES_PATH):
        os.remove(CITATION_OCCURRENCES_PATH)

    total_suspicious = 0
    total_resolved = 0
    total_occurrences = 0
    batch_anomalies = []

    for i, filename in enumerate(txt_files, start=1):
        txt_path = os.path.join(args.corpus_folder, filename)
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                full_text = f.read()

            local_aliases = extract_local_aliases(full_text)
            occurrences = extract_citation_occurrences(
                full_text,
                local_aliases,
                global_aliases,
                document_metadata=document_dates_index.get(
                    (os.path.basename(args.corpus_folder), filename)
                ),
                law_timelines=law_timelines or None,
            )
            append_occurrences_to_jsonl(
                occurrences, CITATION_OCCURRENCES_PATH, document_id=filename
            )
            resolved, anomalies = occurrences_to_resolved_and_anomalies(
                occurrences,
                full_text,
                include_low_confidence_anomalies=args.queue_low_confidence,
            )

            total_occurrences += len(occurrences)
            total_resolved += len(resolved)
            batch_anomalies.extend(anomalies)

            if anomalies:
                print(f"\n[FILE: {filename}]")
                print(f"Local aliases: {list(local_aliases.keys())}")
                print(f"Found {len(anomalies)} anomalies:")
                for anomaly in anomalies:
                    print(f"  -> {anomaly['target_reference']} in context...")
                total_suspicious += len(anomalies)

            print(
                f"[PROGRESS] {i}/{len(txt_files)} | "
                f"file={filename} | citations={total_occurrences} | "
                f"resolved={total_resolved} | anomalies={total_suspicious}"
            )

        except Exception as e:
            print(f"Error reading {filename}: {e}")

    append_anomalies_to_file(batch_anomalies, ANOMALY_QUEUE_PATH)

    print(f"\n--- DONE ---")
    print(f"Total citation occurrences:  {total_occurrences}")
    print(f"Total resolved references:   {total_resolved}")
    print(f"Total suspicious anomalies:  {total_suspicious}")
    print(f"Citations output:            {CITATION_OCCURRENCES_PATH}")
    print(f"Anomaly output:              {ANOMALY_QUEUE_PATH}")


if __name__ == "__main__":
    main()
