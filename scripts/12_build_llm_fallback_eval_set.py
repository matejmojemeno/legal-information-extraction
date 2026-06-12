#!/usr/bin/env python3
"""Build an evaluation set for the future LLM fallback stage.

This script does not run an LLM. It uses the reviewed gold set plus the current
deterministic extractor to isolate cases where an LLM fallback could be useful.

It separates:
- resolver-like failures (good LLM candidates)
- detector/parser failures (usually not an LLM fallback problem)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import extract_citation_occurrences
from src.document_metadata import load_document_dates_index, load_law_timelines

AUDITED_ALIASES_PATH = "data/dicts/audited_aliases.json"
GLOBAL_ALIASES_PATH = "data/dicts/global_aliases.json"
SEEDED_ALIASES_PATH = "data/dicts/seed_aliases.json"
DEFAULT_GOLD_INPUT = "data/annotations/gold/gold_annotations_v1.jsonl"
DEFAULT_JSONL_OUTPUT = "data/annotations/eval/llm_fallback_eval_set_v1.jsonl"
DEFAULT_SUMMARY_OUTPUT = "data/annotations/eval/llm_fallback_eval_set_v1.summary.json"
CONTEXT_RADIUS = 500
LOW_CONFIDENCE_THRESHOLD = 0.80


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _load_gold_documents(path: str) -> list[dict[str, Any]]:
    if os.path.isdir(path):
        docs = []
        for name in sorted(os.listdir(path)):
            if not name.endswith(".json"):
                continue
            payload = _load_json(os.path.join(path, name))
            if isinstance(payload, dict) and isinstance(payload.get("citations"), list):
                docs.append(payload)
        return docs

    if path.endswith(".jsonl"):
        return _load_jsonl(path)

    payload = _load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("citations"), list):
        return [payload]
    raise ValueError(f"Unsupported gold input format: {path}")


def _normalize_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def _normalized_gold_anchor_start(citation: dict[str, Any]) -> int:
    start = int(citation["start_char"])
    citation_text = str(citation.get("citation_text") or "")
    citation_type = str(citation.get("citation_type") or "")
    detail_number = str(citation.get("detail_number") or "").strip()

    if citation_type == "section" and detail_number:
        match = re.search(rf"§{{1,2}}\s*{re.escape(detail_number)}\b", citation_text, flags=re.IGNORECASE)
        if match:
            return start + match.start()
    elif citation_type == "article" and detail_number:
        match = re.search(rf"(?:čl\.|článek)\s*{re.escape(detail_number)}\b", citation_text, flags=re.IGNORECASE)
        if match:
            return start + match.start()
    elif citation_type == "other_normative":
        law_id = str(citation.get("law_id") or "").strip()
        if law_id:
            match = re.search(re.escape(law_id), citation_text, flags=re.IGNORECASE)
            if match:
                return start + match.start()

    fallback = re.search(r"(?:§{1,2}|čl\.|článek|\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.)", citation_text, flags=re.IGNORECASE)
    if fallback:
        return start + fallback.start()
    return start


def _key_for_gold(document_id: str, citation: dict[str, Any]) -> tuple[str, int, str]:
    return (
        document_id,
        int(_normalized_gold_anchor_start(citation)),
        str(citation.get("citation_type", "")),
    )


def _key_for_prediction(document_id: str, pred: dict[str, Any]) -> tuple[str, int, str]:
    return (document_id, int(pred["raw_start"]), str(pred["citation_type"]))


def _prediction_label(pred: dict[str, Any]) -> str:
    explicit = pred.get("predicted_classification")
    if explicit:
        return str(explicit)
    return "czech_resolved" if pred.get("resolved_law_id") else "czech_unresolved"


def _build_context(raw_text: str, start_char: int, end_char: int) -> str:
    ctx_start = max(0, start_char - CONTEXT_RADIUS)
    ctx_end = min(len(raw_text), end_char + CONTEXT_RADIUS)
    return raw_text[ctx_start:ctx_end].replace("\n", " ")


def _build_eval_entry(
    *,
    document_id: str,
    source: str,
    document_path: str,
    route_reason: str,
    llm_suitability: str,
    target_reference: str,
    context_block: str,
    gold: dict[str, Any] | None,
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    row = {
        "document_id": document_id,
        "source": source,
        "document_path": document_path,
        "route_reason": route_reason,
        "llm_suitability": llm_suitability,
        "target_reference": target_reference,
        "context_block": context_block,
    }
    if gold is not None:
        row["gold"] = {
            "citation_text": gold.get("citation_text"),
            "citation_type": gold.get("citation_type"),
            "classification": gold.get("classification"),
            "law_id": gold.get("law_id"),
            "detail_number": gold.get("detail_number"),
            "detail_odst": gold.get("detail_odst"),
            "detail_pism": gold.get("detail_pism"),
            "start_char": gold.get("start_char"),
            "end_char": gold.get("end_char"),
        }
    if prediction is not None:
        row["deterministic_prediction"] = {
            "citation_text": prediction.get("citation_text"),
            "citation_type": prediction.get("citation_type"),
            "predicted_classification": _prediction_label(prediction),
            "resolved_law_id": prediction.get("resolved_law_id"),
            "resolver_stage": prediction.get("resolver_stage"),
            "confidence": prediction.get("confidence"),
            "candidate_law_ids": prediction.get("candidate_law_ids", []),
            "parsed_detail": prediction.get("parsed_detail"),
            "raw_start": prediction.get("raw_start"),
            "raw_end": prediction.get("raw_end"),
        }
    return row


def build_eval_set(
    gold_docs: list[dict[str, Any]],
    global_aliases: dict[str, object],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)

    for gold_doc in sorted(gold_docs, key=lambda x: str(x.get("document_id", ""))):
        document_id = str(gold_doc["document_id"])
        document_path = str(gold_doc["document_path"])
        source = str(gold_doc.get("source", ""))
        citations = gold_doc.get("citations", [])
        if not isinstance(citations, list):
            continue

        with open(document_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        local_aliases = extract_local_aliases(raw_text)
        occurrences = extract_citation_occurrences(
            raw_text,
            local_aliases,
            global_aliases,
            document_metadata=document_dates_index.get((source, document_id)),
            law_timelines=law_timelines or None,
        )

        predictions = []
        for occ in occurrences:
            predictions.append(
                {
                    "citation_text": occ.citation_text,
                    "citation_type": occ.citation_type,
                    "raw_start": occ.raw_start,
                    "raw_end": occ.raw_end,
                    "resolved_law_id": occ.resolved_law_id,
                    "predicted_classification": occ.predicted_classification,
                    "resolver_stage": occ.resolver_stage,
                    "confidence": occ.confidence,
                    "parsed_detail": occ.parsed_detail,
                    "candidate_law_ids": occ.candidate_law_ids,
                }
            )

        pred_by_key: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        for pred in predictions:
            pred_by_key[_key_for_prediction(document_id, pred)].append(pred)

        used_pred_ids: set[int] = set()

        for citation in citations:
            classification = str(citation.get("classification", ""))
            if classification == "non_citation":
                continue

            key = _key_for_gold(document_id, citation)
            candidates = pred_by_key.get(key, [])
            pred = None
            if candidates:
                pred = candidates.pop(0)
                used_pred_ids.add(id(pred))

            gold_start = int(citation.get("start_char") or _normalized_gold_anchor_start(citation))
            gold_end = int(citation.get("end_char") or gold_start + len(str(citation.get("citation_text") or "")))
            target_reference = str(citation.get("citation_text") or "")
            context_block = _build_context(raw_text, gold_start, gold_end)

            if pred is None:
                route_reason = "missing_anchor"
                llm_suitability = "detector_problem"
                rows.append(
                    _build_eval_entry(
                        document_id=document_id,
                        source=source,
                        document_path=document_path,
                        route_reason=route_reason,
                        llm_suitability=llm_suitability,
                        target_reference=target_reference,
                        context_block=context_block,
                        gold=citation,
                        prediction=None,
                    )
                )
                counts[route_reason] += 1
                continue

            pred_label = _prediction_label(pred)
            gold_law_id = str(citation.get("law_id") or "").strip() or None
            pred_law_id = str(pred.get("resolved_law_id") or "").strip() or None
            gold_detail = (
                str(citation.get("detail_number") or "").strip(),
                tuple(_normalize_list(citation.get("detail_odst"))),
                tuple(_normalize_list(citation.get("detail_pism"))),
            )
            pred_detail_raw = pred.get("parsed_detail") or {}
            pred_detail = (
                str(pred_detail_raw.get("number") or "").strip(),
                tuple(_normalize_list(pred_detail_raw.get("odst"))),
                tuple(_normalize_list(pred_detail_raw.get("pism"))),
            )

            route_reason = None
            llm_suitability = None

            if classification != pred_label:
                route_reason = "wrong_classification"
                llm_suitability = (
                    "good_llm_candidate" if pred_label != "non_citation" else "mixed"
                )
            elif classification == "czech_resolved" and gold_law_id != pred_law_id:
                route_reason = "wrong_law_id"
                llm_suitability = "good_llm_candidate"
            elif gold_detail != pred_detail:
                route_reason = "wrong_detail"
                llm_suitability = "maybe_llm_candidate"
            elif float(pred.get("confidence") or 0.0) < LOW_CONFIDENCE_THRESHOLD:
                route_reason = "low_confidence"
                llm_suitability = "good_llm_candidate"

            if route_reason is not None:
                rows.append(
                    _build_eval_entry(
                        document_id=document_id,
                        source=source,
                        document_path=document_path,
                        route_reason=route_reason,
                        llm_suitability=llm_suitability or "good_llm_candidate",
                        target_reference=target_reference,
                        context_block=context_block,
                        gold=citation,
                        prediction=pred,
                    )
                )
                counts[route_reason] += 1

        for pred in predictions:
            if id(pred) in used_pred_ids:
                continue
            if _prediction_label(pred) == "non_citation":
                continue
            rows.append(
                _build_eval_entry(
                    document_id=document_id,
                    source=source,
                    document_path=document_path,
                    route_reason="spurious_prediction",
                    llm_suitability="not_llm_first_choice",
                    target_reference=str(pred.get("citation_text") or ""),
                    context_block=_build_context(raw_text, int(pred["raw_start"]), int(pred["raw_end"])),
                    gold=None,
                    prediction=pred,
                )
            )
            counts["spurious_prediction"] += 1

    summary = {
        "total_entries": len(rows),
        "route_reason_counts": dict(sorted(counts.items())),
        "recommended_llm_route_reasons": [
            "wrong_law_id",
            "wrong_classification",
            "low_confidence",
        ],
        "usually_not_llm_first_choice": [
            "missing_anchor",
            "spurious_prediction",
        ],
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an LLM fallback evaluation set from gold data.")
    parser.add_argument("--gold-input", default=DEFAULT_GOLD_INPUT, help="Gold JSONL/JSON/directory.")
    parser.add_argument("--audited-aliases", default=AUDITED_ALIASES_PATH)
    parser.add_argument("--global-aliases", default=GLOBAL_ALIASES_PATH)
    parser.add_argument("--seed-aliases", default=SEEDED_ALIASES_PATH)
    parser.add_argument("--output-jsonl", default=DEFAULT_JSONL_OUTPUT)
    parser.add_argument("--output-summary", default=DEFAULT_SUMMARY_OUTPUT)
    args = parser.parse_args()

    gold_docs = _load_gold_documents(args.gold_input)
    if not gold_docs:
        raise SystemExit(f"No gold documents found in {args.gold_input}")

    global_aliases, _ = load_runtime_aliases(
        audited_path=args.audited_aliases,
        global_path=args.global_aliases,
        seeded_path=args.seed_aliases,
    )

    rows, summary = build_eval_set(gold_docs, global_aliases)

    os.makedirs(os.path.dirname(args.output_jsonl), exist_ok=True)
    with open(args.output_jsonl, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(args.output_summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Built LLM fallback eval set: {len(rows)}")
    print(f"JSONL:   {args.output_jsonl}")
    print(f"Summary: {args.output_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
