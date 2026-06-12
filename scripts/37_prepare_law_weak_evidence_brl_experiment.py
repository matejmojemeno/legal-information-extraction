#!/usr/bin/env python3
"""
Prepare and optionally score a BRL experiment for weak law-resolution evidence.

The normal production BRL queue is intentionally narrow. It contains unresolved
law references and selected explicit ambiguity signals. This helper creates a
separate reviewed benchmark queue from the 25-document gold set for resolved
low-confidence contextual stages, so we can test whether BRL would improve or
degrade those already-resolved cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


WEAK_STAGE_PREFIXES = (
    "Level 3: Implicit Generic Reference",
    "Level 3B: Citation Chain Carryover",
    "Level 3C: Backward Context Carryover",
    "Level 4: Short Structural Reference",
)


def _entry_id(target_reference: str, context_block: str) -> str:
    payload = f"{target_reference}\n{context_block}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_gold_docs(path: Path) -> list[dict[str, Any]]:
    rows = _load_jsonl(path)
    if rows and "citations" in rows[0]:
        return rows

    by_doc: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("document_id", "")),
            str(row.get("source", "")),
            str(row.get("document_path", "")),
        )
        doc = by_doc.setdefault(
            key,
            {
                "document_id": key[0],
                "source": key[1],
                "document_path": key[2],
                "citations": [],
            },
        )
        doc["citations"].append(row)
    return list(by_doc.values())


def _normalized_gold_anchor_start(citation: dict[str, Any]) -> int:
    start = int(citation["start_char"])
    citation_text = str(citation.get("citation_text") or "")
    citation_type = str(citation.get("citation_type") or "")
    detail_number = str(citation.get("detail_number") or "").strip()

    if citation_type == "section" and detail_number:
        match = re.search(rf"§{{1,2}}\s*{re.escape(detail_number)}\b", citation_text)
        if match:
            return start + match.start()
    if citation_type == "article" and detail_number:
        match = re.search(
            rf"(?:čl\.|článek)\s*{re.escape(detail_number)}\b",
            citation_text,
            flags=re.IGNORECASE,
        )
        if match:
            return start + match.start()
    if citation_type == "other_normative":
        law_id = str(citation.get("law_id") or "").strip()
        if law_id:
            match = re.search(re.escape(law_id), citation_text, flags=re.IGNORECASE)
            if match:
                return start + match.start()

    fallback = re.search(
        r"(?:§{1,2}|čl\.|článek|\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.)",
        citation_text,
        flags=re.IGNORECASE,
    )
    if fallback:
        return start + fallback.start()
    return start


def _gold_key(document_id: str, citation: dict[str, Any]) -> tuple[str, int, str]:
    return (
        document_id,
        _normalized_gold_anchor_start(citation),
        str(citation.get("citation_type", "")),
    )


def _prediction_key(prediction: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(prediction.get("document_id", "")),
        int(prediction.get("raw_start", 0)),
        str(prediction.get("citation_type", "")),
    )


def _prediction_label(prediction: dict[str, Any]) -> str:
    explicit = prediction.get("predicted_classification")
    if explicit:
        return str(explicit)
    return "czech_resolved" if prediction.get("resolved_law_id") else "czech_unresolved"


def _is_weak_evidence_prediction(row: dict[str, Any], threshold: float) -> bool:
    if _prediction_label(row) != "czech_resolved":
        return False
    try:
        confidence = float(row.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence >= threshold:
        return False
    stage = str(row.get("resolver_stage") or "")
    return any(stage.startswith(prefix) for prefix in WEAK_STAGE_PREFIXES)


def _context_for_prediction(row: dict[str, Any], radius: int) -> str:
    path = Path(str(row["document_path"]))
    text = path.read_text(encoding="utf-8")
    start = int(row.get("raw_start", 0))
    end = int(row.get("raw_end", start))
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _build_queue_row(row: dict[str, Any], context_radius: int) -> dict[str, Any]:
    target = str(row.get("citation_text") or "").strip()
    context = _context_for_prediction(row, context_radius).strip()
    candidates = []
    seen = set()
    for law_id in [row.get("resolved_law_id"), *row.get("candidate_law_ids", [])]:
        if isinstance(law_id, str) and law_id and law_id not in seen:
            seen.add(law_id)
            candidates.append(law_id)

    entry_id = _entry_id(target, context)
    return {
        "entry_id": entry_id,
        "target_reference": target,
        "context_block": context,
        "candidates": candidates,
        "current_resolved_law_id": row.get("resolved_law_id"),
        "resolver_stage": row.get("resolver_stage"),
        "confidence": row.get("confidence"),
        "route_reason": "weak_evidence_contextual_resolution",
        "document_id": row.get("document_id"),
        "document_path": row.get("document_path"),
        "document_source": row.get("source"),
        "citation_type": row.get("citation_type"),
        "raw_start": row.get("raw_start"),
        "raw_end": row.get("raw_end"),
    }


def _summarize(labels: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    total = len(labels)
    class_correct = sum(1 for row in labels if row[f"{prefix}_classification_correct"])
    full_correct = sum(1 for row in labels if row[f"{prefix}_fully_correct"])
    czech_total = sum(1 for row in labels if row["gold_classification"] == "czech_resolved")
    czech_correct = sum(1 for row in labels if row[f"{prefix}_czech_law_correct"])
    czech_wrong = sum(1 for row in labels if row[f"{prefix}_czech_law_wrong"])
    return {
        "rows": total,
        "classification_accuracy": class_correct / total if total else 0.0,
        "full_accuracy": full_correct / total if total else 0.0,
        "czech_law_rows": czech_total,
        "czech_law_accuracy": czech_correct / czech_total if czech_total else None,
        "czech_law_wrong": czech_wrong,
    }


def _load_step6_results(path: Path) -> dict[str, dict[str, Any]]:
    rows = _load_jsonl(path)
    return {
        str(row.get("entry_id")): row
        for row in rows
        if isinstance(row.get("entry_id"), str)
    }


def _add_llm_scores(
    labels: list[dict[str, Any]],
    step6_results: dict[str, dict[str, Any]],
) -> None:
    for row in labels:
        result_row = step6_results.get(str(row["entry_id"])) or {}
        result = result_row.get("result") if isinstance(result_row, dict) else {}
        if not isinstance(result, dict):
            result = {}

        llm_class = str(result.get("classification") or "missing")
        llm_law_id = result.get("resolved_law_id")
        if llm_law_id is not None:
            llm_law_id = str(llm_law_id)

        gold_class = row["gold_classification"]
        gold_law_id = row["gold_law_id"]
        row["llm_classification"] = llm_class
        row["llm_law_id"] = llm_law_id
        row["llm_classification_correct"] = llm_class == gold_class
        row["llm_fully_correct"] = (
            llm_class == gold_class
            and (gold_class != "czech_resolved" or llm_law_id == gold_law_id)
        )
        row["llm_czech_law_correct"] = (
            gold_class == "czech_resolved"
            and llm_class == "czech_resolved"
            and llm_law_id == gold_law_id
        )
        row["llm_czech_law_wrong"] = (
            gold_class == "czech_resolved"
            and llm_class == "czech_resolved"
            and llm_law_id != gold_law_id
        )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Law Weak-Evidence BRL Experiment",
        "",
        f"- queue rows: `{summary['queue_rows']}`",
        f"- confidence threshold: `{summary['confidence_threshold']}`",
        f"- queue output: `{summary['queue_output']}`",
        f"- label output: `{summary['label_output']}`",
        "",
        "## Stage Counts",
        "",
    ]
    for stage, count in summary["stage_counts"].items():
        lines.append(f"- `{stage}`: {count}")
    lines.extend(["", "## SPP Baseline on This Slice", ""])
    spp = summary["spp"]
    lines.extend(
        [
            f"- classification accuracy: `{spp['classification_accuracy']:.4f}`",
            f"- full accuracy: `{spp['full_accuracy']:.4f}`",
            f"- Czech-law accuracy: `{spp['czech_law_accuracy']:.4f}`",
            f"- Czech-law wrong: `{spp['czech_law_wrong']}`",
        ]
    )
    if "llm" in summary:
        llm = summary["llm"]
        lines.extend(["", "## BRL on This Slice", ""])
        lines.extend(
            [
                f"- classification accuracy: `{llm['classification_accuracy']:.4f}`",
                f"- full accuracy: `{llm['full_accuracy']:.4f}`",
                f"- Czech-law accuracy: `{llm['czech_law_accuracy']:.4f}`",
                f"- Czech-law wrong: `{llm['czech_law_wrong']}`",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and optionally score weak-evidence law BRL experiment."
    )
    parser.add_argument(
        "--gold",
        default="data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl",
    )
    parser.add_argument(
        "--predictions",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.deterministic_predictions.jsonl",
    )
    parser.add_argument(
        "--queue-output",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval/law_weak_evidence_queue.json",
    )
    parser.add_argument(
        "--label-output",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval/law_weak_evidence_labels.json",
    )
    parser.add_argument(
        "--summary-output",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval/law_weak_evidence_experiment_summary.json",
    )
    parser.add_argument(
        "--markdown-output",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval/law_weak_evidence_experiment_summary.md",
    )
    parser.add_argument("--step6-results", default=None)
    parser.add_argument("--confidence-threshold", type=float, default=0.75)
    parser.add_argument("--context-radius", type=int, default=650)
    args = parser.parse_args()

    gold_docs = _load_gold_docs(Path(args.gold))
    gold_by_key = {}
    for doc in gold_docs:
        document_id = str(doc.get("document_id") or "")
        for citation in doc.get("citations", []):
            gold_by_key[_gold_key(document_id, citation)] = citation

    predictions = _load_jsonl(Path(args.predictions))
    queue = []
    labels = []
    stage_counts: Counter[str] = Counter()

    for pred in predictions:
        if not _is_weak_evidence_prediction(pred, args.confidence_threshold):
            continue
        gold = gold_by_key.get(_prediction_key(pred))
        if gold is None:
            continue

        queue_row = _build_queue_row(pred, args.context_radius)
        queue.append(queue_row)
        stage = str(pred.get("resolver_stage") or "")
        stage_counts[stage] += 1

        gold_class = str(gold.get("classification") or "")
        gold_law_id = gold.get("law_id")
        if gold_law_id is not None:
            gold_law_id = str(gold_law_id)
        spp_class = _prediction_label(pred)
        spp_law_id = pred.get("resolved_law_id")
        if spp_law_id is not None:
            spp_law_id = str(spp_law_id)

        labels.append(
            {
                "entry_id": queue_row["entry_id"],
                "document_id": pred.get("document_id"),
                "citation_text": pred.get("citation_text"),
                "citation_type": pred.get("citation_type"),
                "raw_start": pred.get("raw_start"),
                "resolver_stage": pred.get("resolver_stage"),
                "confidence": pred.get("confidence"),
                "gold_classification": gold_class,
                "gold_law_id": gold_law_id,
                "spp_classification": spp_class,
                "spp_law_id": spp_law_id,
                "spp_classification_correct": spp_class == gold_class,
                "spp_fully_correct": (
                    spp_class == gold_class
                    and (gold_class != "czech_resolved" or spp_law_id == gold_law_id)
                ),
                "spp_czech_law_correct": (
                    gold_class == "czech_resolved"
                    and spp_class == "czech_resolved"
                    and spp_law_id == gold_law_id
                ),
                "spp_czech_law_wrong": (
                    gold_class == "czech_resolved"
                    and spp_class == "czech_resolved"
                    and spp_law_id != gold_law_id
                ),
            }
        )

    if args.step6_results:
        _add_llm_scores(labels, _load_step6_results(Path(args.step6_results)))

    summary = {
        "gold_input": args.gold,
        "predictions_input": args.predictions,
        "queue_output": args.queue_output,
        "label_output": args.label_output,
        "queue_rows": len(queue),
        "confidence_threshold": args.confidence_threshold,
        "stage_counts": dict(sorted(stage_counts.items())),
        "spp": _summarize(labels, "spp"),
    }
    if args.step6_results:
        summary["step6_results"] = args.step6_results
        summary["llm"] = _summarize(labels, "llm")

    _write_json(Path(args.queue_output), queue)
    _write_json(Path(args.label_output), labels)
    _write_json(Path(args.summary_output), summary)
    _write_markdown(Path(args.markdown_output), summary)

    print("--- LAW WEAK-EVIDENCE BRL EXPERIMENT READY ---")
    print(f"Queue rows:       {len(queue)}")
    print(f"Queue output:     {args.queue_output}")
    print(f"Label output:     {args.label_output}")
    print(f"Summary output:   {args.summary_output}")
    print(f"Markdown output:  {args.markdown_output}")


if __name__ == "__main__":
    main()
