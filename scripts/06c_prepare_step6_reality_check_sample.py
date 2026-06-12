#!/usr/bin/env python3
"""
Prepare a reproducible, diverse reality-check sample for Step 6 LLM resolution.

The source queue is the real unresolved anomaly queue. This script samples a
small but varied subset so we can sanity-check production behavior before
running large expensive batches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


ARTICLE_PATTERN = re.compile(r"^\s*(čl\.?|cl\.?)", re.IGNORECASE)
SECTION_PATTERN = re.compile(r"^\s*§", re.IGNORECASE)
TREATYISH_PATTERN = re.compile(
    r"\b(Úmluv|Dohod|Protokol|rámcov[ée]\s+rozhodnut|Framework Decision)\b",
    re.IGNORECASE,
)
FOREIGN_STATUTE_PATTERN = re.compile(r"\b(StGB|InsO)\b")
INTERNAL_RULE_PATTERN = re.compile(
    r"\b(Kárn[ýé]\s+řád|Jednac[íi]\s+řád|Řád Rozhodčího soudu)\b",
    re.IGNORECASE,
)
CRIMINAL_PATTERN = re.compile(
    r"\b(tr\.\s*zák|trestn[íi]\s+zákon|trestn[íi]\s+řád|krádež|obžalovan|odsouzen)\b",
    re.IGNORECASE,
)
CIVIL_PATTERN = re.compile(
    r"\b(o\.\s*s\.\s*ř\.|dědick|zůstavitel|řízení pokračovalo)\b",
    re.IGNORECASE,
)


def _entry_hash(
    target_reference: str,
    context_block: str,
    document_path: str = "",
    raw_start: int | None = None,
    raw_end: int | None = None,
    citation_type: str = "",
) -> str:
    payload = (
        f"{document_path}\n{raw_start}\n{raw_end}\n{citation_type}\n"
        f"{target_reference}\n{context_block}"
    ).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _load_queue(path: str) -> list[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, list):
        raise SystemExit(f"Queue must be a JSON list: {path}")
    return obj


def _default_input_path() -> str:
    preferred = Path("data/processed/to_be_checked_by_llm.backfilled.json")
    if preferred.exists():
        return str(preferred)
    return "data/processed/to_be_checked_by_llm.json"


def _annotate(entry: dict) -> dict:
    target_reference = str(entry.get("target_reference", "")).strip()
    context_block = str(entry.get("context_block", "")).strip()
    candidates = entry.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    combined = f"{target_reference} {context_block}"

    tags: list[str] = []
    if ARTICLE_PATTERN.search(target_reference):
        tags.append("article")
    if SECTION_PATTERN.search(target_reference):
        tags.append("section")
    if TREATYISH_PATTERN.search(combined):
        tags.append("treatyish")
    if FOREIGN_STATUTE_PATTERN.search(combined):
        tags.append("foreign_statute")
    if INTERNAL_RULE_PATTERN.search(combined):
        tags.append("internal_rule")
    if CRIMINAL_PATTERN.search(combined):
        tags.append("criminalish")
    if CIVIL_PATTERN.search(combined):
        tags.append("civilish")
    if not tags:
        tags.append("generic")

    candidate_count = len(candidates)
    if candidate_count == 0:
        tags.append("cand_0")
    elif candidate_count <= 2:
        tags.append("cand_1_2")
    elif candidate_count <= 5:
        tags.append("cand_3_5")
    else:
        tags.append("cand_6_plus")

    return {
        "entry_id": str(
            entry.get("entry_id")
            or _entry_hash(
                target_reference,
                context_block,
                document_path=str(entry.get("document_path", "")),
                raw_start=entry.get("raw_start"),
                raw_end=entry.get("raw_end"),
                citation_type=str(entry.get("citation_type", "")),
            )
        ),
        "target_reference": target_reference,
        "context_block": context_block,
        "candidates": candidates,
        "resolver_stage": entry.get("resolver_stage", ""),
        "confidence": entry.get("confidence", 0.0),
        "tags": tags,
        "document_id": entry.get("document_id"),
        "document_path": entry.get("document_path"),
        "source": entry.get("source"),
        "raw_start": entry.get("raw_start"),
        "raw_end": entry.get("raw_end"),
        "citation_type": entry.get("citation_type"),
        "route_reason": entry.get("route_reason"),
    }


def _stable_sort_key(row: dict) -> tuple:
    return (
        row["target_reference"],
        row["entry_id"],
    )


def _take(rows: list[dict], used: set[str], limit: int) -> list[dict]:
    picked: list[dict] = []
    for row in sorted(rows, key=_stable_sort_key):
        if row["entry_id"] in used:
            continue
        used.add(row["entry_id"])
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a diverse Step 6 reality-check sample.")
    parser.add_argument(
        "--input",
        default=_default_input_path(),
        help="Real Step 6 queue JSON. Prefers the backfilled queue when present.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/eval/llm_step6_reality_check_sample_v1.json",
        help="Sample output JSON.",
    )
    parser.add_argument(
        "--summary-output",
        default="data/annotations/eval/llm_step6_reality_check_sample_v1.summary.json",
        help="Summary output JSON.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=50,
        help="Target sample size.",
    )
    args = parser.parse_args()

    rows = [_annotate(row) for row in _load_queue(args.input)]
    used: set[str] = set()
    sample: list[dict] = []

    buckets: list[tuple[str, int, list[dict]]] = [
        ("foreign_statute", 5, [r for r in rows if "foreign_statute" in r["tags"]]),
        ("treatyish_articles", 10, [r for r in rows if "treatyish" in r["tags"] and "article" in r["tags"]]),
        ("internal_rule", 3, [r for r in rows if "internal_rule" in r["tags"]]),
        ("criminalish_sparse", 8, [r for r in rows if "criminalish" in r["tags"] and ("cand_0" in r["tags"] or "cand_1_2" in r["tags"])]),
        ("civilish", 4, [r for r in rows if "civilish" in r["tags"]]),
        ("cand_0_generic", 10, [r for r in rows if "cand_0" in r["tags"] and "treatyish" not in r["tags"] and "foreign_statute" not in r["tags"]]),
        ("cand_1_2_generic", 6, [r for r in rows if "cand_1_2" in r["tags"] and "criminalish" not in r["tags"] and "treatyish" not in r["tags"]]),
        ("cand_3_5_generic", 3, [r for r in rows if "cand_3_5" in r["tags"] and "treatyish" not in r["tags"]]),
        ("cand_6_plus", 1, [r for r in rows if "cand_6_plus" in r["tags"]]),
    ]

    bucket_counts: dict[str, int] = {}
    for name, limit, bucket_rows in buckets:
        picked = _take(bucket_rows, used, limit)
        sample.extend(picked)
        bucket_counts[name] = len(picked)

    if len(sample) < args.size:
        fallback = _take(rows, used, args.size - len(sample))
        sample.extend(fallback)
        bucket_counts["fallback"] = len(fallback)

    sample = sample[: args.size]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "input": args.input,
        "output": args.output,
        "target_size": args.size,
        "actual_size": len(sample),
        "bucket_counts": bucket_counts,
    }
    Path(args.summary_output).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
