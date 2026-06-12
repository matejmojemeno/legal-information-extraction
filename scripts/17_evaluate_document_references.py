#!/usr/bin/env python3
"""
Evaluate document-reference extraction against manually annotated gold.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.document_reference_extractor import extract_document_references


def _safe_div(num: int, den: int) -> float:
    return float(num) / float(den) if den else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision and recall else 0.0


def _load_gold(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _load_predictions(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate document-reference extraction against gold."
    )
    parser.add_argument(
        "--gold",
        default="data/annotations/document_references/gold/document_reference_gold_v1.jsonl",
        help="Aggregate gold JSONL path.",
    )
    parser.add_argument(
        "--output-json",
        default="data/annotations/document_references/eval/document_reference_eval_v1.json",
        help="Evaluation report JSON path.",
    )
    parser.add_argument(
        "--output-md",
        default="data/annotations/document_references/eval/document_reference_eval_v1.md",
        help="Evaluation report Markdown path.",
    )
    parser.add_argument(
        "--predicted-occurrences",
        default=None,
        help="Optional JSONL of externally generated prediction rows to score instead of rerunning extraction.",
    )
    args = parser.parse_args()

    gold_rows = _load_gold(args.gold)
    gold_positive = [row for row in gold_rows if row.get("classification") == "document_reference"]
    if not gold_positive:
        raise SystemExit(f"No positive gold rows in: {args.gold}")

    gold_by_doc: dict[str, list[dict]] = {}
    for row in gold_positive:
        gold_by_doc.setdefault(str(row["document_path"]), []).append(row)

    predicted_by_doc: dict[str, list[dict]] | None = None
    if args.predicted_occurrences:
        predicted_by_doc = {}
        for row in _load_predictions(args.predicted_occurrences):
            document_path = row.get("document_path")
            if isinstance(document_path, str):
                predicted_by_doc.setdefault(document_path, []).append(row)

    total_pred = 0
    total_gold = len(gold_positive)
    tp_exact = 0
    matched_type = 0
    matched_body = 0
    kind_eval_total = 0
    kind_eval_correct = 0
    court_eval_total = 0
    court_eval_correct = 0
    docs_evaluated = 0
    per_doc_rows: list[dict] = []

    for doc_path, doc_gold in sorted(gold_by_doc.items()):
        if predicted_by_doc is None:
            with open(doc_path, "r", encoding="utf-8") as f:
                text = f.read()
            predicted = extract_document_references(text)
        else:
            predicted = predicted_by_doc.get(doc_path, [])
        docs_evaluated += 1
        total_pred += len(predicted)

        pred_map = {
            (item.raw_start, item.raw_end): item
            for item in predicted
        } if predicted_by_doc is None else {
            (int(item["raw_start"]), int(item["raw_end"])): item for item in predicted
        }

        doc_tp = 0
        for gold in doc_gold:
            key = (int(gold["start_char"]), int(gold["end_char"]))
            pred = pred_map.get(key)
            if pred is None:
                continue
            tp_exact += 1
            doc_tp += 1
            pred_reference_type = pred.reference_type if predicted_by_doc is None else pred.get("reference_type")
            pred_reference_body = pred.reference_body if predicted_by_doc is None else pred.get("reference_body")
            pred_decision_kind_hint = pred.decision_kind_hint if predicted_by_doc is None else pred.get("decision_kind_hint")
            pred_court_hint = pred.court_hint if predicted_by_doc is None else pred.get("court_hint")
            if pred_reference_type == gold.get("reference_type"):
                matched_type += 1
            if pred_reference_body == gold.get("reference_body"):
                matched_body += 1
            if gold.get("decision_kind_hint"):
                kind_eval_total += 1
                if pred_decision_kind_hint == gold.get("decision_kind_hint"):
                    kind_eval_correct += 1
            if gold.get("court_hint"):
                court_eval_total += 1
                if pred_court_hint == gold.get("court_hint"):
                    court_eval_correct += 1

        per_doc_rows.append(
            {
                "document_path": doc_path,
                "document_id": doc_gold[0]["document_id"],
                "gold_references": len(doc_gold),
                "predicted_references": len(predicted),
                "exact_matches": doc_tp,
            }
        )

    precision = _safe_div(tp_exact, total_pred)
    recall = _safe_div(tp_exact, total_gold)
    report = {
        "docs_evaluated": docs_evaluated,
        "gold_positive_references": total_gold,
        "predicted_references": total_pred,
        "exact_span_precision": precision,
        "exact_span_recall": recall,
        "exact_span_f1": _f1(precision, recall),
        "reference_type_accuracy_on_exact_matches": _safe_div(matched_type, tp_exact),
        "reference_body_accuracy_on_exact_matches": _safe_div(matched_body, tp_exact),
        "decision_kind_hint_accuracy": _safe_div(kind_eval_correct, kind_eval_total),
        "decision_kind_hint_support": kind_eval_total,
        "court_hint_accuracy": _safe_div(court_eval_correct, court_eval_total),
        "court_hint_support": court_eval_total,
        "per_document": per_doc_rows,
    }

    output_dir = os.path.dirname(args.output_json)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md_lines = [
        "# Document Reference Evaluation",
        "",
        f"- docs evaluated: {report['docs_evaluated']}",
        f"- gold positive references: {report['gold_positive_references']}",
        f"- predicted references: {report['predicted_references']}",
        f"- exact span precision: {report['exact_span_precision']:.4f}",
        f"- exact span recall: {report['exact_span_recall']:.4f}",
        f"- exact span F1: {report['exact_span_f1']:.4f}",
        f"- reference_type accuracy on exact matches: {report['reference_type_accuracy_on_exact_matches']:.4f}",
        f"- reference_body accuracy on exact matches: {report['reference_body_accuracy_on_exact_matches']:.4f}",
        f"- decision_kind_hint accuracy: {report['decision_kind_hint_accuracy']:.4f} (support={report['decision_kind_hint_support']})",
        f"- court_hint accuracy: {report['court_hint_accuracy']:.4f} (support={report['court_hint_support']})",
    ]
    with open(args.output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"Saved JSON report to {args.output_json}")
    print(f"Saved Markdown report to {args.output_md}")


if __name__ == "__main__":
    main()
