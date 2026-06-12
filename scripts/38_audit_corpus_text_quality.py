#!/usr/bin/env python3
"""
Audit normalized corpus text quality on a deterministic per-source sample.

The audit is intentionally lightweight and reproducible. It does not try to
judge legal correctness. It measures surface text quality after normalization:
empty/very short documents, likely OCR or encoding artifacts, layout noise, and
generally readable text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_CORPUS_ROOT = "data/imports/parent_bucket_texts"
DEFAULT_OUTPUT_JSON = "data/metadata/corpus_text_quality_audit.json"
DEFAULT_OUTPUT_MD = "data/metadata/corpus_text_quality_audit.md"
SOURCE_DIRS = ("nalus", "ns", "nss", "uohs")


def _stable_score(path: Path, seed: str) -> str:
    payload = f"{seed}:{path.as_posix()}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _sample_paths(paths: list[Path], sample_size: int, seed: str) -> list[Path]:
    return sorted(paths, key=lambda p: _stable_score(p, seed))[:sample_size]


def _safe_ratio(num: int | float, den: int | float) -> float:
    return float(num) / float(den) if den else 0.0


def _line_features(text: str) -> dict[str, float]:
    lines = [line.strip() for line in text.splitlines()]
    nonempty = [line for line in lines if line]
    line_count = len(lines)
    nonempty_count = len(nonempty)
    short_lines = sum(1 for line in nonempty if len(line) <= 20)
    tiny_lines = sum(1 for line in nonempty if len(line) <= 3)
    page_like = sum(
        1
        for line in nonempty
        if re.fullmatch(r"(?:\d+|strana\s+\d+|\-\s*\d+\s*\-)", line, flags=re.IGNORECASE)
    )
    duplicate_lines = nonempty_count - len(set(nonempty))
    avg_line_len = mean(len(line) for line in nonempty) if nonempty else 0.0

    return {
        "line_count": float(line_count),
        "nonempty_line_count": float(nonempty_count),
        "avg_line_length": avg_line_len,
        "short_line_ratio": _safe_ratio(short_lines, nonempty_count),
        "tiny_line_ratio": _safe_ratio(tiny_lines, nonempty_count),
        "page_artifact_line_ratio": _safe_ratio(page_like, nonempty_count),
        "duplicate_line_ratio": _safe_ratio(duplicate_lines, nonempty_count),
    }


def _features(text: str) -> dict[str, float]:
    char_count = len(text)
    alnum = sum(1 for ch in text if ch.isalnum())
    whitespace = sum(1 for ch in text if ch.isspace())
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t")
    replacement = text.count("\ufffd")
    mojibake = len(re.findall(r"[ÃÂÄÅ][^\s]{0,3}", text))
    hyphen_breaks = len(re.findall(r"\w-\s*\n\s*\w", text))
    token_count = len(re.findall(r"\w+", text, flags=re.UNICODE))

    out = {
        "char_count": float(char_count),
        "token_count": float(token_count),
        "alnum_ratio": _safe_ratio(alnum, char_count),
        "whitespace_ratio": _safe_ratio(whitespace, char_count),
        "control_ratio": _safe_ratio(control, char_count),
        "replacement_char_ratio": _safe_ratio(replacement, char_count),
        "mojibake_hits_per_10k": _safe_ratio(mojibake * 10_000, char_count),
        "hyphen_breaks_per_10k": _safe_ratio(hyphen_breaks * 10_000, char_count),
    }
    out.update(_line_features(text))
    return out


def _classify(features: dict[str, float]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    chars = features["char_count"]
    tokens = features["token_count"]

    if chars < 500 or tokens < 50:
        reasons.append("very short or nearly empty text")
        return "empty_or_short", reasons

    severe_checks = [
        (features["replacement_char_ratio"] > 0.001, "replacement characters"),
        (features["mojibake_hits_per_10k"] > 8.0, "encoding artifacts"),
        (features["control_ratio"] > 0.002, "control characters"),
        (features["alnum_ratio"] < 0.45, "low alphanumeric share"),
        (
            features["avg_line_length"] < 18 and features["short_line_ratio"] > 0.55,
            "fragmented line layout",
        ),
        (features["duplicate_line_ratio"] > 0.45, "many repeated lines"),
        (features["page_artifact_line_ratio"] > 0.25, "many page-number lines"),
    ]
    for is_match, reason in severe_checks:
        if is_match:
            reasons.append(reason)
    if reasons:
        return "artifacts", [f"severe signal: {reason}" for reason in reasons]

    moderate_checks = [
        (features["replacement_char_ratio"] > 0.0, "some replacement characters"),
        (features["mojibake_hits_per_10k"] > 0.0, "some encoding artifacts"),
        (features["hyphen_breaks_per_10k"] > 12.0, "many line-break hyphenations"),
        (features["short_line_ratio"] > 0.35, "many short lines"),
        (features["duplicate_line_ratio"] > 0.20, "repeated lines"),
        (features["page_artifact_line_ratio"] > 0.08, "page-number lines"),
        (features["whitespace_ratio"] > 0.30, "high whitespace share"),
    ]
    for is_match, reason in moderate_checks:
        if is_match:
            reasons.append(reason)
    if reasons:
        return "artifacts", reasons

    return "readable", reasons


def _audit_document(path: Path, source: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    features = _features(text)
    category, reasons = _classify(features)
    return {
        "source": source,
        "path": str(path),
        "document_id": path.name,
        "category": category,
        "reasons": reasons,
        "features": {key: round(value, 6) for key, value in features.items()},
    }


def _summary(rows: list[dict[str, Any]], available_by_source: dict[str, int]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(row)

    source_summaries = {}
    for source in SOURCE_DIRS:
        source_rows = by_source.get(source, [])
        category_counts = Counter(row["category"] for row in source_rows)
        char_counts = [row["features"]["char_count"] for row in source_rows]
        token_counts = [row["features"]["token_count"] for row in source_rows]
        source_summaries[source] = {
            "available_documents": available_by_source.get(source, 0),
            "sampled_documents": len(source_rows),
            "category_counts": dict(category_counts),
            "category_shares": {
                category: round(_safe_ratio(count, len(source_rows)), 4)
                for category, count in sorted(category_counts.items())
            },
            "median_chars": round(median(char_counts), 1) if char_counts else 0.0,
            "median_tokens": round(median(token_counts), 1) if token_counts else 0.0,
            "mean_short_line_ratio": round(
                mean(row["features"]["short_line_ratio"] for row in source_rows), 4
            )
            if source_rows
            else 0.0,
            "mean_duplicate_line_ratio": round(
                mean(row["features"]["duplicate_line_ratio"] for row in source_rows), 4
            )
            if source_rows
            else 0.0,
            "mean_hyphen_breaks_per_10k": round(
                mean(row["features"]["hyphen_breaks_per_10k"] for row in source_rows), 4
            )
            if source_rows
            else 0.0,
        }

    return {
        "source_summaries": source_summaries,
        "overall_category_counts": dict(Counter(row["category"] for row in rows)),
        "sampled_documents": len(rows),
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Corpus Text Quality Audit",
        "",
        "Deterministic heuristic audit of normalized text quality. Categories are based on surface text features, not manual legal-quality annotation.",
        "",
        f"- sample size per source: `{payload['sample_size_per_source']}`",
        f"- randomization seed: `{payload['seed']}`",
        f"- sampled documents: `{payload['summary']['sampled_documents']}`",
        "",
        "## Category Counts by Source",
        "",
        "| Source | Available | Sampled | Readable | Artifacts | Empty/short |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for source in SOURCE_DIRS:
        summary = payload["summary"]["source_summaries"][source]
        counts = summary["category_counts"]
        lines.append(
            "| {source} | {available} | {sampled} | {readable} | {artifacts} | {short} |".format(
                source=source.upper(),
                available=summary["available_documents"],
                sampled=summary["sampled_documents"],
                readable=counts.get("readable", 0),
                artifacts=counts.get("artifacts", 0),
                short=counts.get("empty_or_short", 0),
            )
        )

    lines.extend(
        [
            "",
            "## Selected Feature Summary",
            "",
            "| Source | Median chars | Median tokens | Mean short-line ratio | Mean duplicate-line ratio | Mean hyphen breaks / 10k chars |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for source in SOURCE_DIRS:
        summary = payload["summary"]["source_summaries"][source]
        lines.append(
            "| {source} | {chars:.1f} | {tokens:.1f} | {short:.4f} | {dup:.4f} | {hyphen:.4f} |".format(
                source=source.upper(),
                chars=summary["median_chars"],
                tokens=summary["median_tokens"],
                short=summary["mean_short_line_ratio"],
                dup=summary["mean_duplicate_line_ratio"],
                hyphen=summary["mean_hyphen_breaks_per_10k"],
            )
        )

    lines.extend(["", "## Example Flagged Documents", ""])
    for category in ("empty_or_short", "artifacts"):
        examples = [
            row
            for row in payload["sampled_documents"]
            if row["category"] == category
        ][:5]
        if not examples:
            continue
        lines.append(f"### {category}")
        lines.append("")
        for row in examples:
            lines.append(
                f"- `{row['source']}` `{row['document_id']}`: {', '.join(row['reasons'])}"
            )
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit normalized corpus text quality.")
    parser.add_argument("--corpus-root", default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--sample-size", type=int, default=400)
    parser.add_argument("--seed", default="thesis_text_quality_v1")
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root)
    rows = []
    available_by_source = {}
    for source in SOURCE_DIRS:
        source_dir = corpus_root / source
        paths = sorted(path for path in source_dir.glob("*.txt") if path.is_file())
        available_by_source[source] = len(paths)
        for path in _sample_paths(paths, min(args.sample_size, len(paths)), args.seed):
            rows.append(_audit_document(path, source))

    payload = {
        "corpus_root": str(corpus_root),
        "sample_size_per_source": args.sample_size,
        "seed": args.seed,
        "summary": _summary(rows, available_by_source),
        "sampled_documents": rows,
    }

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(Path(args.output_md), payload)

    print("--- CORPUS TEXT QUALITY AUDIT COMPLETE ---")
    print(f"Sampled documents: {len(rows)}")
    print(f"JSON output:       {args.output_json}")
    print(f"Markdown output:   {args.output_md}")


if __name__ == "__main__":
    main()
