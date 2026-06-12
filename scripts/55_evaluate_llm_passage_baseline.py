#!/usr/bin/env python3
"""Evaluate bounded-passage LLM extraction baseline against joint gold."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


POSITIVE_LAW_CLASSES = {"czech_resolved", "czech_unresolved", "foreign_law"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate LLM passage baseline.")
    parser.add_argument(
        "--law-gold",
        default="data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl",
    )
    parser.add_argument(
        "--docref-gold",
        default="data/annotations/joint_reference_gold_v1/document_reference_gold/joint_document_reference_gold_v1.jsonl",
    )
    parser.add_argument(
        "--results",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_results.jsonl",
    )
    parser.add_argument(
        "--output-json",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_eval.json",
    )
    parser.add_argument(
        "--output-md",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_eval.md",
    )
    parser.add_argument(
        "--predictions-jsonl",
        default="data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_predictions.jsonl",
    )
    return parser.parse_args()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: str | Path, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def _write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_div(num: int, den: int) -> float:
    return num / den if den else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision and recall else 0.0


def _normalize_law_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_gold(law_gold: Path, docref_gold: Path) -> list[dict[str, Any]]:
    gold: list[dict[str, Any]] = []
    for doc in _load_jsonl(law_gold):
        document_id = str(doc["document_id"])
        for cit in doc.get("citations", []):
            if cit.get("classification") not in POSITIVE_LAW_CLASSES:
                continue
            gold.append(
                {
                    "kind": "law_reference",
                    "document_id": document_id,
                    "start": int(cit["start_char"]),
                    "end": int(cit["end_char"]),
                    "text": cit.get("citation_text"),
                    "classification": cit.get("classification"),
                    "law_id": _normalize_law_id(cit.get("law_id")),
                    "task_id": cit.get("task_id"),
                }
            )
    for row in _load_jsonl(docref_gold):
        if row.get("classification") != "document_reference":
            continue
        gold.append(
            {
                "kind": "document_reference",
                "document_id": str(row["document_id"]),
                "start": int(row["start_char"]),
                "end": int(row["end_char"]),
                "text": row.get("reference_text"),
                "classification": row.get("classification"),
                "law_id": None,
                "task_id": row.get("task_id"),
            }
        )
    return gold


def _load_predictions(results_path: Path) -> tuple[list[dict[str, Any]], Counter]:
    predictions_by_key: dict[tuple[str, str, int, int, str], dict[str, Any]] = {}
    stats: Counter = Counter()
    text_cache: dict[str, str] = {}

    for row in _load_jsonl(results_path):
        document_id = str(row["document_id"])
        passage_start = int(row["passage_start"])
        passage_end = int(row["passage_end"])
        document_path = str(row.get("document_path") or "")
        if document_path and document_path not in text_cache:
            text_cache[document_path] = Path(document_path).read_text(encoding="utf-8")
        doc_text = text_cache.get(document_path, "")
        result = row.get("result", {})
        if isinstance(result, dict):
            refs = result.get("references", [])
            if not isinstance(refs, list):
                required = result.get("required_output")
                if isinstance(required, dict) and isinstance(required.get("references"), list):
                    refs = required["references"]
                    stats["references_recovered_from_echoed_required_output"] += 1
        elif isinstance(result, list):
            refs = result
            stats["bare_result_list"] += 1
        else:
            refs = []
            stats["invalid_result_shape"] += 1
        if not isinstance(refs, list):
            stats["invalid_result_shape"] += 1
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                stats["invalid_reference_shape"] += 1
                continue
            kind = ref.get("reference_type")
            if kind not in {"law_reference", "document_reference"}:
                stats["invalid_reference_type"] += 1
                continue
            try:
                rel_start = int(ref["start_offset"])
                rel_end = int(ref["end_offset"])
            except (KeyError, TypeError, ValueError):
                stats["invalid_offsets"] += 1
                continue
            if rel_start < 0 or rel_end <= rel_start:
                stats["invalid_offsets"] += 1
                continue
            start = passage_start + rel_start
            end = passage_start + rel_end
            exact_text = str(ref.get("exact_text") or "")
            if doc_text and (start > len(doc_text) or end > len(doc_text)):
                stats["offset_out_of_document"] += 1
                continue
            observed = doc_text[start:end] if doc_text else exact_text
            if exact_text and observed != exact_text:
                stats["text_mismatch"] += 1
                if doc_text:
                    passage_text = doc_text[passage_start:passage_end]
                    matches: list[int] = []
                    search_from = 0
                    while True:
                        found = passage_text.find(exact_text, search_from)
                        if found < 0:
                            break
                        matches.append(found)
                        search_from = found + 1
                    if len(matches) == 1:
                        corrected_start = passage_start + matches[0]
                        corrected_end = corrected_start + len(exact_text)
                        start = corrected_start
                        end = corrected_end
                        observed = exact_text
                        stats["offset_corrected_from_unique_text"] += 1
                    elif len(matches) > 1:
                        closest = min(matches, key=lambda item: abs(item - rel_start))
                        corrected_start = passage_start + closest
                        corrected_end = corrected_start + len(exact_text)
                        start = corrected_start
                        end = corrected_end
                        observed = exact_text
                        stats["offset_corrected_from_nearest_text"] += 1
                    else:
                        stats["offset_not_corrected_text_not_found"] += 1
            key = (kind, document_id, start, end, observed)
            predictions_by_key[key] = {
                "kind": kind,
                "document_id": document_id,
                "start": start,
                "end": end,
                "text": observed,
                "model_text": exact_text,
                "law_id": _normalize_law_id(ref.get("law_id")),
                "target_identifier": ref.get("target_identifier"),
                "confidence": ref.get("confidence"),
            }
    return list(predictions_by_key.values()), stats


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> int:
    if a["document_id"] != b["document_id"] or a["kind"] != b["kind"]:
        return 0
    return max(0, min(int(a["end"]), int(b["end"])) - max(int(a["start"]), int(b["start"])))


def _score_kind(gold: list[dict[str, Any]], pred: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    gold_kind = [row for row in gold if row["kind"] == kind]
    pred_kind = [row for row in pred if row["kind"] == kind]
    gold_exact = {(row["document_id"], row["start"], row["end"]) for row in gold_kind}
    pred_exact = {(row["document_id"], row["start"], row["end"]) for row in pred_kind}
    tp_exact_keys = gold_exact & pred_exact
    exact_precision = _safe_div(len(tp_exact_keys), len(pred_exact))
    exact_recall = _safe_div(len(tp_exact_keys), len(gold_exact))

    unmatched_gold = set(range(len(gold_kind)))
    relaxed_tp = 0
    for prediction in pred_kind:
        best_idx: int | None = None
        best_overlap = 0
        for idx in unmatched_gold:
            amount = _overlap(prediction, gold_kind[idx])
            if amount > best_overlap:
                best_overlap = amount
                best_idx = idx
        if best_idx is not None and best_overlap > 0:
            unmatched_gold.remove(best_idx)
            relaxed_tp += 1
    relaxed_precision = _safe_div(relaxed_tp, len(pred_kind))
    relaxed_recall = _safe_div(relaxed_tp, len(gold_kind))

    report: dict[str, Any] = {
        "gold": len(gold_kind),
        "predicted": len(pred_kind),
        "exact_true_positive": len(tp_exact_keys),
        "exact_precision": exact_precision,
        "exact_recall": exact_recall,
        "exact_f1": _f1(exact_precision, exact_recall),
        "relaxed_overlap_true_positive": relaxed_tp,
        "relaxed_overlap_precision": relaxed_precision,
        "relaxed_overlap_recall": relaxed_recall,
        "relaxed_overlap_f1": _f1(relaxed_precision, relaxed_recall),
    }

    if kind == "law_reference":
        gold_by_span = {(row["document_id"], row["start"], row["end"]): row for row in gold_kind}
        pred_by_span = {(row["document_id"], row["start"], row["end"]): row for row in pred_kind}
        czech_total = 0
        czech_correct = 0
        czech_predicted = 0
        for key in tp_exact_keys:
            gold_row = gold_by_span[key]
            pred_row = pred_by_span[key]
            if gold_row.get("classification") == "czech_resolved" and gold_row.get("law_id"):
                czech_total += 1
                if pred_row.get("law_id"):
                    czech_predicted += 1
                if pred_row.get("law_id") == gold_row.get("law_id"):
                    czech_correct += 1
        report["czech_law_id_accuracy_on_exact_matches"] = _safe_div(czech_correct, czech_total)
        report["czech_law_id_support"] = czech_total
        report["czech_law_id_predicted_share_on_exact_matches"] = _safe_div(czech_predicted, czech_total)

    return report


def main() -> None:
    args = parse_args()
    gold = _load_gold(Path(args.law_gold), Path(args.docref_gold))
    predictions, prediction_stats = _load_predictions(Path(args.results))
    _write_jsonl(args.predictions_jsonl, predictions)

    report = {
        "law_gold": args.law_gold,
        "docref_gold": args.docref_gold,
        "results": args.results,
        "predictions_jsonl": args.predictions_jsonl,
        "prediction_quality_counts": dict(prediction_stats),
        "combined": _score_kind(gold, predictions, "law_reference"),
        "law_reference": _score_kind(gold, predictions, "law_reference"),
        "document_reference": _score_kind(gold, predictions, "document_reference"),
    }

    # Replace the accidentally law-only combined block with a true combined score.
    gold_exact = {(row["kind"], row["document_id"], row["start"], row["end"]) for row in gold}
    pred_exact = {(row["kind"], row["document_id"], row["start"], row["end"]) for row in predictions}
    tp = len(gold_exact & pred_exact)
    precision = _safe_div(tp, len(pred_exact))
    recall = _safe_div(tp, len(gold_exact))
    report["combined"] = {
        "gold": len(gold_exact),
        "predicted": len(pred_exact),
        "exact_true_positive": tp,
        "exact_precision": precision,
        "exact_recall": recall,
        "exact_f1": _f1(precision, recall),
    }

    lines = [
        "# LLM Passage Baseline Evaluation",
        "",
        f"- results: `{args.results}`",
        f"- prediction rows: `{args.predictions_jsonl}`",
        "",
        "## Exact-Span Metrics",
        "",
        "| Task | Gold | Predicted | TP | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in [
        ("Combined", "combined"),
        ("Law references", "law_reference"),
        ("Document references", "document_reference"),
    ]:
        block = report[key]
        lines.append(
            f"| {label} | {block['gold']} | {block['predicted']} | {block['exact_true_positive']} | "
            f"{block['exact_precision']:.4f} | {block['exact_recall']:.4f} | {block['exact_f1']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Relaxed Overlap Metrics",
            "",
            "| Task | TP | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, key in [("Law references", "law_reference"), ("Document references", "document_reference")]:
        block = report[key]
        lines.append(
            f"| {label} | {block['relaxed_overlap_true_positive']} | "
            f"{block['relaxed_overlap_precision']:.4f} | {block['relaxed_overlap_recall']:.4f} | "
            f"{block['relaxed_overlap_f1']:.4f} |"
        )
    law = report["law_reference"]
    lines.extend(
        [
            "",
            "## Law-ID Diagnostic",
            "",
            f"- Czech-law ID accuracy on exact law-span matches: `{law['czech_law_id_accuracy_on_exact_matches']:.4f}`",
            f"- support: `{law['czech_law_id_support']}`",
            f"- share with any predicted law id: `{law['czech_law_id_predicted_share_on_exact_matches']:.4f}`",
            "",
            "## Prediction Quality Counts",
            "",
        ]
    )
    if prediction_stats:
        for key, value in sorted(prediction_stats.items()):
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")

    _write_json(args.output_json, report)
    _write_text(args.output_md, "\n".join(lines) + "\n")

    print("--- LLM PASSAGE BASELINE EVALUATION COMPLETE ---")
    print(f"Combined exact F1: {report['combined']['exact_f1']:.4f}")
    print(f"Law exact F1:      {report['law_reference']['exact_f1']:.4f}")
    print(f"Docref exact F1:   {report['document_reference']['exact_f1']:.4f}")
    print(f"JSON output:       {args.output_json}")
    print(f"Markdown output:   {args.output_md}")


if __name__ == "__main__":
    main()
