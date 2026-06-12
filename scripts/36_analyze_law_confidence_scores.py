#!/usr/bin/env python3
"""
Analyze law-resolver confidence scores on the joint gold set.

The resolver confidence is a fixed score derived from the stage that produced a
prediction. This script groups matched gold findings by confidence/stage family
and reports how often the resulting classification and law identifier were
correct.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


GOLD_PATH = Path("data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl")
PREDICTIONS_PATH = Path(
    "data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.deterministic_predictions.jsonl"
)
OUTPUT_JSON = Path("data/annotations/joint_reference_gold_v1/law_confidence_analysis.json")
OUTPUT_MD = Path("data/annotations/joint_reference_gold_v1/law_confidence_analysis.md")


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


def _prediction_label(prediction: dict[str, Any]) -> str:
    explicit = prediction.get("predicted_classification")
    if explicit:
        return str(explicit)
    return "czech_resolved" if prediction.get("resolved_law_id") else "czech_unresolved"


def _stage_family(stage: str) -> str:
    if stage.startswith("Level 5A"):
        return "Level 5A: Ambiguous Local Alias + Context"
    if stage.startswith("Level 5"):
        return "Level 5: Local Dictionary"
    if stage.startswith("Level 6A"):
        return "Level 6A: Ambiguous Global Alias + Context"
    if stage.startswith("Level 6"):
        return "Level 6: Global Dictionary"
    return re.sub(r"\s+\('[^']+'\)$", "", stage)


def _safe_div(num: int, den: int) -> float | None:
    return num / den if den else None


def _round4(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def _new_bucket(confidence: float, stage_family: str) -> dict[str, Any]:
    return {
        "confidence": confidence,
        "stage_family": stage_family,
        "matched_gold": 0,
        "classification_correct": 0,
        "fully_correct": 0,
        "czech_law_total": 0,
        "czech_law_correct": 0,
        "czech_law_unresolved": 0,
        "czech_law_wrong": 0,
        "gold_class_counts": defaultdict(int),
        "predicted_class_counts": defaultdict(int),
    }


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    matched = int(bucket["matched_gold"])
    czech_total = int(bucket["czech_law_total"])
    return {
        "confidence": bucket["confidence"],
        "stage_family": bucket["stage_family"],
        "matched_gold": matched,
        "classification_correct": bucket["classification_correct"],
        "classification_accuracy": _round4(_safe_div(bucket["classification_correct"], matched)),
        "fully_correct": bucket["fully_correct"],
        "full_accuracy": _round4(_safe_div(bucket["fully_correct"], matched)),
        "czech_law_total": czech_total,
        "czech_law_correct": bucket["czech_law_correct"],
        "czech_law_accuracy": _round4(_safe_div(bucket["czech_law_correct"], czech_total)),
        "czech_law_unresolved": bucket["czech_law_unresolved"],
        "czech_law_wrong": bucket["czech_law_wrong"],
        "gold_class_counts": dict(sorted(bucket["gold_class_counts"].items())),
        "predicted_class_counts": dict(sorted(bucket["predicted_class_counts"].items())),
    }


def _fully_correct(gold: dict[str, Any], prediction: dict[str, Any]) -> bool:
    gold_class = str(gold.get("classification") or "")
    predicted_class = _prediction_label(prediction)
    if gold_class != predicted_class:
        return False
    if gold_class == "czech_resolved":
        return prediction.get("resolved_law_id") == gold.get("law_id")
    return True


def build_analysis() -> dict[str, Any]:
    gold_docs = LAW_EVAL._load_gold_documents(str(GOLD_PATH))
    prediction_rows = _load_jsonl(PREDICTIONS_PATH)
    predictions_by_key = {
        LAW_EVAL._key_for_prediction(str(row["document_id"]), row): row
        for row in prediction_rows
    }

    buckets: dict[tuple[float, str], dict[str, Any]] = {}
    unmatched_gold = 0
    matched_gold = 0

    for document in gold_docs:
        document_id = str(document["document_id"])
        for citation in LAW_EVAL._dedupe_gold_citations(document.get("citations", [])):
            key = LAW_EVAL._key_for_gold({**citation, "document_id": document_id})
            prediction = predictions_by_key.get(key)
            if prediction is None:
                unmatched_gold += 1
                continue

            matched_gold += 1
            confidence = float(prediction.get("confidence") or 0.0)
            stage_family = _stage_family(str(prediction.get("resolver_stage") or "unknown"))
            bucket_key = (confidence, stage_family)
            bucket = buckets.setdefault(bucket_key, _new_bucket(confidence, stage_family))

            gold_class = str(citation.get("classification") or "")
            predicted_class = _prediction_label(prediction)
            bucket["matched_gold"] += 1
            bucket["gold_class_counts"][gold_class] += 1
            bucket["predicted_class_counts"][predicted_class] += 1
            if gold_class == predicted_class:
                bucket["classification_correct"] += 1
            if _fully_correct(citation, prediction):
                bucket["fully_correct"] += 1

            if gold_class == "czech_resolved":
                bucket["czech_law_total"] += 1
                if prediction.get("resolved_law_id") == citation.get("law_id"):
                    bucket["czech_law_correct"] += 1
                elif predicted_class != "czech_resolved" or not prediction.get("resolved_law_id"):
                    bucket["czech_law_unresolved"] += 1
                else:
                    bucket["czech_law_wrong"] += 1

    rows = [
        _finalize_bucket(bucket)
        for _, bucket in sorted(
            buckets.items(),
            key=lambda item: (-item[0][0], item[0][1]),
        )
    ]

    return {
        "gold_input": str(GOLD_PATH),
        "predictions_input": str(PREDICTIONS_PATH),
        "matched_gold": matched_gold,
        "unmatched_gold": unmatched_gold,
        "rows": rows,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Law Confidence Analysis",
        "",
        f"- gold input: `{report['gold_input']}`",
        f"- predictions input: `{report['predictions_input']}`",
        f"- matched gold findings: `{report['matched_gold']}`",
        f"- unmatched gold findings: `{report['unmatched_gold']}`",
        "",
        "| Confidence | Stage family | Matched | Class acc. | Czech-law acc. | Czech unresolved | Czech wrong |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        czech_accuracy = "--" if row["czech_law_accuracy"] is None else f"{row['czech_law_accuracy']:.4f}"
        lines.append(
            f"| {row['confidence']:.2f} | {row['stage_family']} | {row['matched_gold']} | "
            f"{row['classification_accuracy']:.4f} | {czech_accuracy} | "
            f"{row['czech_law_unresolved']} | {row['czech_law_wrong']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    report = build_analysis()
    _write_json(OUTPUT_JSON, report)
    _write_text(OUTPUT_MD, _markdown(report))
    print("--- LAW CONFIDENCE ANALYSIS COMPLETE ---")
    print(f"Rows:        {len(report['rows'])}")
    print(f"JSON output: {OUTPUT_JSON}")
    print(f"MD output:   {OUTPUT_MD}")


if __name__ == "__main__":
    main()
