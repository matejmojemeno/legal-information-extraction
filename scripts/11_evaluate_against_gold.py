#!/usr/bin/env python3
"""
Step 11: Evaluate deterministic extraction against gold annotations.

This evaluator is intentionally aligned with the current pipeline:
- predictions come from `extract_citation_occurrences`
- global aliases are loaded the same way as Step 3
- metrics distinguish between currently supported anchor types
  (`section`, `article`) and unsupported `other_normative` gold items

Outputs:
- JSON report with machine-readable metrics and taxonomy counts
- Markdown report for quick inspection
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
DEFAULT_JSON_OUTPUT = "data/annotations/eval/evaluation_report_v1.json"
DEFAULT_MD_OUTPUT = "data/annotations/eval/evaluation_report_v1.md"

POSITIVE_CLASSIFICATIONS = {"czech_resolved", "czech_unresolved", "foreign_law"}
SUPPORTED_TYPES = {"section", "article", "other_normative"}


def _safe_div(num: int, den: int) -> float | None:
    if den == 0:
        return None
    return num / den


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def _normalize_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def _prediction_label(pred: dict[str, Any]) -> str:
    explicit = pred.get("predicted_classification")
    if explicit:
        return str(explicit)
    return "czech_resolved" if pred.get("resolved_law_id") else "czech_unresolved"


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


def _write_json(path: str, payload: dict[str, Any]) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_text(path: str, text: str) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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


def _key_for_gold(citation: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(citation["document_id"]),
        int(_normalized_gold_anchor_start(citation)),
        str(citation["citation_type"]),
    )


def _key_for_prediction(document_id: str, prediction: dict[str, Any]) -> tuple[str, int, str]:
    return (
        document_id,
        int(prediction["raw_start"]),
        str(prediction["citation_type"]),
    )


def _example_payload(
    document_id: str,
    gold: dict[str, Any] | None = None,
    pred: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"document_id": document_id}
    if gold is not None:
        payload["gold"] = {
            "citation_text": gold.get("citation_text"),
            "citation_type": gold.get("citation_type"),
            "start_char": gold.get("start_char"),
            "classification": gold.get("classification"),
            "law_id": gold.get("law_id"),
            "detail_number": gold.get("detail_number"),
            "detail_odst": gold.get("detail_odst"),
            "detail_pism": gold.get("detail_pism"),
            "task_id": gold.get("task_id"),
        }
        if pred is not None:
            payload["prediction"] = {
                "citation_text": pred.get("citation_text"),
                "citation_type": pred.get("citation_type"),
                "raw_start": pred.get("raw_start"),
                "label": _prediction_label(pred),
                "predicted_classification": pred.get("predicted_classification"),
                "resolved_law_id": pred.get("resolved_law_id"),
                "resolver_stage": pred.get("resolver_stage"),
                "confidence": pred.get("confidence"),
            "parsed_detail": pred.get("parsed_detail"),
        }
    return payload


def _normalized_gold_anchor_start(citation: dict[str, Any]) -> int:
    start = int(citation["start_char"])
    citation_text = str(citation.get("citation_text") or "")
    citation_type = str(citation.get("citation_type") or "")
    detail_number = str(citation.get("detail_number") or "").strip()

    if citation_type == "section" and detail_number:
        match = re.search(rf"§{{1,2}}\s*{re.escape(detail_number)}\b", citation_text, flags=re.IGNORECASE)
        if match:
            return start + match.start()
        return start
    elif citation_type == "article" and detail_number:
        match = re.search(rf"(?:čl\.|článek)\s*{re.escape(detail_number)}\b", citation_text, flags=re.IGNORECASE)
        if match:
            return start + match.start()
        return start
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


def _dedupe_gold_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for citation in citations:
        key = (
            _normalized_gold_anchor_start(citation),
            str(citation.get("citation_type") or ""),
            str(citation.get("classification") or ""),
            str(citation.get("law_id") or ""),
            str(citation.get("detail_number") or ""),
            tuple(_normalize_list(citation.get("detail_odst"))),
            tuple(_normalize_list(citation.get("detail_pism"))),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _append_example(
    examples: dict[str, list[dict[str, Any]]],
    key: str,
    payload: dict[str, Any],
    limit: int,
) -> None:
    bucket = examples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(payload)


def evaluate(
    gold_docs: list[dict[str, Any]],
    global_aliases: dict[str, str],
    document_dates_index,
    law_timelines,
    example_limit: int,
    predicted_occurrences_by_document: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()

    gold_type_counts: Counter[str] = Counter()
    gold_class_counts: Counter[str] = Counter()
    predicted_stage_counts: Counter[str] = Counter()
    classification_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    taxonomy_counts: Counter[str] = Counter()
    taxonomy_examples: dict[str, list[dict[str, Any]]] = {}

    total_gold = 0
    total_supported_gold = 0
    total_supported_positive_gold = 0
    total_supported_non_citation_gold = 0
    total_unsupported_gold = 0

    total_predictions = 0
    total_matched_supported = 0
    total_spurious_predictions = 0
    total_duplicate_anchor_predictions = 0
    total_missing_supported = 0

    total_positive_tp = 0
    total_positive_fp = 0
    total_positive_fn = 0

    law_eval_total = 0
    law_eval_correct = 0

    detail_eval_total = 0
    detail_number_correct = 0
    detail_odst_correct = 0
    detail_pism_correct = 0
    detail_full_correct = 0

    per_doc_rows: list[dict[str, Any]] = []

    for gold_doc in sorted(gold_docs, key=lambda x: str(x.get("document_id", ""))):
        document_id = str(gold_doc["document_id"])
        document_path = str(gold_doc["document_path"])
        source = str(gold_doc.get("source", ""))
        citations = gold_doc.get("citations", [])
        if not isinstance(citations, list):
            continue
        citations = _dedupe_gold_citations(citations)

        with open(document_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        predictions = []
        if predicted_occurrences_by_document is None:
            local_aliases = extract_local_aliases(raw_text)
            for occ in extract_citation_occurrences(
                raw_text,
                local_aliases,
                global_aliases,
                document_metadata=document_dates_index.get((source, document_id)),
                law_timelines=law_timelines or None,
            ):
                pred = {
                    "citation_text": occ.citation_text,
                    "citation_type": occ.citation_type,
                    "raw_start": occ.raw_start,
                    "raw_end": occ.raw_end,
                    "resolved_law_id": occ.resolved_law_id,
                    "predicted_classification": occ.predicted_classification,
                    "resolver_stage": occ.resolver_stage,
                    "confidence": occ.confidence,
                    "parsed_detail": occ.parsed_detail,
                }
                predictions.append(pred)
                predicted_stage_counts[pred["resolver_stage"]] += 1
        else:
            for pred in predicted_occurrences_by_document.get(document_id, []):
                pred = dict(pred)
                predictions.append(pred)
                predicted_stage_counts[str(pred.get("resolver_stage") or "external_predictions")] += 1

        total_predictions += len(predictions)

        pred_by_key: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        doc_positive_prediction_keys: set[tuple[str, int, str]] = set()
        for pred in predictions:
            key = _key_for_prediction(document_id, pred)
            pred_by_key[key].append(pred)
            if _prediction_label(pred) != "non_citation":
                doc_positive_prediction_keys.add(key)

        doc_supported = 0
        doc_supported_positive = 0
        doc_supported_matched = 0
        doc_predictions = len(predictions)

        doc_positive_gold_keys: set[tuple[str, int, str]] = set()
        doc_gold_keys: set[tuple[str, int, str]] = set()
        doc_prediction_keys = set(pred_by_key.keys())

        for citation in citations:
            total_gold += 1
            citation = dict(citation)
            citation["document_id"] = document_id
            citation["document_path"] = document_path
            citation["source"] = source

            citation_type = str(citation.get("citation_type", ""))
            classification = str(citation.get("classification", ""))
            gold_type_counts[citation_type] += 1
            gold_class_counts[classification] += 1

            total_supported_gold += 1
            doc_supported += 1

            if classification == "non_citation":
                total_supported_non_citation_gold += 1
            else:
                total_supported_positive_gold += 1
                doc_supported_positive += 1
                doc_positive_gold_keys.add(_key_for_gold(citation))

            key = _key_for_gold(citation)
            doc_gold_keys.add(key)
            candidates = pred_by_key.get(key, [])
            pred = candidates.pop(0) if candidates else None

            if pred is None:
                total_missing_supported += 1
                taxonomy_counts["missing_supported_anchor"] += 1
                _append_example(
                    taxonomy_examples,
                    "missing_supported_anchor",
                    _example_payload(document_id, gold=citation),
                    example_limit,
                )
                continue

            total_matched_supported += 1
            doc_supported_matched += 1

            pred_label = _prediction_label(pred)
            classification_confusion[classification][pred_label] += 1

            if classification == "non_citation" and pred_label != "non_citation":
                taxonomy_counts["gold_non_citation_predicted_as_citation"] += 1
                _append_example(
                    taxonomy_examples,
                    "gold_non_citation_predicted_as_citation",
                    _example_payload(document_id, gold=citation, pred=pred),
                    example_limit,
                )
            elif classification == "foreign_law" and pred_label != "foreign_law":
                taxonomy_counts["foreign_law_not_typed_explicitly"] += 1
                _append_example(
                    taxonomy_examples,
                    "foreign_law_not_typed_explicitly",
                    _example_payload(document_id, gold=citation, pred=pred),
                    example_limit,
                )
            elif classification == "czech_unresolved" and pred_label != "czech_unresolved":
                taxonomy_counts["gold_unresolved_but_predicted_resolved"] += 1
                _append_example(
                    taxonomy_examples,
                    "gold_unresolved_but_predicted_resolved",
                    _example_payload(document_id, gold=citation, pred=pred),
                    example_limit,
                )

            if classification != pred_label:
                taxonomy_counts["wrong_classification"] += 1
                _append_example(
                    taxonomy_examples,
                    "wrong_classification",
                    _example_payload(document_id, gold=citation, pred=pred),
                    example_limit,
                )

            if classification == "czech_resolved":
                law_eval_total += 1
                if pred.get("resolved_law_id") == citation.get("law_id"):
                    law_eval_correct += 1
                else:
                    taxonomy_counts["wrong_law_id"] += 1
                    _append_example(
                        taxonomy_examples,
                        "wrong_law_id",
                        _example_payload(document_id, gold=citation, pred=pred),
                        example_limit,
                    )

            if classification != "non_citation":
                detail_eval_total += 1
                pred_detail = pred.get("parsed_detail") or {}
                gold_number = str(citation.get("detail_number") or "").strip() or None
                pred_number = str(pred_detail.get("number") or "").strip() or None
                gold_odst = _normalize_list(citation.get("detail_odst"))
                pred_odst = _normalize_list(pred_detail.get("odst"))
                gold_pism = _normalize_list(citation.get("detail_pism"))
                pred_pism = _normalize_list(pred_detail.get("pism"))

                number_ok = gold_number == pred_number
                odst_ok = gold_odst == pred_odst
                pism_ok = gold_pism == pred_pism

                if number_ok:
                    detail_number_correct += 1
                else:
                    taxonomy_counts["wrong_detail_number"] += 1
                    _append_example(
                        taxonomy_examples,
                        "wrong_detail_number",
                        _example_payload(document_id, gold=citation, pred=pred),
                        example_limit,
                    )

                if odst_ok:
                    detail_odst_correct += 1
                else:
                    taxonomy_counts["wrong_detail_odst"] += 1
                    _append_example(
                        taxonomy_examples,
                        "wrong_detail_odst",
                        _example_payload(document_id, gold=citation, pred=pred),
                        example_limit,
                    )

                if pism_ok:
                    detail_pism_correct += 1
                else:
                    taxonomy_counts["wrong_detail_pism"] += 1
                    _append_example(
                        taxonomy_examples,
                        "wrong_detail_pism",
                        _example_payload(document_id, gold=citation, pred=pred),
                        example_limit,
                    )

                if number_ok and odst_ok and pism_ok:
                    detail_full_correct += 1

        unmatched_preds = 0
        duplicate_preds = 0
        for key, rows in pred_by_key.items():
            if not rows:
                continue
            if key in doc_gold_keys:
                duplicate_preds += len(rows)
                continue
            unmatched_preds += len(rows)

        total_spurious_predictions += unmatched_preds
        total_duplicate_anchor_predictions += duplicate_preds

        if unmatched_preds:
            taxonomy_counts["spurious_predicted_anchor"] += unmatched_preds
            for key, rows in pred_by_key.items():
                if key in doc_gold_keys:
                    continue
                for pred in rows:
                    _append_example(
                        taxonomy_examples,
                        "spurious_predicted_anchor",
                        _example_payload(document_id, pred=pred),
                        example_limit,
                    )
        if duplicate_preds:
            taxonomy_counts["duplicate_prediction_same_anchor"] += duplicate_preds
            for key, rows in pred_by_key.items():
                if key not in doc_gold_keys:
                    continue
                for pred in rows:
                    _append_example(
                        taxonomy_examples,
                        "duplicate_prediction_same_anchor",
                        _example_payload(document_id, pred=pred),
                        example_limit,
                    )

        doc_positive_tp = len(doc_positive_prediction_keys & doc_positive_gold_keys)
        doc_positive_fp = len(doc_positive_prediction_keys - doc_positive_gold_keys)
        doc_positive_fn = len(doc_positive_gold_keys - doc_positive_prediction_keys)
        total_positive_tp += doc_positive_tp
        total_positive_fp += doc_positive_fp
        total_positive_fn += doc_positive_fn

        per_doc_rows.append(
            {
                "document_id": document_id,
                "source": source,
                "document_path": document_path,
                "gold_total": len(citations),
                "gold_supported": doc_supported,
                "gold_supported_positive": doc_supported_positive,
                "predicted_occurrences": doc_predictions,
                "matched_supported": doc_supported_matched,
                "missing_supported": doc_supported - doc_supported_matched,
                "spurious_predictions": unmatched_preds,
                "duplicate_anchor_predictions": duplicate_preds,
            }
        )

    anchor_precision = _safe_div(total_matched_supported, total_predictions)
    anchor_recall = _safe_div(total_matched_supported, total_supported_gold)
    anchor_f1 = _f1(anchor_precision, anchor_recall)

    positive_precision = _safe_div(total_positive_tp, total_positive_tp + total_positive_fp)
    positive_recall = _safe_div(total_positive_tp, total_positive_tp + total_positive_fn)
    positive_f1 = _f1(positive_precision, positive_recall)

    matched_supported_for_classification = total_matched_supported
    exact_classification_matches = 0
    for gold_label, pred_counts in classification_confusion.items():
        exact_classification_matches += pred_counts.get(gold_label, 0)
    classification_accuracy = _safe_div(
        exact_classification_matches,
        matched_supported_for_classification,
    )

    report = {
        "generated_at_utc": generated_at,
        "gold_documents": len(gold_docs),
        "gold_stats": {
            "total_findings": total_gold,
            "by_type": dict(sorted(gold_type_counts.items())),
            "by_classification": dict(sorted(gold_class_counts.items())),
            "supported_types": sorted(SUPPORTED_TYPES),
            "supported_findings": total_supported_gold,
            "supported_positive_findings": total_supported_positive_gold,
            "supported_non_citation_findings": total_supported_non_citation_gold,
            "unsupported_other_normative_findings": total_unsupported_gold,
        },
        "prediction_stats": {
            "total_occurrences": total_predictions,
            "duplicate_same_anchor_occurrences": total_duplicate_anchor_predictions,
            "resolved_occurrences": sum(
                1 for stage, count in predicted_stage_counts.items() if not stage.startswith("Level 7")
                for _ in range(count)
            ),
            "unresolved_occurrences": predicted_stage_counts.get("Level 7: Unresolved", 0),
            "resolver_stages": dict(sorted(predicted_stage_counts.items())),
        },
        "metrics": {
            "supported_anchor_detection": {
                "tp": total_matched_supported,
                "fp": total_spurious_predictions,
                "fn": total_missing_supported,
                "precision": _round_metric(anchor_precision),
                "recall": _round_metric(anchor_recall),
                "f1": _round_metric(anchor_f1),
            },
            "supported_positive_citation_presence": {
                "tp": total_positive_tp,
                "fp": total_positive_fp,
                "fn": total_positive_fn,
                "precision": _round_metric(positive_precision),
                "recall": _round_metric(positive_recall),
                "f1": _round_metric(positive_f1),
            },
            "matched_supported_classification_accuracy": {
                "correct": exact_classification_matches,
                "total": matched_supported_for_classification,
                "accuracy": _round_metric(classification_accuracy),
                "confusion": {
                    gold_label: dict(sorted(pred_counts.items()))
                    for gold_label, pred_counts in sorted(classification_confusion.items())
                },
            },
            "czech_resolved_law_accuracy": {
                "correct": law_eval_correct,
                "total": law_eval_total,
                "accuracy": _round_metric(_safe_div(law_eval_correct, law_eval_total)),
            },
            "detail_accuracy_on_supported_positives": {
                "total": detail_eval_total,
                "number_exact": _round_metric(_safe_div(detail_number_correct, detail_eval_total)),
                "odst_exact": _round_metric(_safe_div(detail_odst_correct, detail_eval_total)),
                "pism_exact": _round_metric(_safe_div(detail_pism_correct, detail_eval_total)),
                "full_exact": _round_metric(_safe_div(detail_full_correct, detail_eval_total)),
            },
        },
        "error_taxonomy": {
            "counts": dict(sorted(taxonomy_counts.items())),
            "examples": taxonomy_examples,
        },
        "per_document": per_doc_rows,
    }

    return report


def _markdown_report(report: dict[str, Any], alias_source: str, gold_input: str) -> str:
    metrics = report["metrics"]
    gold_stats = report["gold_stats"]
    prediction_stats = report["prediction_stats"]
    taxonomy = report["error_taxonomy"]["counts"]

    lines = [
        "# Evaluation Report",
        "",
        f"- Generated: `{report['generated_at_utc']}`",
        f"- Gold input: `{gold_input}`",
        f"- Alias source: `{alias_source}`",
        f"- Gold documents: `{report['gold_documents']}`",
        "",
        "## Gold Coverage",
        "",
        f"- Total findings: `{gold_stats['total_findings']}`",
        f"- Supported findings: `{gold_stats['supported_findings']}`",
        f"- Supported positive findings: `{gold_stats['supported_positive_findings']}`",
        f"- Supported non-citation findings: `{gold_stats['supported_non_citation_findings']}`",
        f"- Unsupported `other_normative` findings: `{gold_stats['unsupported_other_normative_findings']}`",
        "",
        "## Prediction Summary",
        "",
        f"- Predicted occurrences: `{prediction_stats['total_occurrences']}`",
        f"- Duplicate same-anchor occurrences: `{prediction_stats['duplicate_same_anchor_occurrences']}`",
        f"- Resolved occurrences: `{prediction_stats['resolved_occurrences']}`",
        f"- Unresolved occurrences: `{prediction_stats['unresolved_occurrences']}`",
        "",
        "## Metrics",
        "",
        "### Supported Anchor Detection",
        "",
        f"- Precision: `{metrics['supported_anchor_detection']['precision']}`",
        f"- Recall: `{metrics['supported_anchor_detection']['recall']}`",
        f"- F1: `{metrics['supported_anchor_detection']['f1']}`",
        f"- TP / FP / FN: `{metrics['supported_anchor_detection']['tp']} / {metrics['supported_anchor_detection']['fp']} / {metrics['supported_anchor_detection']['fn']}`",
        "",
        "### Supported Positive Citation Presence",
        "",
        f"- Precision: `{metrics['supported_positive_citation_presence']['precision']}`",
        f"- Recall: `{metrics['supported_positive_citation_presence']['recall']}`",
        f"- F1: `{metrics['supported_positive_citation_presence']['f1']}`",
        f"- TP / FP / FN: `{metrics['supported_positive_citation_presence']['tp']} / {metrics['supported_positive_citation_presence']['fp']} / {metrics['supported_positive_citation_presence']['fn']}`",
        "",
        "### Classification / Resolution / Detail",
        "",
        f"- Matched supported classification accuracy: `{metrics['matched_supported_classification_accuracy']['accuracy']}`",
        f"- Czech resolved law accuracy: `{metrics['czech_resolved_law_accuracy']['accuracy']}`",
        f"- Detail number exact: `{metrics['detail_accuracy_on_supported_positives']['number_exact']}`",
        f"- Detail odst. exact: `{metrics['detail_accuracy_on_supported_positives']['odst_exact']}`",
        f"- Detail písm. exact: `{metrics['detail_accuracy_on_supported_positives']['pism_exact']}`",
        f"- Detail full exact: `{metrics['detail_accuracy_on_supported_positives']['full_exact']}`",
        "",
        "## Error Taxonomy",
        "",
    ]

    for key, count in sorted(taxonomy.items()):
        lines.append(f"- `{key}`: `{count}`")

    lines.extend(
        [
            "",
            "## Per-document Summary",
            "",
            "| Document | Gold supported | Gold positive | Predicted | Matched | Missing | Spurious | Duplicate same-anchor |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(report["per_document"], key=lambda x: x["document_id"]):
        lines.append(
            f"| `{row['document_id']}` | {row['gold_supported']} | {row['gold_supported_positive']} | "
            f"{row['predicted_occurrences']} | {row['matched_supported']} | "
            f"{row['missing_supported']} | {row['spurious_predictions']} | "
            f"{row['duplicate_anchor_predictions']} |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic extraction against gold annotations."
    )
    parser.add_argument(
        "--gold-input",
        default=DEFAULT_GOLD_INPUT,
        help="Gold aggregate JSONL, single gold JSON, or directory of per-document gold JSON files.",
    )
    parser.add_argument(
        "--audited-aliases",
        default=AUDITED_ALIASES_PATH,
        help="Audited alias dictionary path.",
    )
    parser.add_argument(
        "--global-aliases",
        default=GLOBAL_ALIASES_PATH,
        help="Fallback global alias dictionary path.",
    )
    parser.add_argument(
        "--seed-aliases",
        default=SEEDED_ALIASES_PATH,
        help="Optional seeded alias dictionary path.",
    )
    parser.add_argument(
        "--output-json",
        default=DEFAULT_JSON_OUTPUT,
        help="JSON output report path.",
    )
    parser.add_argument(
        "--output-md",
        default=DEFAULT_MD_OUTPUT,
        help="Markdown output report path.",
    )
    parser.add_argument(
        "--example-limit",
        type=int,
        default=5,
        help="Maximum stored examples per taxonomy bucket.",
    )
    parser.add_argument(
        "--predicted-occurrences",
        default=None,
        help="Optional JSONL of externally generated predicted occurrences to score instead of rerunning extraction.",
    )
    args = parser.parse_args()

    gold_docs = _load_gold_documents(args.gold_input)
    if not gold_docs:
        raise SystemExit(f"No gold documents found in {args.gold_input}")

    predicted_occurrences_by_document: dict[str, list[dict[str, Any]]] | None = None
    if args.predicted_occurrences:
        predicted_rows = _load_jsonl(args.predicted_occurrences)
        predicted_occurrences_by_document = defaultdict(list)
        for row in predicted_rows:
            document_id = row.get("document_id")
            if isinstance(document_id, str):
                predicted_occurrences_by_document[document_id].append(row)
        global_aliases = {}
        alias_source = "external_predictions"
    else:
        global_aliases, alias_source = load_runtime_aliases(
            audited_path=args.audited_aliases,
            global_path=args.global_aliases,
            seeded_path=args.seed_aliases,
        )
    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()

    report = evaluate(
        gold_docs=gold_docs,
        global_aliases=global_aliases,
        document_dates_index=document_dates_index,
        law_timelines=law_timelines,
        example_limit=args.example_limit,
        predicted_occurrences_by_document=predicted_occurrences_by_document,
    )
    report["alias_source"] = alias_source
    report["gold_input"] = args.gold_input

    _write_json(args.output_json, report)
    _write_text(args.output_md, _markdown_report(report, alias_source, args.gold_input))

    print("--- EVALUATION COMPLETE ---")
    print(f"Gold documents:          {report['gold_documents']}")
    print(f"Gold findings:           {report['gold_stats']['total_findings']}")
    print(f"Supported findings:      {report['gold_stats']['supported_findings']}")
    print(
        f"Unsupported findings:    "
        f"{report['gold_stats']['unsupported_other_normative_findings']}"
    )
    print(f"Predicted occurrences:   {report['prediction_stats']['total_occurrences']}")
    print(
        f"Anchor detection F1:     "
        f"{report['metrics']['supported_anchor_detection']['f1']}"
    )
    print(
        f"Citation presence F1:    "
        f"{report['metrics']['supported_positive_citation_presence']['f1']}"
    )
    print(
        f"Law resolution accuracy: "
        f"{report['metrics']['czech_resolved_law_accuracy']['accuracy']}"
    )
    print(f"JSON report:             {args.output_json}")
    print(f"Markdown report:         {args.output_md}")


if __name__ == "__main__":
    main()
