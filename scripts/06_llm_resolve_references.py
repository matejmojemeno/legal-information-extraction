#!/usr/bin/env python3
"""
Step 6: LLM-assisted reference resolution for anomaly queue.

Design:
- deterministic candidate retrieval from local data (canonical laws + aliases)
- send only a small candidate shortlist to the LLM
- strict structured output with post-validation
- checkpointed JSONL output for resumable long runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional

# Ensure project root is importable when script is run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.citation_extractor import check_alias_in_context
from src.law_retriever import CanonicalLawRetriever, normalize_law_id, normalize_text

LAW_ID_PATTERN = re.compile(r"\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.")
_NOISY_LAW_NAME_PREFIXES = (
    "zákon, kterým se mění",
    "nález ústavního soudu",
    "sdělení ",
    "rozhodnutí prezidenta republiky",
    "vyhláška ",
    "nařízení ",
)


def _entry_id(target_reference: str, context_block: str) -> str:
    payload = f"{target_reference}\n{context_block}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _looks_like_law_id(value: str) -> bool:
    return bool(LAW_ID_PATTERN.fullmatch(value.strip()))


def _load_json(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_aliases(
    audited_path: str,
    global_path: str,
    seed_path: str,
) -> dict[str, str]:
    aliases: dict[str, str] = {}

    if os.path.exists(global_path):
        raw_global = _load_json(global_path)
        if isinstance(raw_global, dict):
            for alias, law_id in raw_global.items():
                if isinstance(alias, str) and isinstance(law_id, str):
                    aliases[alias] = normalize_law_id(law_id)

    if os.path.exists(audited_path):
        audited = _load_json(audited_path)
        if isinstance(audited, dict):
            for alias, details in audited.items():
                if not isinstance(details, dict):
                    continue
                if details.get("is_valid") is True and isinstance(
                    details.get("law_id"), str
                ):
                    aliases[alias] = normalize_law_id(details["law_id"])

    if os.path.exists(seed_path):
        seeded = _load_json(seed_path)
        if isinstance(seeded, dict):
            for alias, law_id in seeded.items():
                if isinstance(alias, str) and isinstance(law_id, str):
                    aliases[alias] = normalize_law_id(law_id)

    return aliases


def _alias_hint(alias: str) -> str | None:
    first = alias.split()[0] if alias.split() else alias
    first = normalize_text(first)
    first = re.sub(r"[^a-z0-9á-ž]+", "", first)
    if len(first) < 3:
        return None
    if first.isalpha() and len(first) > 4:
        return first[:-1]
    return first


def _build_alias_matchers(alias_map: dict[str, str]) -> list[tuple[str, str, str | None]]:
    out = []
    for alias, law_id in sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True):
        out.append((alias, law_id, _alias_hint(alias)))
    return out


def _collect_alias_candidates(
    context: str,
    alias_matchers: list[tuple[str, str, str | None]],
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    normalized_ctx = normalize_text(context)
    for alias, law_id, hint in alias_matchers:
        if hint and hint not in normalized_ctx:
            continue
        if check_alias_in_context(alias, context):
            if law_id not in seen:
                seen.add(law_id)
                candidates.append(law_id)
    return candidates


def _collect_direct_id_candidates(context: str) -> list[str]:
    found = []
    seen: set[str] = set()
    for raw in LAW_ID_PATTERN.findall(context):
        law_id = normalize_law_id(raw)
        if law_id not in seen:
            seen.add(law_id)
            found.append(law_id)
    return found


@dataclass(slots=True)
class CandidateBundle:
    law_ids: list[str]
    sources: dict[str, list[str]]


def _collect_candidates(
    entry: dict,
    retriever: CanonicalLawRetriever,
    alias_matchers: list[tuple[str, str, str | None]],
    top_k_lexical: int,
    max_candidates: int,
    min_lexical_score: float,
    max_lexical_additions: int,
) -> CandidateBundle:
    context = str(entry.get("context_block", ""))
    raw_candidates = entry.get("candidates", [])

    ordered: list[str] = []
    sources: dict[str, list[str]] = {}
    seen: set[str] = set()

    def add_candidate(law_id: str, source: str) -> None:
        law_id = normalize_law_id(law_id)
        if law_id not in retriever.primary_name_by_id:
            return
        if law_id not in seen:
            seen.add(law_id)
            ordered.append(law_id)
            sources[law_id] = [source]
        elif source not in sources[law_id]:
            sources[law_id].append(source)

    if isinstance(raw_candidates, list):
        for law_id in raw_candidates:
            if isinstance(law_id, str) and _looks_like_law_id(normalize_law_id(law_id)):
                add_candidate(law_id, "historical_candidates")

    for law_id in _collect_direct_id_candidates(context):
        add_candidate(law_id, "direct_id_in_context")

    for law_id in _collect_alias_candidates(context, alias_matchers):
        add_candidate(law_id, "alias_match")

    strong_count = len(ordered)
    lexical_added = 0
    for law_id, score in retriever.shortlist(context, top_k=top_k_lexical):
        if score < min_lexical_score:
            continue
        name_lower = retriever.get_name(law_id).lower()
        is_noisy = any(name_lower.startswith(prefix) for prefix in _NOISY_LAW_NAME_PREFIXES)
        if is_noisy and strong_count > 0:
            continue
        if lexical_added >= max_lexical_additions:
            break
        before = len(ordered)
        add_candidate(law_id, "lexical_retrieval")
        if len(ordered) > before:
            lexical_added += 1

    return CandidateBundle(
        law_ids=ordered[:max_candidates],
        sources={lid: sources[lid] for lid in ordered[:max_candidates]},
    )


def _load_existing_jsonl(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    existing: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_id = item.get("entry_id")
            if isinstance(entry_id, str):
                existing[entry_id] = item
    return existing


def _append_jsonl(path: str, row: dict) -> None:
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_prompt(
    target_reference: str,
    context_block: str,
    candidates: list[tuple[str, str]],
) -> str:
    candidate_lines = "\n".join(
        f"- {law_id}: {law_name}" for law_id, law_name in candidates
    )
    return f"""
