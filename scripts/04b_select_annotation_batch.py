#!/usr/bin/env python3
"""
Select a high-value next batch of documents for manual annotation.

The goal is not pure randomness. We want a compact batch that:
- covers all sources
- contains different citation phenomena
- avoids both trivial and overwhelming documents

Example:
  python3 scripts/04b_select_annotation_batch.py --per-source 2
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass

# Ensure project root is importable when script is run directly.
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.citation_extractor import SECTION_PATTERN

LAW_ID_PATTERN = re.compile(r"\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.")
ARTICLE_PATTERN = re.compile(r"\bčl\.\s*[IVXLC0-9]+", re.IGNORECASE)
ANAPHORA_PATTERN = re.compile(
    r"\b(citovan(?:ého|é|ý)|téhož|tohoto|uveden(?:ého|é|ý))\s+"
    r"(zákona|předpisu|ustanovení|článku|paragrafu)\b",
    re.IGNORECASE,
)
ALIAS_DECL_PATTERN = re.compile(
    r"(dále\s+jen\s+[\"„][^\"“”]{1,80}[\"“”])|"
    r"(zkratk(?:a|ou)\s+[\"„][^\"“”]{1,80}[\"“”])",
    re.IGNORECASE,
)
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class DocStats:
    source: str
    document_id: str
    path: str
    char_len: int
    section_count: int
    law_id_count: int
    article_count: int
    anaphora_count: int
    alias_decl_count: int
    multi_section_sentences: int
    candidate_count: int
    score: float


def _discover_docs(processed_root: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for source in sorted(os.listdir(processed_root)):
        source_dir = os.path.join(processed_root, source)
        if not os.path.isdir(source_dir):
            continue
        for name in sorted(os.listdir(source_dir)):
            if name.endswith(".txt"):
                rows.append((source, name, os.path.join(source_dir, name)))
    return rows


def _load_excluded_document_ids(manifest_path: str | None) -> set[str]:
    if not manifest_path or not os.path.exists(manifest_path):
        return set()

    excluded: set[str] = set()
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            document_id = str(row.get("document_id") or "").strip()
            if document_id:
                excluded.add(document_id)
    return excluded


def _multi_section_sentence_count(text: str) -> int:
    count = 0
    for sentence in SENTENCE_SPLIT_PATTERN.split(text):
        if len(sentence) < 20:
            continue
        if len(SECTION_PATTERN.findall(sentence)) >= 2:
            count += 1
    return count


def _score(candidate_count: int, stats: dict[str, int], char_len: int) -> float:
    score = 0.0

    if 3 <= candidate_count <= 20:
        score += 3.0
    elif 21 <= candidate_count <= 35:
        score += 1.5
    elif candidate_count > 45:
        score -= 2.5

    if 1000 <= char_len <= 30000:
        score += 1.0

    score += min(stats["multi_section_sentences"], 3) * 3.0
    score += min(stats["anaphora_count"], 3) * 2.5
    score += min(stats["alias_decl_count"], 2) * 2.0
    score += min(stats["article_count"], 2) * 1.5
    score += min(stats["law_id_count"], 3) * 1.0

    if stats["section_count"] > 0 and stats["law_id_count"] > 0:
        score += 1.5
    if stats["section_count"] > 0 and stats["alias_decl_count"] > 0:
        score += 1.0

    return score


def _analyze_doc(source: str, document_id: str, path: str) -> DocStats | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    section_count = len(SECTION_PATTERN.findall(text))
    law_id_count = len(LAW_ID_PATTERN.findall(text))
    article_count = len(ARTICLE_PATTERN.findall(text))
    anaphora_count = len(ANAPHORA_PATTERN.findall(text))
    alias_decl_count = len(ALIAS_DECL_PATTERN.findall(text))
    multi_section_sentences = _multi_section_sentence_count(text)
    candidate_count = section_count + law_id_count
    char_len = len(text)

    stats = {
        "section_count": section_count,
        "law_id_count": law_id_count,
        "article_count": article_count,
        "anaphora_count": anaphora_count,
        "alias_decl_count": alias_decl_count,
        "multi_section_sentences": multi_section_sentences,
    }

    return DocStats(
        source=source,
        document_id=document_id,
        path=path,
        char_len=char_len,
        section_count=section_count,
        law_id_count=law_id_count,
        article_count=article_count,
        anaphora_count=anaphora_count,
        alias_decl_count=alias_decl_count,
        multi_section_sentences=multi_section_sentences,
        candidate_count=candidate_count,
        score=_score(candidate_count, stats, char_len),
    )


def _feature_vector(doc: DocStats) -> tuple[int, int, int, int, int]:
    return (
        1 if doc.multi_section_sentences > 0 else 0,
        1 if doc.anaphora_count > 0 else 0,
        1 if doc.alias_decl_count > 0 else 0,
        1 if doc.article_count > 0 else 0,
        1 if doc.law_id_count > 0 else 0,
    )


def _select_for_source(docs: list[DocStats], k: int) -> list[DocStats]:
    if not docs or k <= 0:
        return []

    ordered = sorted(
        docs,
        key=lambda d: (
            -d.score,
            abs(d.candidate_count - 10),
            d.char_len,
            d.document_id,
        ),
    )

    selected: list[DocStats] = []
    covered = [0, 0, 0, 0, 0]

    while ordered and len(selected) < k:
        best_idx = 0
        best_gain = (-1, -1.0)

        for i, doc in enumerate(ordered):
            vec = _feature_vector(doc)
            new_coverage = sum(1 for a, b in zip(covered, vec) if a == 0 and b == 1)
            gain = (new_coverage, doc.score)
            if gain > best_gain:
                best_gain = gain
                best_idx = i

        chosen = ordered.pop(best_idx)
        selected.append(chosen)
        covered = [max(a, b) for a, b in zip(covered, _feature_vector(chosen))]

    return selected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select the next batch of documents for manual annotation."
    )
    parser.add_argument(
        "--processed-root",
        default="data/processed",
        help="Root directory containing source subfolders with .txt files.",
    )
    parser.add_argument(
        "--per-source",
        type=int,
        default=2,
        help="How many documents to recommend per source.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=35,
        help="Skip documents with more than this many raw candidates.",
    )
    parser.add_argument(
        "--min-candidates",
        type=int,
        default=3,
        help="Skip documents with fewer than this many raw candidates.",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/manifests/next_annotation_batch.jsonl",
        help="Output JSONL manifest path.",
    )
    parser.add_argument(
        "--exclude-manifest",
        default="data/annotations/manifests/dataset_manifest.jsonl",
        help="Existing dataset manifest whose document_ids should be excluded.",
    )
    args = parser.parse_args()

    docs = _discover_docs(args.processed_root)
    excluded_document_ids = _load_excluded_document_ids(args.exclude_manifest)
    stats_rows: list[DocStats] = []

    for source, document_id, path in docs:
        if document_id in excluded_document_ids:
            continue
        stats = _analyze_doc(source, document_id, path)
        if stats is None:
            continue
        if stats.candidate_count < args.min_candidates:
            continue
        if stats.candidate_count > args.max_candidates:
            continue
        stats_rows.append(stats)

    by_source: dict[str, list[DocStats]] = {}
    for row in stats_rows:
        by_source.setdefault(row.source, []).append(row)

    selected: list[DocStats] = []
    for source in sorted(by_source):
        selected.extend(_select_for_source(by_source[source], args.per_source))

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        for row in selected:
            f.write(
                json.dumps(
                    {
                        "source": row.source,
                        "document_id": row.document_id,
                        "path": row.path,
                        "selection_score": round(row.score, 2),
                        "candidate_count": row.candidate_count,
                        "section_count": row.section_count,
                        "law_id_count": row.law_id_count,
                        "article_count": row.article_count,
                        "anaphora_count": row.anaphora_count,
                        "alias_decl_count": row.alias_decl_count,
                        "multi_section_sentences": row.multi_section_sentences,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"Saved {len(selected)} documents to {args.output}")
    for row in selected:
        print(
            f"[{row.source}] {row.document_id} | score={row.score:.1f} "
            f"| cand={row.candidate_count} | multi={row.multi_section_sentences} "
            f"| anaph={row.anaphora_count} | alias={row.alias_decl_count} "
            f"| law_id={row.law_id_count} | article={row.article_count}"
        )


if __name__ == "__main__":
    main()
