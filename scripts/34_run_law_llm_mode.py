#!/usr/bin/env python3
"""
Run law-reference evaluation on a reviewed gold set in one of three modes:

- deterministic: current deterministic pipeline only
- llm: deterministic pipeline plus Step 6 overlay on routed anomalies
- compare: run both and write a comparison report with full-set and routed-subset deltas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import (
    extract_citation_occurrences,
    occurrences_to_resolved_and_anomalies,
)
from src.document_metadata import load_document_dates_index, load_law_timelines
from src.production_paths import (
    PRODUCTION_AMBIGUOUS_ALIASES_PATH,
    PRODUCTION_AUDITED_ALIASES_PATH,
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_GLOBAL_ALIASES_PATH,
    PRODUCTION_SEED_ALIASES_PATH,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run law-reference deterministic vs Step 6 LLM comparison on a reviewed gold set."
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "llm", "compare"],
        required=True,
        help="Which mode to run.",
    )
    parser.add_argument(
        "--gold-input",
        default="data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl",
        help="Gold aggregate JSONL, single gold JSON, or directory of per-document gold JSON files.",
    )
    parser.add_argument(
        "--tag",
        default="current",
        help="Tag for output file names.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/annotations/joint_reference_gold_v1/law_llm_eval",
        help="Directory for generated predictions, queues, and reports.",
    )
    parser.add_argument(
        "--audited-aliases",
        default=PRODUCTION_AUDITED_ALIASES_PATH,
        help="Audited alias dictionary path for deterministic extraction.",
    )
    parser.add_argument(
        "--global-aliases",
        default=PRODUCTION_GLOBAL_ALIASES_PATH,
        help="Fallback global alias dictionary path for deterministic extraction.",
    )
    parser.add_argument(
        "--seed-aliases",
        default=PRODUCTION_SEED_ALIASES_PATH,
        help="Optional seeded alias dictionary path for deterministic extraction.",
    )
    parser.add_argument(
        "--ambiguous-aliases",
        default=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
        help="Optional ambiguous runtime alias dictionary path for deterministic extraction.",
    )
    parser.add_argument(
        "--step6-audited-aliases",
        default=PRODUCTION_AUDITED_ALIASES_PATH,
        help="Audited alias path used by Step 6.",
    )
    parser.add_argument(
        "--step6-global-aliases",
        default=PRODUCTION_GLOBAL_ALIASES_PATH,
        help="Global alias path used by Step 6.",
    )
    parser.add_argument(
        "--step6-ambiguous-aliases",
        default=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
        help="Ambiguous alias path used by Step 6.",
    )
    parser.add_argument(
        "--step6-seed-aliases",
        default=PRODUCTION_SEED_ALIASES_PATH,
        help="Seed alias path used by Step 6.",
    )
    parser.add_argument(
        "--canonical",
        default=PRODUCTION_CANONICAL_LAWS_PATH,
        help="Canonical laws path for Step 6.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model name for llm/compare mode.",
    )
    parser.add_argument("--timeout-ms", type=int, default=10000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--queue-low-confidence",
        action="store_true",
        help="Also queue ambiguity-heavy low-confidence deterministic resolutions.",
    )
    parser.add_argument(
        "--llm-predicted",
        default=None,
        help="Existing Step 6 JSONL output to reuse in compare mode.",
    )
    return parser.parse_args()


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def _safe_div(num: int, den: int) -> float:
    return num / den if den else 0.0


def _flatten_occurrence(
    *,
    document_id: str,
    document_path: str,
    source: str,
    occ,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "document_path": document_path,
        "source": source,
        "citation_text": occ.citation_text,
        "citation_type": occ.citation_type,
        "raw_start": occ.raw_start,
        "raw_end": occ.raw_end,
        "resolved_law_id": occ.resolved_law_id,
        "predicted_classification": occ.predicted_classification,
        "resolver_stage": occ.resolver_stage,
        "confidence": occ.confidence,
        "quality_flag": occ.quality_flag,
        "quality_reason": occ.quality_reason,
        "parsed_detail": occ.parsed_detail,
        "candidate_law_ids": occ.candidate_law_ids,
    }


def _build_deterministic_predictions_and_queue(
    gold_docs: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    global_aliases, alias_source = load_runtime_aliases(
        audited_path=args.audited_aliases,
        global_path=args.global_aliases,
        seeded_path=args.seed_aliases,
        ambiguous_path=args.ambiguous_aliases,
    )
    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()

    predictions: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    by_reason = Counter()

    for gold_doc in sorted(gold_docs, key=lambda x: str(x.get("document_id", ""))):
        document_id = str(gold_doc["document_id"])
        document_path = str(gold_doc["document_path"])
        source = str(gold_doc.get("source", ""))

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
        predictions.extend(
            _flatten_occurrence(
                document_id=document_id,
                document_path=document_path,
                source=source,
                occ=occ,
            )
            for occ in occurrences
        )
        _, doc_anomalies = occurrences_to_resolved_and_anomalies(
            occurrences,
            raw_text,
            include_low_confidence_anomalies=args.queue_low_confidence,
            document_id=document_id,
            document_path=document_path,
            document_source=source,
        )
        anomalies.extend(doc_anomalies)
        for row in doc_anomalies:
            by_reason[str(row.get("route_reason") or "unknown")] += 1

    summary = {
        "alias_source": alias_source,
        "documents": len(gold_docs),
        "predicted_occurrences": len(predictions),
        "routed_anomalies": len(anomalies),
        "route_reasons": dict(sorted(by_reason.items())),
    }
    return predictions, anomalies, summary


def _overlay_step6_results(
    deterministic_rows: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
    step6_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hybrid_rows = [dict(row) for row in deterministic_rows]
    by_key = {
        _key_for_prediction(str(row["document_id"]), row): row
        for row in hybrid_rows
    }
    def _step6_entry_id_for_anomaly(row: dict[str, Any]) -> str:
        target_reference = str(row.get("target_reference") or "").strip()
        context_block = str(row.get("context_block") or "").strip()
        payload = f"{target_reference}\n{context_block}".encode("utf-8")
        return hashlib.sha1(payload).hexdigest()

    anomaly_by_entry_id = {}
    for row in anomaly_rows:
        if not isinstance(row, dict):
            continue
        entry_id = _step6_entry_id_for_anomaly(row)
        anomaly_by_entry_id[entry_id] = row

    for step6_row in step6_rows:
        entry_id = str(step6_row.get("entry_id") or "")
        anomaly_row = anomaly_by_entry_id.get(entry_id)
        if anomaly_row is None:
            continue

        document_id = str(anomaly_row.get("document_id") or "")
        raw_start = anomaly_row.get("raw_start")
        citation_type = str(anomaly_row.get("citation_type") or "")
        if not document_id or raw_start is None or not citation_type:
            continue
        key = (document_id, int(raw_start), citation_type)
        pred = by_key.get(key)
        if pred is None:
            continue

        result = step6_row.get("result") or {}
        classification = str(result.get("classification") or "czech_unresolved")
        pred["predicted_classification"] = classification
        pred["resolved_law_id"] = result.get("resolved_law_id")
        pred["confidence"] = float(result.get("confidence") or 0.0)
        pred["resolver_stage"] = "Step 6: LLM Fallback"
        pred["llm_result"] = result

    return hybrid_rows


def _evaluate_routed_subset(
    gold_docs: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
    deterministic_rows: list[dict[str, Any]],
    hybrid_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    gold_by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    for gold_doc in gold_docs:
        document_id = str(gold_doc["document_id"])
        citations = gold_doc.get("citations", [])
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            gold_by_key[_key_for_gold(document_id, citation)] = citation

    routed_keys = []
    route_reason_counts = Counter()
    for row in anomaly_rows:
        key = (
            str(row.get("document_id") or ""),
            int(row.get("raw_start") or 0),
            str(row.get("citation_type") or ""),
        )
        if key in gold_by_key:
            routed_keys.append(key)
            route_reason_counts[str(row.get("route_reason") or "unknown")] += 1

    det_by_key = {
        _key_for_prediction(str(row["document_id"]), row): row
        for row in deterministic_rows
    }
    hybrid_by_key = {
        _key_for_prediction(str(row["document_id"]), row): row
        for row in hybrid_rows
    }

    def score(pred_by_key: dict[tuple[str, int, str], dict[str, Any]]) -> dict[str, Any]:
        class_correct = 0
        law_total = 0
        law_correct = 0
        fully_correct = 0

        for key in routed_keys:
            gold = gold_by_key[key]
            pred = pred_by_key.get(key)
            pred_label = _prediction_label(pred or {})
            gold_label = str(gold.get("classification") or "")
            class_ok = pred_label == gold_label
            if class_ok:
                class_correct += 1

            law_ok = True
            if gold_label == "czech_resolved":
                law_total += 1
                law_ok = bool(pred) and str(pred.get("resolved_law_id") or "") == str(gold.get("law_id") or "")
                if law_ok:
                    law_correct += 1

            if class_ok and law_ok:
                fully_correct += 1

        return {
            "rows": len(routed_keys),
            "classification_accuracy": _safe_div(class_correct, len(routed_keys)),
            "classification_correct": class_correct,
            "czech_law_accuracy": _safe_div(law_correct, law_total),
            "czech_law_correct": law_correct,
            "czech_law_total": law_total,
            "full_accuracy": _safe_div(fully_correct, len(routed_keys)),
            "full_correct": fully_correct,
        }

    deterministic = score(det_by_key)
    hybrid = score(hybrid_by_key)
    return {
        "rows": len(routed_keys),
        "route_reasons": dict(sorted(route_reason_counts.items())),
        "deterministic": deterministic,
        "hybrid": hybrid,
        "delta": {
            "classification_accuracy": hybrid["classification_accuracy"] - deterministic["classification_accuracy"],
            "czech_law_accuracy": hybrid["czech_law_accuracy"] - deterministic["czech_law_accuracy"],
            "full_accuracy": hybrid["full_accuracy"] - deterministic["full_accuracy"],
        },
    }


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _run_eval(gold_input: str, predicted: Path, json_output: Path, md_output: Path) -> None:
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "11_evaluate_against_gold.py"),
            "--gold-input",
            gold_input,
            "--predicted-occurrences",
            str(predicted),
            "--output-json",
            str(json_output),
            "--output-md",
            str(md_output),
        ]
    )


def _run_step6(args: argparse.Namespace, queue_path: Path, output_path: Path) -> None:
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "06_llm_resolve_references.py"),
            "--input",
            str(queue_path),
            "--canonical",
            args.canonical,
            "--audited-aliases",
            args.step6_audited_aliases,
            "--global-aliases",
            args.step6_global_aliases,
            "--seed-aliases",
            args.step6_seed_aliases,
            "--output",
            str(output_path),
            "--model",
            args.model,
            "--overwrite",
        ]
    )


def _build_compare_report(
    *,
    gold_input: str,
    deterministic_eval: dict[str, Any],
    hybrid_eval: dict[str, Any],
    routed_subset: dict[str, Any],
    deterministic_predicted: Path,
    hybrid_predicted: Path,
    anomaly_queue: Path,
    step6_results: Path | None,
) -> dict[str, Any]:
    det_metrics = deterministic_eval["metrics"]
    hyb_metrics = hybrid_eval["metrics"]
    return {
        "gold_input": gold_input,
        "deterministic_predicted": str(deterministic_predicted),
        "hybrid_predicted": str(hybrid_predicted),
        "anomaly_queue": str(anomaly_queue),
        "step6_results": str(step6_results) if step6_results is not None else None,
        "whole_document": {
            "deterministic": deterministic_eval,
            "hybrid": hybrid_eval,
            "delta": {
                "supported_anchor_f1": (hyb_metrics["supported_anchor_detection"]["f1"] or 0.0)
                - (det_metrics["supported_anchor_detection"]["f1"] or 0.0),
                "supported_positive_f1": (hyb_metrics["supported_positive_citation_presence"]["f1"] or 0.0)
                - (det_metrics["supported_positive_citation_presence"]["f1"] or 0.0),
                "classification_accuracy": (hyb_metrics["matched_supported_classification_accuracy"]["accuracy"] or 0.0)
                - (det_metrics["matched_supported_classification_accuracy"]["accuracy"] or 0.0),
                "czech_law_accuracy": (hyb_metrics["czech_resolved_law_accuracy"]["accuracy"] or 0.0)
                - (det_metrics["czech_resolved_law_accuracy"]["accuracy"] or 0.0),
            },
        },
        "routed_subset": routed_subset,
    }


def _comparison_markdown(report: dict[str, Any]) -> str:
    whole_det = report["whole_document"]["deterministic"]["metrics"]
    whole_hyb = report["whole_document"]["hybrid"]["metrics"]
    routed = report["routed_subset"]
    lines = [
        "# Law Reference LLM Comparison",
        "",
        f"- gold input: `{report['gold_input']}`",
        f"- deterministic predictions: `{report['deterministic_predicted']}`",
        f"- hybrid predictions: `{report['hybrid_predicted']}`",
        f"- routed anomaly queue: `{report['anomaly_queue']}`",
    ]
    if report.get("step6_results"):
        lines.append(f"- Step 6 results: `{report['step6_results']}`")
    lines.extend(
        [
            "",
            "## Whole Documents",
            "",
            f"- deterministic positive F1: `{whole_det['supported_positive_citation_presence']['f1']}`",
            f"- hybrid positive F1: `{whole_hyb['supported_positive_citation_presence']['f1']}`",
            f"- positive-F1 delta: `{report['whole_document']['delta']['supported_positive_f1']:+.4f}`",
            f"- deterministic classification accuracy: `{whole_det['matched_supported_classification_accuracy']['accuracy']}`",
            f"- hybrid classification accuracy: `{whole_hyb['matched_supported_classification_accuracy']['accuracy']}`",
            f"- classification-accuracy delta: `{report['whole_document']['delta']['classification_accuracy']:+.4f}`",
            f"- deterministic Czech law accuracy: `{whole_det['czech_resolved_law_accuracy']['accuracy']}`",
            f"- hybrid Czech law accuracy: `{whole_hyb['czech_resolved_law_accuracy']['accuracy']}`",
            f"- Czech-law-accuracy delta: `{report['whole_document']['delta']['czech_law_accuracy']:+.4f}`",
            "",
            "## Routed Subset",
            "",
            f"- routed rows with gold match: `{routed['rows']}`",
            f"- route reasons: `{routed['route_reasons']}`",
            f"- deterministic classification accuracy: `{routed['deterministic']['classification_accuracy']:.4f}`",
            f"- hybrid classification accuracy: `{routed['hybrid']['classification_accuracy']:.4f}`",
            f"- routed classification delta: `{routed['delta']['classification_accuracy']:+.4f}`",
            f"- deterministic Czech law accuracy: `{routed['deterministic']['czech_law_accuracy']:.4f}`",
            f"- hybrid Czech law accuracy: `{routed['hybrid']['czech_law_accuracy']:.4f}`",
            f"- routed Czech-law delta: `{routed['delta']['czech_law_accuracy']:+.4f}`",
            f"- deterministic full accuracy: `{routed['deterministic']['full_accuracy']:.4f}`",
            f"- hybrid full accuracy: `{routed['hybrid']['full_accuracy']:.4f}`",
            f"- routed full delta: `{routed['delta']['full_accuracy']:+.4f}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    gold_docs = _load_gold_documents(args.gold_input)
    if not gold_docs:
        raise SystemExit(f"No gold documents found in {args.gold_input}")

    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    deterministic_predicted = output_dir / f"law_llm_{args.tag}.deterministic_predictions.jsonl"
    anomaly_queue = output_dir / f"law_llm_{args.tag}.anomalies.json"
    deterministic_eval_json = output_dir / f"law_llm_{args.tag}.deterministic_eval.json"
    deterministic_eval_md = output_dir / f"law_llm_{args.tag}.deterministic_eval.md"

    deterministic_rows, anomaly_rows, summary = _build_deterministic_predictions_and_queue(gold_docs, args)
    _write_jsonl(deterministic_predicted, deterministic_rows)
    _write_json(anomaly_queue, anomaly_rows)
    _write_json(output_dir / f"law_llm_{args.tag}.deterministic_summary.json", summary)

    if args.mode == "deterministic":
        _run_eval(args.gold_input, deterministic_predicted, deterministic_eval_json, deterministic_eval_md)
        print("--- LAW LLM MODE COMPLETE ---")
        print("Mode:                   deterministic")
        print(f"Predicted output:       {deterministic_predicted}")
        print(f"Anomaly queue:          {anomaly_queue}")
        print(f"Evaluation:             {deterministic_eval_md}")
        return

    step6_results = Path(args.llm_predicted) if args.llm_predicted else output_dir / f"law_llm_{args.tag}.step6_results.jsonl"
    if not args.llm_predicted:
        _run_step6(args, anomaly_queue, step6_results)

    hybrid_predicted = output_dir / f"law_llm_{args.tag}.hybrid_predictions.jsonl"
    hybrid_rows = _overlay_step6_results(deterministic_rows, anomaly_rows, _load_jsonl(str(step6_results)))
    _write_jsonl(hybrid_predicted, hybrid_rows)

    hybrid_eval_json = output_dir / f"law_llm_{args.tag}.hybrid_eval.json"
    hybrid_eval_md = output_dir / f"law_llm_{args.tag}.hybrid_eval.md"
    _run_eval(args.gold_input, hybrid_predicted, hybrid_eval_json, hybrid_eval_md)

    if args.mode == "llm":
        routed_subset = _evaluate_routed_subset(gold_docs, anomaly_rows, deterministic_rows, hybrid_rows)
        _write_json(output_dir / f"law_llm_{args.tag}.routed_subset.json", routed_subset)
        print("--- LAW LLM MODE COMPLETE ---")
        print("Mode:                   llm")
        print(f"Predicted output:       {hybrid_predicted}")
        print(f"Step 6 results:         {step6_results}")
        print(f"Evaluation:             {hybrid_eval_md}")
        return

    _run_eval(args.gold_input, deterministic_predicted, deterministic_eval_json, deterministic_eval_md)
    deterministic_eval = _load_json(str(deterministic_eval_json))
    hybrid_eval = _load_json(str(hybrid_eval_json))
    routed_subset = _evaluate_routed_subset(gold_docs, anomaly_rows, deterministic_rows, hybrid_rows)
    compare_report = _build_compare_report(
        gold_input=args.gold_input,
        deterministic_eval=deterministic_eval,
        hybrid_eval=hybrid_eval,
        routed_subset=routed_subset,
        deterministic_predicted=deterministic_predicted,
        hybrid_predicted=hybrid_predicted,
        anomaly_queue=anomaly_queue,
        step6_results=step6_results,
    )
    compare_json = output_dir / f"law_llm_{args.tag}.comparison.json"
    compare_md = output_dir / f"law_llm_{args.tag}.comparison.md"
    _write_json(compare_json, compare_report)
    compare_md.write_text(_comparison_markdown(compare_report), encoding="utf-8")

    print("--- LAW LLM MODE COMPLETE ---")
    print("Mode:                   compare")
    print(f"Deterministic eval:     {deterministic_eval_md}")
    print(f"Hybrid eval:            {hybrid_eval_md}")
    print(f"Comparison report:      {compare_md}")


if __name__ == "__main__":
    raise SystemExit(main())
