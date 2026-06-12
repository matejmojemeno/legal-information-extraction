#!/usr/bin/env python3
"""
Run law-resolver ablations on the joint 25-document gold set.

The ablations keep candidate detection and detail parsing fixed, then remove
selected lexical resources from the resolver. This isolates how much the main
runtime alias layer contributes to Czech-law resolution.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import extract_citation_occurrences
from src.document_metadata import load_document_dates_index, load_law_timelines
from src.production_paths import (
    PRODUCTION_AMBIGUOUS_ALIASES_PATH,
    PRODUCTION_AUDITED_ALIASES_PATH,
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_GLOBAL_ALIASES_PATH,
    PRODUCTION_SEED_ALIASES_PATH,
)


GOLD_PATH = Path("data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl")
OUTPUT_DIR = Path("data/annotations/joint_reference_gold_v1/law_resolver_ablations")
NULL_PATH = "__missing_ablation_resource__.json"


def _load_law_eval_module():
    module_path = Path("scripts/11_evaluate_against_gold.py")
    spec = importlib.util.spec_from_file_location("law_eval", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load evaluator module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAW_EVAL = _load_law_eval_module()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _payload_with_exact_matching(payload: Any) -> Any:
    if isinstance(payload, str):
        return {"law_id": payload, "match_mode": "exact"}
    if isinstance(payload, list):
        return {"law_ids": payload, "match_mode": "exact"}
    if isinstance(payload, dict):
        clone = dict(payload)
        clone["match_mode"] = "exact"
        return clone
    return payload


def _force_exact_alias_matching(alias_map: dict[str, Any]) -> dict[str, Any]:
    return {alias: _payload_with_exact_matching(payload) for alias, payload in alias_map.items()}


def _alias_map_for_variant(variant: str) -> tuple[dict[str, Any], str]:
    if variant == "full_spp":
        return load_runtime_aliases(
            audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
            global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
            seeded_path=PRODUCTION_SEED_ALIASES_PATH,
            ambiguous_path=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
            canonical_laws_path=PRODUCTION_CANONICAL_LAWS_PATH,
        )
    if variant == "no_global_aliases":
        return {}, "empty_global_aliases"
    if variant == "no_seed_aliases":
        return load_runtime_aliases(
            audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
            global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
            seeded_path=NULL_PATH,
            ambiguous_path=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
            canonical_laws_path=PRODUCTION_CANONICAL_LAWS_PATH,
        )
    if variant == "no_canonical_title_harvesting":
        return load_runtime_aliases(
            audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
            global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
            seeded_path=PRODUCTION_SEED_ALIASES_PATH,
            ambiguous_path=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
            canonical_laws_path=NULL_PATH,
        )
    if variant == "no_inflection_aware_alias_matching":
        alias_map, source = load_runtime_aliases(
            audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
            global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
            seeded_path=PRODUCTION_SEED_ALIASES_PATH,
            ambiguous_path=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
            canonical_laws_path=PRODUCTION_CANONICAL_LAWS_PATH,
        )
        return _force_exact_alias_matching(alias_map), f"{source}+forced_exact"
    raise ValueError(f"Unknown variant: {variant}")


def _local_aliases_for_variant(text: str, variant: str) -> dict[str, Any]:
    local_aliases = extract_local_aliases(text)
    if variant == "no_inflection_aware_alias_matching":
        return _force_exact_alias_matching(local_aliases)
    return local_aliases


def _prediction_row(document: dict[str, Any], occurrence: Any) -> dict[str, Any]:
    return {
        "document_id": str(document["document_id"]),
        "source": str(document.get("source") or ""),
        "document_path": str(document["document_path"]),
        "citation_text": occurrence.citation_text,
        "citation_type": occurrence.citation_type,
        "raw_start": occurrence.raw_start,
        "raw_end": occurrence.raw_end,
        "resolved_law_id": occurrence.resolved_law_id,
        "predicted_classification": occurrence.predicted_classification,
        "resolver_stage": occurrence.resolver_stage,
        "confidence": occurrence.confidence,
        "parsed_detail": occurrence.parsed_detail,
    }


def _generate_predictions(
    gold_docs: list[dict[str, Any]],
    variant: str,
    global_aliases: dict[str, Any],
    document_dates_index: dict[Any, Any],
    law_timelines: dict[str, dict],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document in gold_docs:
        document_path = Path(str(document["document_path"]))
        text = document_path.read_text(encoding="utf-8")
        source = str(document.get("source") or "")
        document_id = str(document["document_id"])
        occurrences = extract_citation_occurrences(
            text,
            _local_aliases_for_variant(text, variant),
            global_aliases,
            document_metadata=document_dates_index.get((source, document_id)),
            law_timelines=law_timelines,
        )
        rows.extend(_prediction_row(document, occurrence) for occurrence in occurrences)
    rows.sort(key=lambda item: (item["document_id"], int(item["raw_start"]), str(item["citation_type"])))
    return rows


def _prediction_key(document_id: str, row: dict[str, Any]) -> tuple[str, int, str]:
    return (document_id, int(row["raw_start"]), str(row["citation_type"]))


def _resolution_error_counts(
    gold_docs: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, int]:
    predictions_by_key = {
        _prediction_key(str(row["document_id"]), row): row
        for row in predictions
    }
    unresolved = 0
    wrong_law = 0
    correct = 0
    total = 0

    for document in gold_docs:
        document_id = str(document["document_id"])
        for citation in LAW_EVAL._dedupe_gold_citations(document.get("citations", [])):
            if citation.get("classification") != "czech_resolved":
                continue
            total += 1
            key = LAW_EVAL._key_for_gold({**citation, "document_id": document_id})
            prediction = predictions_by_key.get(key)
            if prediction is None:
                unresolved += 1
                continue
            resolved_law_id = prediction.get("resolved_law_id")
            if not resolved_law_id or prediction.get("predicted_classification") != "czech_resolved":
                unresolved += 1
            elif resolved_law_id == citation.get("law_id"):
                correct += 1
            else:
                wrong_law += 1

    return {
        "czech_resolved_gold_total": total,
        "correct_law_count": correct,
        "unresolved_count": unresolved,
        "wrong_law_count": wrong_law,
    }


def _evaluate_variant(
    gold_docs: list[dict[str, Any]],
    variant: str,
    alias_source: str,
    predictions: list[dict[str, Any]],
    document_dates_index: dict[Any, Any],
    law_timelines: dict[str, dict],
) -> dict[str, Any]:
    predictions_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in predictions:
        predictions_by_document.setdefault(str(row["document_id"]), []).append(row)

    report = LAW_EVAL.evaluate(
        gold_docs=gold_docs,
        global_aliases={},
        document_dates_index=document_dates_index,
        law_timelines=law_timelines,
        example_limit=5,
        predicted_occurrences_by_document=predictions_by_document,
    )
    report["alias_source"] = alias_source
    report["gold_input"] = str(GOLD_PATH)
    report["ablation_variant"] = variant
    return report


def _summary_row(
    variant: str,
    alias_source: str,
    report: dict[str, Any],
    error_counts: dict[str, int],
) -> dict[str, Any]:
    metrics = report["metrics"]
    czech_accuracy = metrics["czech_resolved_law_accuracy"]["accuracy"]
    classification_accuracy = metrics["matched_supported_classification_accuracy"]["accuracy"]
    anchor_f1 = metrics["supported_anchor_detection"]["f1"]
    citation_presence_f1 = metrics["supported_positive_citation_presence"]["f1"]
    return {
        "variant": variant,
        "alias_source": alias_source,
        "anchor_detection_f1": anchor_f1,
        "citation_presence_f1": citation_presence_f1,
        "law_classification_accuracy": classification_accuracy,
        "czech_law_resolution_accuracy": czech_accuracy,
        **error_counts,
    }


def _markdown_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Law Resolver Ablation Results",
        "",
        "| Variant | Czech-law accuracy | Unresolved | Wrong law | Classification accuracy | Anchor F1 | Citation F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['variant']}` | {row['czech_law_resolution_accuracy']:.4f} | "
            f"{row['unresolved_count']} | {row['wrong_law_count']} | "
            f"{row['law_classification_accuracy']:.4f} | {row['anchor_detection_f1']:.4f} | "
            f"{row['citation_presence_f1']:.4f} |"
        )
    lines.extend(
        [
            "",
            "All variants are scored on the same 25-document joint law-reference gold set.",
            "The unresolved and wrong-law columns are counted only over gold Czech-law references that should resolve to a concrete act.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    variants = [
        "full_spp",
        "no_global_aliases",
        "no_seed_aliases",
        "no_canonical_title_harvesting",
        "no_inflection_aware_alias_matching",
    ]
    gold_docs = LAW_EVAL._load_gold_documents(str(GOLD_PATH))
    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()

    summary_rows: list[dict[str, Any]] = []
    for variant in variants:
        print(f"Running variant: {variant}")
        global_aliases, alias_source = _alias_map_for_variant(variant)
        predictions = _generate_predictions(
            gold_docs=gold_docs,
            variant=variant,
            global_aliases=global_aliases,
            document_dates_index=document_dates_index,
            law_timelines=law_timelines,
        )
        prediction_path = OUTPUT_DIR / f"{variant}.predictions.jsonl"
        eval_json_path = OUTPUT_DIR / f"{variant}.eval.json"
        eval_md_path = OUTPUT_DIR / f"{variant}.eval.md"
        _write_jsonl(prediction_path, predictions)
        report = _evaluate_variant(
            gold_docs=gold_docs,
            variant=variant,
            alias_source=alias_source,
            predictions=predictions,
            document_dates_index=document_dates_index,
            law_timelines=law_timelines,
        )
        _write_json(eval_json_path, report)
        _write_text(eval_md_path, LAW_EVAL._markdown_report(report, alias_source, str(GOLD_PATH)))
        error_counts = _resolution_error_counts(gold_docs, predictions)
        summary_rows.append(_summary_row(variant, alias_source, report, error_counts))

    _write_json(OUTPUT_DIR / "law_resolver_ablation_summary.json", {"rows": summary_rows})
    _write_text(OUTPUT_DIR / "law_resolver_ablation_summary.md", _markdown_summary(summary_rows))
    print(_markdown_summary(summary_rows))


if __name__ == "__main__":
    main()
