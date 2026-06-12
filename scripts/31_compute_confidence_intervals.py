#!/usr/bin/env python3
"""
Compute compact 95% confidence intervals for thesis headline metrics.

The script reads the frozen evaluation artifacts used in the thesis and writes a
small JSON/Markdown summary. Intervals are Wilson score intervals over reviewed
benchmark rows. For F1 metrics, the equivalent F1 ratio
2TP / (2TP + FP + FN) is used.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_JSON = (
    "data/annotations/joint_reference_gold_v1/metric_confidence_intervals.json"
)
DEFAULT_OUTPUT_MD = (
    "data/annotations/joint_reference_gold_v1/metric_confidence_intervals.md"
)

LAW_COMPARISON = (
    "data/annotations/joint_reference_gold_v1/law_llm_eval/"
    "law_llm_fresh.comparison.json"
)
LAW_SPP_EVAL = "data/annotations/joint_reference_gold_v1/law_gold_eval_current.json"
LAW_BRL_EVAL = (
    "data/annotations/joint_reference_gold_v1/law_llm_eval/"
    "law_llm_fresh.hybrid_eval.json"
)
DOC_EXTRACTION = (
    "data/annotations/joint_reference_gold_v1/"
    "document_reference_eval_current.json"
)
DOC_GAP_EXTRACTION = (
    "data/annotations/joint_reference_gold_v1/document_reference_llm_eval/"
    "document_reference_llm_fresh.hybrid_extraction_eval.json"
)
DOC_LINK_SPP = (
    "data/annotations/joint_reference_gold_v1/document_reference_llm_eval/"
    "document_reference_llm_enriched_link_only.deterministic_availability_eval.json"
)
DOC_LINK_BRL = (
    "data/annotations/joint_reference_gold_v1/document_reference_llm_eval/"
    "document_reference_llm_enriched_link_only.hybrid_availability_eval.json"
)
DOC_ROUTE_SPP = (
    "data/annotations/document_reference_links/eval/"
    "document_reference_llm_route_thesis_v1.baseline_eval.json"
)
DOC_ROUTE_BRL = (
    "data/annotations/document_reference_links/eval/"
    "document_reference_llm_route_thesis_v1.llm_eval.json"
)
LAW_WEAK_EVIDENCE = (
    "data/annotations/joint_reference_gold_v1/law_llm_eval/"
    "law_weak_evidence_experiment_summary.json"
)


def _load_json(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError(f"Expected JSON object in {path}")
    return payload


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Cannot compute a confidence interval with total <= 0")
    phat = successes / total
    denom = 1.0 + (z * z / total)
    center = (phat + (z * z / (2.0 * total))) / denom
    half = (
        z
        * math.sqrt((phat * (1.0 - phat) + (z * z / (4.0 * total))) / total)
        / denom
    )
    return center - half, center + half


def _round4(value: float) -> float:
    return round(value + 0.0, 4)


def _f1_ratio_counts(tp: int, fp: int, fn: int) -> tuple[int, int]:
    return 2 * tp, (2 * tp) + fp + fn


def _metric_row(
    metric: str,
    source: str,
    successes: int,
    total: int,
    note: str,
) -> dict[str, Any]:
    low, high = _wilson(successes, total)
    return {
        "metric": metric,
        "source": source,
        "successes": successes,
        "total": total,
        "estimate": _round4(successes / total),
        "ci95_low": _round4(low),
        "ci95_high": _round4(high),
        "note": note,
    }


def _extraction_f1_row(metric: str, source: str, report: dict[str, Any]) -> dict[str, Any]:
    gold = int(report["gold_positive_references"])
    predicted = int(report["predicted_references"])
    recall = float(report["exact_span_recall"])
    tp = round(recall * gold)
    fp = predicted - tp
    fn = gold - tp
    successes, total = _f1_ratio_counts(tp, fp, fn)
    return _metric_row(
        metric=metric,
        source=source,
        successes=successes,
        total=total,
        note=f"F1 as 2TP/(2TP+FP+FN), TP={tp}, FP={fp}, FN={fn}",
    )


def build_rows() -> list[dict[str, Any]]:
    law = _load_json(LAW_COMPARISON)
    law_spp = _load_json(LAW_SPP_EVAL)
    law_brl = _load_json(LAW_BRL_EVAL)
    doc_extraction = _load_json(DOC_EXTRACTION)
    doc_gap_extraction = _load_json(DOC_GAP_EXTRACTION)
    doc_link_spp = _load_json(DOC_LINK_SPP)
    doc_link_brl = _load_json(DOC_LINK_BRL)
    doc_route_spp = _load_json(DOC_ROUTE_SPP)
    doc_route_brl = _load_json(DOC_ROUTE_BRL)
    law_weak_evidence = _load_json(LAW_WEAK_EVIDENCE)

    det_law_metrics = law_spp["metrics"]
    hybrid_law_metrics = law_brl["metrics"]
    routed = law["routed_subset"]

    rows: list[dict[str, Any]] = []

    anchor = det_law_metrics["supported_anchor_detection"]
    successes, total = _f1_ratio_counts(anchor["tp"], anchor["fp"], anchor["fn"])
    rows.append(
        _metric_row(
            "Law anchor detection F1",
            "SPP",
            successes,
            total,
            f"F1 as 2TP/(2TP+FP+FN), TP={anchor['tp']}, FP={anchor['fp']}, FN={anchor['fn']}",
        )
    )

    presence = det_law_metrics["supported_positive_citation_presence"]
    successes, total = _f1_ratio_counts(presence["tp"], presence["fp"], presence["fn"])
    rows.append(
        _metric_row(
            "Law citation-presence F1",
            "SPP",
            successes,
            total,
            f"F1 as 2TP/(2TP+FP+FN), TP={presence['tp']}, FP={presence['fp']}, FN={presence['fn']}",
        )
    )

    for source, metrics in [
        ("SPP", det_law_metrics),
        ("SPP + BRL", hybrid_law_metrics),
    ]:
        law_accuracy = metrics["czech_resolved_law_accuracy"]
        rows.append(
            _metric_row(
                "Czech-law resolution accuracy",
                source,
                int(law_accuracy["correct"]),
                int(law_accuracy["total"]),
                "Correct law identifiers among gold Czech-law references",
            )
        )

    for source, metrics in [
        ("SPP", det_law_metrics),
        ("SPP + BRL", hybrid_law_metrics),
    ]:
        classification = metrics["matched_supported_classification_accuracy"]
        rows.append(
            _metric_row(
                "Law classification accuracy",
                source,
                int(classification["correct"]),
                int(classification["total"]),
                "Correct labels over matched supported law findings",
            )
        )

    rows.append(
        _metric_row(
            "Routed hard-case law classification accuracy",
            "SPP + BRL",
            int(routed["hybrid"]["classification_correct"]),
            int(routed["hybrid"]["rows"]),
            "Correct labels on routed law hard cases",
        )
    )
    rows.append(
        _metric_row(
            "Routed hard-case Czech-law accuracy",
            "SPP + BRL",
            int(routed["hybrid"]["czech_law_correct"]),
            int(routed["hybrid"]["czech_law_total"]),
            "Correct law identifiers on routed Czech-law hard cases",
        )
    )

    for source_key, source_label in [
        ("spp", "SPP weak-evidence slice"),
        ("llm", "BRL verification"),
    ]:
        report = law_weak_evidence[source_key]
        rows.append(
            _metric_row(
                "Weak-evidence law full accuracy",
                source_label,
                round(float(report["full_accuracy"]) * int(report["rows"])),
                int(report["rows"]),
                "Correct classification and, for Czech-law rows, correct law identifier",
            )
        )
        rows.append(
            _metric_row(
                "Weak-evidence Czech-law accuracy",
                source_label,
                round(float(report["czech_law_accuracy"]) * int(report["czech_law_rows"])),
                int(report["czech_law_rows"]),
                "Correct law identifiers on resolved weak-evidence Czech-law rows",
            )
        )

    rows.append(
        _extraction_f1_row(
            "Document-reference exact-span F1",
            "SPP",
            doc_extraction,
        )
    )
    rows.append(
        _extraction_f1_row(
            "Document-reference exact-span F1",
            "BRL gap promotion",
            doc_gap_extraction,
        )
    )

    for source, report in [
        ("SPP", doc_link_spp),
        ("BRL link-only with enriched candidates", doc_link_brl),
    ]:
        counts = report["outcome_counts"]
        availability = report["availability_counts"]
        rows.append(
            _metric_row(
                "Exact-linkable recovery",
                source,
                int(counts["exact_correct"]),
                int(availability["exact_in_corpus"]),
                "Correct exact links among reviewed references with an exact in-corpus target",
            )
        )
        rows.append(
            _metric_row(
                "Unavailable exact target correctly unresolved",
                source,
                int(counts["not_in_corpus_correctly_unresolved"])
                + int(counts["unknown_unresolved"]),
                int(availability["not_in_corpus"]) + int(availability["unknown"]),
                "Reviewed rows outside the exact-link task that received no exact link",
            )
        )

    for source, report in [
        ("SPP", doc_route_spp),
        ("BRL", doc_route_brl),
    ]:
        rows.append(
            _metric_row(
                "Document-reference route accuracy",
                source,
                int(report["decision_correct_count"]),
                int(report["gold_row_count"]),
                "Correct decisions on the reviewed 30-row document-reference route slice",
            )
        )

    return rows


def _format_interval(row: dict[str, Any]) -> str:
    return f"[{row['ci95_low']:.4f}, {row['ci95_high']:.4f}]"


def _write_outputs(rows: list[dict[str, Any]], output_json: str, output_md: str) -> None:
    payload = {
        "method": (
            "Wilson score 95% intervals over reviewed rows; F1 intervals use "
            "the equivalent ratio 2TP/(2TP+FP+FN)."
        ),
        "rows": rows,
    }

    json_path = Path(output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Metric Confidence Intervals",
        "",
        payload["method"],
        "",
        "| Metric | Source | Estimate | 95% CI | Count |",
        "|---|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['metric']} | {row['source']} | {row['estimate']:.4f} | "
            f"{_format_interval(row)} | {row['successes']}/{row['total']} |"
        )
    lines.append("")

    md_path = Path(output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute 95% confidence intervals for thesis headline metrics."
    )
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    rows = build_rows()
    _write_outputs(rows, args.output_json, args.output_md)

    print("--- CONFIDENCE INTERVALS COMPLETE ---")
    print(f"Rows:            {len(rows)}")
    print(f"JSON output:     {args.output_json}")
    print(f"Markdown output: {args.output_md}")


if __name__ == "__main__":
    main()