You are a careful legal citation resolver for Czech legal texts.

Task:
Resolve the target reference to one Czech law ID only if evidence is strong.
If context points to a foreign law, classify as foreign_law.
If ambiguous or insufficient, classify as unresolved.

Critical constraints:
1) If classification is czech_resolved, resolved_law_id MUST be one of candidate IDs.
2) If classification is foreign_law or unresolved, resolved_law_id MUST be null.
3) Do not invent IDs outside candidate list.

Input:
Target reference: {target_reference}
Context block:
{context_block}

Candidate laws:
{candidate_lines if candidate_lines else "(no candidates provided)"}
""".strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve anomalies using local retrieval + Gemini."
    )
    parser.add_argument(
        "--input",
        default="data/processed/to_be_checked_by_llm.json",
        help="Input anomaly JSON.",
    )
    parser.add_argument(
        "--canonical",
        default="data/dicts/canonical_laws.json",
        help="Canonical laws JSON path.",
    )
    parser.add_argument(
        "--audited-aliases",
        default="data/dicts/audited_aliases.json",
        help="Audited aliases path.",
    )
    parser.add_argument(
        "--global-aliases",
        default="data/dicts/global_aliases.json",
        help="Global aliases path.",
    )
    parser.add_argument(
        "--seed-aliases",
        default="data/dicts/seed_aliases.json",
        help="Seed aliases path.",
    )
    parser.add_argument(
        "--output",
        default="data/processed/llm_reference_resolutions.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"),
        help="Gemini model name (default: GEMINI_MODEL or gemini-2.5-pro).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only first N entries.")
    parser.add_argument(
        "--top-k-lexical",
        type=int,
        default=10,
        help="Lexical retrieval candidates per entry.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=12,
        help="Max candidates sent to LLM per entry.",
    )
    parser.add_argument(
        "--min-lexical-score",
        type=float,
        default=0.8,
        help="Minimum lexical retrieval score to include candidate.",
    )
    parser.add_argument(
        "--max-lexical-additions",
        type=int,
        default=4,
        help="Maximum number of lexical candidates added per entry.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=0,
        help="Optional delay between API calls.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10000,
        help="Per-request timeout in milliseconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries after the initial LLM attempt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip API calls and output deterministic shortlist only.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file instead of resuming.",
    )
    args = parser.parse_args()

    anomalies = _load_json(args.input)
    if not isinstance(anomalies, list):
        raise SystemExit(f"Input file must be a JSON list: {args.input}")
    if args.limit:
        anomalies = anomalies[: args.limit]

    canonical_laws = _load_json(args.canonical)
    if not isinstance(canonical_laws, dict):
        raise SystemExit(f"Canonical laws must be object: {args.canonical}")
    retriever = CanonicalLawRetriever(canonical_laws)

    alias_map = _load_aliases(
        audited_path=args.audited_aliases,
        global_path=args.global_aliases,
        seed_path=args.seed_aliases,
    )
    alias_matchers = _build_alias_matchers(alias_map)

    if args.overwrite and os.path.exists(args.output):
        os.remove(args.output)

    processed = _load_existing_jsonl(args.output)

    use_llm = not args.dry_run
    client = None
    ResolutionDecision = None

    if use_llm:
        try:
            from google import genai
            from google.genai import types as genai_types
            from pydantic import BaseModel, Field
            from typing import Literal
        except ImportError:
            print("Missing dependencies. Install: pip install google-genai pydantic")
            sys.exit(1)

        if not os.environ.get("GEMINI_API_KEY"):
            print("GEMINI_API_KEY is not set. Use --dry-run or set API key.")
            sys.exit(1)

        class _ResolutionDecision(BaseModel):
            classification: Literal["czech_resolved", "foreign_law", "unresolved"] = Field(
                description="Final classification for this citation."
            )
            resolved_law_id: Optional[str] = Field(
                default=None,
                description="Chosen law ID from candidates when czech_resolved, else null.",
            )
            confidence: float = Field(
                ge=0.0,
                le=1.0,
                description="Confidence in final classification.",
            )
            rationale: str = Field(
                min_length=1,
                max_length=300,
                description="Short evidence-based explanation.",
            )

        ResolutionDecision = _ResolutionDecision
        client = genai.Client()

    total = len(anomalies)
    stats = Counter()
    started = time.time()

    print(f"Processing {total} anomaly entries...")
    print(f"Loaded canonical laws: {len(retriever.primary_name_by_id)}")
    print(f"Loaded alias mappings: {len(alias_map)}")
    print(f"Mode: {'dry-run' if args.dry_run else 'LLM'}")
    print(f"Model: {args.model}")

    for i, entry in enumerate(anomalies, start=1):
        target_reference = str(entry.get("target_reference", "")).strip()
        context_block = str(entry.get("context_block", "")).strip()
        entry_id = _entry_id(target_reference, context_block)

        if not target_reference or not context_block:
            continue
        if entry_id in processed:
            stats["skipped_existing"] += 1
            continue

        bundle = _collect_candidates(
            entry=entry,
            retriever=retriever,
            alias_matchers=alias_matchers,
            top_k_lexical=args.top_k_lexical,
            max_candidates=args.max_candidates,
            min_lexical_score=args.min_lexical_score,
            max_lexical_additions=args.max_lexical_additions,
        )
        candidate_laws = [(law_id, retriever.get_name(law_id)) for law_id in bundle.law_ids]

        if args.dry_run:
            result = {
                "classification": "unresolved",
                "resolved_law_id": None,
                "confidence": 0.0,
                "rationale": "dry_run_no_llm",
            }
        else:
            prompt = _build_prompt(target_reference, context_block, candidate_laws)
            response = None
            last_exc = None
            for attempt in range(args.retries + 1):
                try:
                    response = client.models.generate_content(
                        model=args.model,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": ResolutionDecision,
                            "temperature": 0.1,
                            "http_options": genai_types.HttpOptions(timeout=args.timeout_ms),
                        },
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt >= args.retries:
                        break
                    time.sleep(min(2.0, 0.5 * (attempt + 1)))

            if response is None:
                result = {
                    "classification": "unresolved",
                    "resolved_law_id": None,
                    "confidence": 0.0,
                    "rationale": f"api_error: {last_exc}",
                }
                stats["api_errors"] += 1
            else:
                parsed = getattr(response, "parsed", None)
                if parsed is None:
                    text_payload = str(getattr(response, "text", "") or "").strip()
                    recovered = None
                    if text_payload:
                        try:
                            recovered = json.loads(text_payload)
                        except Exception:
                            recovered = None

                    if isinstance(recovered, dict):
                        classification = str(recovered.get("classification") or "").strip()
                        if classification in {"czech_resolved", "foreign_law", "unresolved"}:
                            raw_confidence = recovered.get("confidence", 0.0)
                            try:
                                confidence = float(raw_confidence)
                            except Exception:
                                confidence = 0.0
                            confidence = max(0.0, min(1.0, confidence))
                            rationale = str(
                                recovered.get("rationale")
                                or "Recovered from unparsed structured response."
                            ).strip()[:300]
                            if not rationale:
                                rationale = "Recovered from unparsed structured response."
                            resolved_law_id = recovered.get("resolved_law_id")
                            result = {
                                "classification": classification,
                                "resolved_law_id": (
                                    str(resolved_law_id).strip() if resolved_law_id else None
                                ),
                                "confidence": confidence,
                                "rationale": rationale,
                            }
                        else:
                            result = {
                                "classification": "unresolved",
                                "resolved_law_id": None,
                                "confidence": 0.0,
                                "rationale": "unparsed_response_missing_valid_classification",
                            }
                    else:
                        result = {
                            "classification": "unresolved",
                            "resolved_law_id": None,
                            "confidence": 0.0,
                            "rationale": "unparsed_response_no_structured_payload",
                        }
                else:
                    result = {
                        "classification": parsed.classification,
                        "resolved_law_id": parsed.resolved_law_id,
                        "confidence": float(parsed.confidence),
                        "rationale": parsed.rationale.strip(),
                    }

        if result["classification"] == "czech_resolved":
            if result["resolved_law_id"] not in bundle.law_ids:
                result["classification"] = "unresolved"
                result["resolved_law_id"] = None
                result["confidence"] = min(float(result["confidence"]), 0.5)
                result["rationale"] = (
                    "Post-validation rejected ID outside shortlist. " + result["rationale"]
                )
                stats["post_validation_reject"] += 1
        else:
            result["resolved_law_id"] = None

        row = {
            "entry_id": entry_id,
            "target_reference": target_reference,
            "context_block": context_block,
            "candidate_laws": [
                {
                    "law_id": law_id,
                    "law_name": retriever.get_name(law_id),
                    "sources": bundle.sources.get(law_id, []),
                }
                for law_id in bundle.law_ids
            ],
            "result": result,
            "model": args.model if not args.dry_run else None,
            "timestamp_unix": int(time.time()),
        }

        _append_jsonl(args.output, row)

        stats["processed"] += 1
        stats[f"class_{result['classification']}"] += 1

        elapsed = time.time() - started
        print(
            f"[{i}/{total}] processed={stats['processed']} "
            f"resolved={stats['class_czech_resolved']} "
            f"foreign={stats['class_foreign_law']} "
            f"unresolved={stats['class_unresolved']} "
            f"elapsed={elapsed:.1f}s"
        )

        if args.sleep_ms > 0 and not args.dry_run:
            time.sleep(args.sleep_ms / 1000.0)

    print("\n--- DONE ---")
    print(f"Processed now:      {stats['processed']}")
    print(f"Skipped existing:   {stats['skipped_existing']}")
    print(f"Czech resolved:     {stats['class_czech_resolved']}")
    print(f"Foreign law:        {stats['class_foreign_law']}")
    print(f"Unresolved:         {stats['class_unresolved']}")
    print(f"API errors:         {stats['api_errors']}")
    print(f"Post-val rejects:   {stats['post_validation_reject']}")
    print(f"Output:             {args.output}")


if __name__ == "__main__":
    main()
