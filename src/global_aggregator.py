"""
Global Alias Aggregator — majority voting across a corpus.

Implements Rule 6 from the NLP Extraction Rulebook:
  - Minimum frequency ≥ 2
  - Dominant ID must account for > 85% of all mappings
  - Ambiguous/conflicting aliases are discarded
"""

import json
import os
from collections import defaultdict

from src.alias_extractor import extract_local_aliases


# Rule 6 thresholds
MIN_FREQUENCY = 2
DOMINANCE_THRESHOLD = 0.85

# --- Global Rule: Generic / meaningless aliases to reject from global dict ---
_GLOBAL_GENERIC_ALIASES = {
    "zákon",
    "zákona",
    "zákonu",
    "zákonem",
    "zákoně",
    "zákony",
    "vyhláška",
    "vyhlášky",
    "vyhlášce",
    "vyhláškou",
    "nařízení",
    "směrnice",
    "předpis",
    "předpisu",
    "novela",
    "novely",
    "novelou",
    "s.r.o.",
    "s. r. o.",
}


def _collect_txt_files(folders: list[str]) -> list[str]:
    """Collect all .txt file paths from one or more directories."""
    all_files = []
    for folder in folders:
        for f in sorted(os.listdir(folder)):
            if f.endswith(".txt"):
                all_files.append(os.path.join(folder, f))
    return all_files


def build_global_dictionary(
    folders: str | list[str],
    output_json: str = "data/dicts/global_aliases.json",
    limit: int | None = None,
    verbose_aliases: bool = False,
    progress_every: int = 1000,
) -> dict[str, str]:
    """
    Pass 1: Scan all documents and aggregate alias→law_id mappings.
    Apply majority voting to produce a safe global dictionary.

    Args:
        folders: Directory or list of directories containing .txt files.
        output_json: Path for the output JSON file.
        limit: Optional cap on total number of documents to process.
        verbose_aliases: Print every found alias mapping (debug only).
        progress_every: Emit a coarse progress line every N documents.

    Returns:
        dict of safe global aliases (alias → law_id).
    """
    if isinstance(folders, str):
        folders = [folders]

    print("--- BUILDING GLOBAL DICTIONARY ---")
    print(f"Corpus folders: {folders}")

    txt_files = _collect_txt_files(folders)
    if limit:
        txt_files = txt_files[:limit]
        print(f"Processing sample: {limit} documents...\n")
    else:
        print(f"Processing all {len(txt_files)} documents...\n")

    # Track all alias -> all seen law IDs and their occurrence counts.
    alias_id_counts = defaultdict(lambda: defaultdict(int))
    processed_count = 0

    # 1. Read documents and mine aliases
    for txt_path in txt_files:
        filename = os.path.basename(txt_path)
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                full_text = f.read()

            local_aliases = extract_local_aliases(full_text)

            for alias, law_id in local_aliases.items():
                if verbose_aliases:
                    print(f"[FOUND] File: {filename} | Alias: '{alias}' -> {law_id}")
                alias_id_counts[alias][law_id] += 1

            processed_count += 1
            if progress_every and processed_count % progress_every == 0:
                print(
                    f"[PROGRESS] {processed_count}/{len(txt_files)} documents | "
                    f"unique aliases seen: {len(alias_id_counts)}"
                )

        except Exception as e:
            print(f"Error reading {filename}: {e}")

    # 2. Rule 6: Majority voting
    strict_global_aliases = {}
    conflict_aliases = {}

    for alias, id_counts in alias_id_counts.items():
        total = sum(id_counts.values())

        # Must appear at least MIN_FREQUENCY times
        if total < MIN_FREQUENCY:
            continue

        # Reject generic aliases from the global dictionary
        if alias.lower() in _GLOBAL_GENERIC_ALIASES:
            continue

        # Find dominant ID and check its share
        dominant_id = max(id_counts, key=id_counts.get)
        dominance = id_counts[dominant_id] / total

        if dominance > DOMINANCE_THRESHOLD:
            strict_global_aliases[alias] = dominant_id
        else:
            conflict_aliases[alias] = dict(id_counts)

    # 3. Save results
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(strict_global_aliases, f, ensure_ascii=False, indent=4)

    # Save conflict aliases to a separate file for LLM review
    conflict_json = os.path.join(os.path.dirname(output_json), "conflict_aliases.json")
    with open(conflict_json, "w", encoding="utf-8") as f:
        json.dump(conflict_aliases, f, ensure_ascii=False, indent=4)

    # 4. Print stats
    print("\n--- DICTIONARY STATS ---")
    print(f"Saved: {len(strict_global_aliases)} unique safe global aliases.")
    print(f"Discarded (conflicts): {len(conflict_aliases)} aliases.")
    print(f"Output: {output_json}")
    print(f"Conflicts Output: {conflict_json}")

    if conflict_aliases:
        print("\nSample discarded (conflicting) aliases:")
        for alias, ids in list(conflict_aliases.items())[:10]:
            print(f"  - '{alias}' mapped to: {ids}")

    return strict_global_aliases
