#!/usr/bin/env python3
"""
Run full-dataset document-reference deterministic vs hybrid comparison.

Hybrid mode overlays current route-aware LLM results onto the same 25-document
joint benchmark by:
- keeping deterministic extracted references as the base prediction set
- sending only unresolved link hard-cases with candidate targets to the LLM
- sending mined non-overlapping gap candidates to extraction_presence_check
- overlaying accepted LLM outcomes back onto extraction and link predictions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from demo_app.services.docref_llm import enrich_document_reference_tasks
from src.document_reference_extractor import extract_document_references
from src.document_reference_linker import (
    DocumentSelfIdentifier,
    build_document_self_id_index,
    build_fuzzy_identifier_buckets,
    build_reference_candidate_targets,
    iter_corpus_documents,
    link_document_reference,
)


_PREFIX_BODY = r"(?:sp\.?\s*zn\.?|sen\.?\s*zn\.?|č\.?\s*j\.?|čj\.?)"
_PREFIX_PATTERN = re.compile(
    rf"(?<!\w)(?P<prefix>{_PREFIX_BODY})\s*:?\s*(?P<body>[^\n\]\);]{{4,140}})",
    re.IGNORECASE,
)
_BODY_CUTOFF_PATTERN = re.compile(
    r"("
    r"(?:,?\s+ze\s+dne\b)|"
    r"(?:,?\s+(?:a|či|nebo)\s+ze\s+dne\b)|"
    r"(?:,?\s+kter(?:ý|á|é|ou|ým|ého|ém|ých)\b)|"
    r"(?:,?\s+vydan(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+veden(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+ve\s+věci\b)|"
    rf"(?:\s+(?:a|či|nebo)\s+{_PREFIX_BODY})|"
    r"(?:[;\)\]\n])"
    r")",
    re.IGNORECASE,
)
_BARE_US_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>("
    r"(?:Pl\.?\s*ÚS(?:-st\.)?|\b[IVXLCDM]+\.\s*ÚS|\bÚS)\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?"
    r"))",
    re.IGNORECASE,
)
_BARE_COURT_CASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full-dataset document-reference deterministic vs hybrid comparison."
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "llm", "compare"],
        required=True,
        help="Which mode to run.",
    )
    parser.add_argument(
        "--gold-docref",
        default="data/annotations/joint_reference_gold_v1/document_reference_gold/joint_document_reference_gold_v1.jsonl",
        help="Aggregate joint document-reference extraction gold.",
    )
    parser.add_argument(
        "--gold-exact-link",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_gold_v1/joint_document_reference_exact_link_gold_v1.jsonl",
        help="Joint exact-link gold JSONL.",
    )
    parser.add_argument(
        "--gold-same-proceeding",
        default="data/annotations/joint_reference_gold_v1/document_reference_link_gold_v1/joint_document_reference_same_proceeding_gold_v1.jsonl",
        help="Joint same-proceeding gold JSONL.",
    )
    parser.add_argument(
        "--processed-root",
        default="data/processed",
        help="Processed corpus root used for candidate linking.",
    )
    parser.add_argument(
        "--external-self-id-metadata",
        default="data/metadata/document_metadata.jsonl.gz",
        help="External self-identifier metadata dump.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/annotations/joint_reference_gold_v1/document_reference_llm_eval",
        help="Directory for predictions, tasks, and reports.",
    )
    parser.add_argument(
        "--tag",
        default="current",
        help="Tag for output files.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model name.",
    )
    parser.add_argument(
        "--prompt-version",
        choices=["v1", "v2"],
        default="v2",
        help="Prompt version for the route-aware LLM.",
    )
    parser.add_argument("--timeout-ms", type=int, default=10000)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--max-gap-candidates-per-document",
        type=int,
        default=25,
        help="Maximum second-pass extraction gap candidates per document.",
    )
    parser.add_argument(
        "--disable-gap-tasks",
        action="store_true",
        help="Do not enqueue extraction-gap presence-check tasks; only run LLM on unresolved linking tasks.",
    )
    parser.add_argument(
        "--llm-predicted",
        default=None,
        help="Existing JSONL of full-dataset docref LLM task results to reuse.",
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
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text: str, start: int, end: int, radius: int = 180) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return _normalize_spaces(text[lo:hi])


def _cut_prefixed_body(body: str) -> str:
    match = _BODY_CUTOFF_PATTERN.search(body)
    if match:
        body = body[: match.start()]
    return body.rstrip(" \t\r\n,.:;")


def _find_gap_candidates(text: str) -> list[tuple[int, int, str, str]]:
    candidates: list[tuple[int, int, str, str]] = []

    for match in _PREFIX_PATTERN.finditer(text):
        prefix = match.group("prefix")
        body = _normalize_spaces(_cut_prefixed_body(match.group("body")))
        if len(body) < 5 or not re.search(r"\d", body):
            continue
        full_text = _normalize_spaces(f"{prefix} {body}")
        candidates.append((match.start(), match.start() + len(match.group(0)), full_text, "prefixed_anchor"))

    for match in _BARE_US_PATTERN.finditer(text):
        body = _normalize_spaces(match.group("body"))
        candidates.append((match.start("body"), match.end("body"), body, "ustavni_soud"))

    for match in _BARE_COURT_CASE_PATTERN.finditer(text):
        body = _normalize_spaces(match.group("body"))
        candidates.append((match.start("body"), match.end("body"), body, "bare_court_case"))

    dedup: dict[tuple[int, int, str], tuple[int, int, str, str]] = {}
    for start, end, candidate_text, category in candidates:
        dedup[(start, end, candidate_text)] = (start, end, candidate_text, category)
    return sorted(dedup.values(), key=lambda item: (item[0], item[1], item[2]))


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for other_start, other_end in spans:
        if start < other_end and other_start < end:
            return True
    return False


def _load_joint_documents(docref_gold_path: str) -> list[dict[str, Any]]:
    rows = _load_jsonl(docref_gold_path)
    docs: dict[str, dict[str, Any]] = {}
    for row in rows:
        doc_id = str(row["document_id"])
        docs.setdefault(
            doc_id,
            {
                "document_id": doc_id,
                "document_path": str(row["document_path"]),
                "source": str(row.get("source") or ""),
            },
        )
    return [docs[key] for key in sorted(docs)]


def _extraction_row(document: dict[str, Any], ref) -> dict[str, Any]:
    return {
        "document_id": document["document_id"],
        "document_path": document["document_path"],
        "source": document["source"],
        "reference_text": ref.reference_text,
        "reference_prefix": ref.reference_prefix,
        "reference_body": ref.reference_body,
        "reference_type": ref.reference_type,
        "raw_start": ref.raw_start,
        "raw_end": ref.raw_end,
        "decision_kind_hint": ref.decision_kind_hint,
        "court_hint": ref.court_hint,
    }


def _link_task_from_reference(
    *,
    document: dict[str, Any],
    reference_index: int,
    reference,
    candidate_info: dict[str, Any],
) -> dict[str, Any]:
    return {
        "entry_id": f"joint-docref::{document['document_id']}::link::{reference_index}",
        "reference_index": reference_index,
        "llm_route": candidate_info["llm_route"],
        "source": document["source"],
        "document_id": document["document_id"],
        "document_path": document["document_path"],
        "target_reference": reference.reference_text,
        "reference_body": reference.reference_body,
        "reference_type_hint": reference.reference_type,
        "raw_start": reference.raw_start,
        "raw_end": reference.raw_end,
        "context_block": reference.context,
        "candidate_targets": list(candidate_info["candidate_targets"]),
    }


def _gap_task(
    *,
    document: dict[str, Any],
    raw_start: int,
    raw_end: int,
    candidate_text: str,
    category: str,
    context_block: str,
) -> dict[str, Any]:
    return {
        "entry_id": f"joint-docref::{document['document_id']}::gap::{raw_start}",
        "llm_route": "extraction_presence_check",
        "entry_type": "extraction_gap",
        "source": document["source"],
        "document_id": document["document_id"],
        "document_path": document["document_path"],
        "candidate_text": candidate_text,
        "candidate_category": category,
        "target_reference": candidate_text,
        "reference_body": candidate_text,
        "raw_start": raw_start,
        "raw_end": raw_end,
        "context_block": context_block,
        "candidate_targets": [],
    }


def _resolve_candidate_target(task: dict[str, Any], target_document_id: str) -> dict[str, Any] | None:
    for candidate in task.get("candidate_targets", []):
        if str(candidate.get("target_document_id") or "") == target_document_id:
            return candidate
    return None


def _linked_row_from_task(task: dict[str, Any], decision: str, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_document_source": task["source"],
        "source_document_id": task["document_id"],
        "source_document_path": task["document_path"],
        "target_source": target.get("target_source"),
        "target_document_id": target.get("target_document_id"),
        "target_document_path": target.get("target_document_path"),
        "reference_text": task["target_reference"],
        "reference_prefix": "",
        "reference_body": task.get("reference_body") or "",
        "reference_type": task.get("reference_type_hint") or "unknown",
        "raw_start": int(task["raw_start"]),
        "raw_end": int(task["raw_end"]),
        "link_key": "",
        "link_method": "llm_fallback",
        "target_match_scope": "same_proceeding" if decision == "same_proceeding" else "exact_decision",
        "target_proceeding_key": None,
        "target_group_size": None,
        "decision_kind_hint": None,
        "court_hint": None,
    }


def _build_deterministic_state(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    documents = _load_joint_documents(args.gold_docref)
    all_document_paths = iter_corpus_documents(args.processed_root)
    self_id_index, all_identifiers = build_document_self_id_index(
        all_document_paths,
        external_metadata_path=args.external_self_id_metadata,
        processed_root=args.processed_root,
    )
    fuzzy_buckets = build_fuzzy_identifier_buckets(all_identifiers)

    extraction_rows: list[dict[str, Any]] = []
    linked_rows: list[dict[str, Any]] = []
    llm_tasks: list[dict[str, Any]] = []
    task_counts = Counter()

    for document in documents:
        text = Path(document["document_path"]).read_text(encoding="utf-8")
        refs = extract_document_references(text)
        extracted_spans = []
        for idx, ref in enumerate(refs):
            extraction_rows.append(_extraction_row(document, ref))
            extracted_spans.append((ref.raw_start, ref.raw_end))

            linked = link_document_reference(
                source_document_path=document["document_path"],
                source_document_source=document["source"],
                source_document_id=document["document_id"],
                reference=ref,
                self_id_index=self_id_index,
            )
            if linked is not None:
                linked_rows.append(linked.to_dict())
                continue

            candidate_info = build_reference_candidate_targets(
                reference=ref,
                source_document_path=document["document_path"],
                source_document_source=document["source"],
                self_id_index=self_id_index,
                all_identifiers=all_identifiers,
                fuzzy_buckets=fuzzy_buckets,
            )
            if candidate_info.get("llm_route") == "filtered_out_of_scope":
                continue
            if candidate_info.get("candidate_targets"):
                llm_tasks.append(
                    _link_task_from_reference(
                        document=document,
                        reference_index=idx,
                        reference=ref,
                        candidate_info=candidate_info,
                    )
                )
                task_counts[str(candidate_info["llm_route"])] += 1

        if not args.disable_gap_tasks:
            gap_added = 0
            for raw_start, raw_end, candidate_text, category in _find_gap_candidates(text):
                if gap_added >= args.max_gap_candidates_per_document:
                    break
                if _overlaps(raw_start, raw_end, extracted_spans):
                    continue
                llm_tasks.append(
                    _gap_task(
                        document=document,
                        raw_start=raw_start,
                        raw_end=raw_end,
                        candidate_text=candidate_text,
                        category=category,
                        context_block=_snippet(text, raw_start, raw_end),
                    )
                )
                gap_added += 1
                task_counts["extraction_presence_check"] += 1

    summary = {
        "documents": len(documents),
        "deterministic_extraction_rows": len(extraction_rows),
        "deterministic_link_rows": len(linked_rows),
        "llm_tasks": len(llm_tasks),
        "llm_tasks_by_route": dict(sorted(task_counts.items())),
    }
    return extraction_rows, linked_rows, llm_tasks, summary


def _overlay_hybrid(
    deterministic_extraction_rows: list[dict[str, Any]],
    deterministic_linked_rows: list[dict[str, Any]],
    llm_tasks: list[dict[str, Any]],
    llm_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    extraction_rows = [dict(row) for row in deterministic_extraction_rows]
    linked_rows = [dict(row) for row in deterministic_linked_rows]
    extraction_keys = {
        (str(row["document_id"]), int(row["raw_start"]), int(row["raw_end"])): row
        for row in extraction_rows
    }
    existing_link_keys = {
        (str(row["source_document_id"]), int(row["raw_start"]), str(row["target_document_id"]))
        for row in linked_rows
    }
    task_by_entry_id = {
        str(task["entry_id"]): task
        for task in llm_tasks
        if task.get("entry_id")
    }
    promoted_gap_count = 0
    promoted_link_count = 0
    updated_existing_ref_count = 0

    for llm_row in llm_rows:
        task = task_by_entry_id.get(str(llm_row.get("entry_id") or ""))
        if task is None:
            continue
        result = llm_row.get("result") if isinstance(llm_row.get("result"), dict) else {}
        decision = str(result.get("decision") or "")

        if task.get("llm_route") == "extraction_presence_check":
            if decision != "is_reference":
                continue
            key = (str(task["document_id"]), int(task["raw_start"]), int(task["raw_end"]))
            existing = extraction_keys.get(key)
            if existing is not None:
                existing["reference_type"] = result.get("reference_type") or existing.get("reference_type")
                normalized_body = str(result.get("normalized_body") or "").strip()
                if normalized_body:
                    existing["reference_body"] = normalized_body
                updated_existing_ref_count += 1
                continue

            normalized_body = str(result.get("normalized_body") or task.get("candidate_text") or "").strip()
            new_row = {
                "document_id": task["document_id"],
                "document_path": task["document_path"],
                "source": task["source"],
                "reference_text": task.get("candidate_text") or task.get("target_reference") or "",
                "reference_prefix": "",
                "reference_body": normalized_body,
                "reference_type": result.get("reference_type") or "unknown",
                "raw_start": int(task["raw_start"]),
                "raw_end": int(task["raw_end"]),
                "decision_kind_hint": None,
                "court_hint": None,
            }
            extraction_rows.append(new_row)
            extraction_keys[key] = new_row
            promoted_gap_count += 1
            continue

        if decision not in {"exact_target", "same_proceeding"}:
            continue
        target_document_id = str(result.get("target_document_id") or "").strip()
        if not target_document_id:
            continue
        target = _resolve_candidate_target(task, target_document_id)
        if target is None:
            continue
        link_key = (str(task["document_id"]), int(task["raw_start"]), target_document_id)
        if link_key in existing_link_keys:
            continue
        linked_rows.append(_linked_row_from_task(task, decision, target))
        existing_link_keys.add(link_key)
        promoted_link_count += 1

    summary = {
        "promoted_gap_count": promoted_gap_count,
        "updated_existing_ref_count": updated_existing_ref_count,
        "promoted_link_count": promoted_link_count,
        "hybrid_extraction_rows": len(extraction_rows),
        "hybrid_link_rows": len(linked_rows),
    }
    return extraction_rows, linked_rows, summary


def _run(cmd: list[str]) -> None:
    import subprocess

    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _evaluate_extraction(gold: str, predicted: Path, json_output: Path, md_output: Path) -> None:
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "17_evaluate_document_references.py"),
            "--gold",
            gold,
            "--predicted-occurrences",
            str(predicted),
            "--output-json",
            str(json_output),
            "--output-md",
            str(md_output),
        ]
    )


def _evaluate_links(gold: str, predicted: Path, json_output: Path, md_output: Path) -> None:
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "21_evaluate_document_reference_links.py"),
            "--predicted",
            str(predicted),
            "--gold",
            gold,
            "--json-output",
            str(json_output),
            "--md-output",
            str(md_output),
        ]
    )


def _comparison_report(
    *,
    deterministic_extraction_eval: dict[str, Any],
    hybrid_extraction_eval: dict[str, Any],
    deterministic_exact_link_eval: dict[str, Any],
    hybrid_exact_link_eval: dict[str, Any],
    deterministic_same_eval: dict[str, Any],
    hybrid_same_eval: dict[str, Any],
    llm_summary: dict[str, Any],
    paths: dict[str, str],
) -> dict[str, Any]:
    return {
        "paths": paths,
        "llm_summary": llm_summary,
        "extraction": {
            "deterministic": deterministic_extraction_eval,
            "hybrid": hybrid_extraction_eval,
            "delta": {
                "exact_span_f1": hybrid_extraction_eval["exact_span_f1"] - deterministic_extraction_eval["exact_span_f1"],
                "reference_body_accuracy": hybrid_extraction_eval["reference_body_accuracy_on_exact_matches"]
                - deterministic_extraction_eval["reference_body_accuracy_on_exact_matches"],
            },
        },
        "exact_link": {
            "deterministic": deterministic_exact_link_eval,
            "hybrid": hybrid_exact_link_eval,
            "delta": {
                "scoped_f1": hybrid_exact_link_eval["scoped_f1"] - deterministic_exact_link_eval["scoped_f1"],
            },
        },
        "same_proceeding": {
            "deterministic": deterministic_same_eval,
            "hybrid": hybrid_same_eval,
            "delta": {
                "scoped_f1": hybrid_same_eval["scoped_f1"] - deterministic_same_eval["scoped_f1"],
            },
        },
    }


def _comparison_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full-Dataset Document Reference LLM Comparison",
        "",
        f"- deterministic extraction predictions: `{report['paths']['deterministic_extraction_predictions']}`",
        f"- deterministic links: `{report['paths']['deterministic_link_predictions']}`",
        f"- llm task queue: `{report['paths']['llm_tasks']}`",
        f"- llm results: `{report['paths']['llm_results']}`",
        f"- hybrid extraction predictions: `{report['paths']['hybrid_extraction_predictions']}`",
        f"- hybrid links: `{report['paths']['hybrid_link_predictions']}`",
        "",
        "## LLM Overlay Summary",
        "",
    ]
    for key, value in sorted(report["llm_summary"].items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Extraction",
            "",
            f"- deterministic exact span F1: `{report['extraction']['deterministic']['exact_span_f1']:.4f}`",
            f"- hybrid exact span F1: `{report['extraction']['hybrid']['exact_span_f1']:.4f}`",
            f"- exact span F1 delta: `{report['extraction']['delta']['exact_span_f1']:+.4f}`",
            "",
            "## Exact Linking",
            "",
            f"- deterministic scoped F1: `{report['exact_link']['deterministic']['scoped_f1']:.4f}`",
            f"- hybrid scoped F1: `{report['exact_link']['hybrid']['scoped_f1']:.4f}`",
            f"- scoped F1 delta: `{report['exact_link']['delta']['scoped_f1']:+.4f}`",
            "",
            "## Same-Proceeding Linking",
            "",
            f"- deterministic scoped F1: `{report['same_proceeding']['deterministic']['scoped_f1']:.4f}`",
            f"- hybrid scoped F1: `{report['same_proceeding']['hybrid']['scoped_f1']:.4f}`",
            f"- scoped F1 delta: `{report['same_proceeding']['delta']['scoped_f1']:+.4f}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    deterministic_extraction_predictions = output_dir / f"document_reference_llm_{args.tag}.deterministic_extraction_predictions.jsonl"
    deterministic_link_predictions = output_dir / f"document_reference_llm_{args.tag}.deterministic_links.jsonl"
    llm_tasks_path = output_dir / f"document_reference_llm_{args.tag}.tasks.jsonl"
    deterministic_summary_path = output_dir / f"document_reference_llm_{args.tag}.deterministic_summary.json"

    det_extract_eval_json = output_dir / f"document_reference_llm_{args.tag}.deterministic_extraction_eval.json"
    det_extract_eval_md = output_dir / f"document_reference_llm_{args.tag}.deterministic_extraction_eval.md"
    det_exact_eval_json = output_dir / f"document_reference_llm_{args.tag}.deterministic_exact_link_eval.json"
    det_exact_eval_md = output_dir / f"document_reference_llm_{args.tag}.deterministic_exact_link_eval.md"
    det_same_eval_json = output_dir / f"document_reference_llm_{args.tag}.deterministic_same_proceeding_eval.json"
    det_same_eval_md = output_dir / f"document_reference_llm_{args.tag}.deterministic_same_proceeding_eval.md"

    deterministic_extraction_rows, deterministic_linked_rows, llm_tasks, deterministic_summary = _build_deterministic_state(args)
    _write_jsonl(deterministic_extraction_predictions, deterministic_extraction_rows)
    _write_jsonl(deterministic_link_predictions, deterministic_linked_rows)
    _write_jsonl(llm_tasks_path, llm_tasks)
    _write_json(deterministic_summary_path, deterministic_summary)

    _evaluate_extraction(args.gold_docref, deterministic_extraction_predictions, det_extract_eval_json, det_extract_eval_md)
    _evaluate_links(args.gold_exact_link, deterministic_link_predictions, det_exact_eval_json, det_exact_eval_md)
    _evaluate_links(args.gold_same_proceeding, deterministic_link_predictions, det_same_eval_json, det_same_eval_md)

    if args.mode == "deterministic":
        print("--- DOCUMENT REFERENCE FULL LLM MODE COMPLETE ---")
        print("Mode:                         deterministic")
        print(f"Deterministic extraction:     {deterministic_extraction_predictions}")
        print(f"Deterministic links:          {deterministic_link_predictions}")
        print(f"LLM task queue:               {llm_tasks_path}")
        return

    llm_results_path = Path(args.llm_predicted) if args.llm_predicted else output_dir / f"document_reference_llm_{args.tag}.llm_results.jsonl"
    if args.llm_predicted is None:
        llm_rows = enrich_document_reference_tasks(
            llm_tasks,
            model=args.model,
            prompt_version=args.prompt_version,
            timeout_ms=args.timeout_ms,
            retries=args.retries,
        )
        _write_jsonl(llm_results_path, llm_rows)
    else:
        llm_rows = _load_jsonl(str(llm_results_path))

    hybrid_extraction_rows, hybrid_link_rows, llm_summary = _overlay_hybrid(
        deterministic_extraction_rows,
        deterministic_linked_rows,
        llm_tasks,
        llm_rows,
    )

    hybrid_extraction_predictions = output_dir / f"document_reference_llm_{args.tag}.hybrid_extraction_predictions.jsonl"
    hybrid_link_predictions = output_dir / f"document_reference_llm_{args.tag}.hybrid_links.jsonl"
    _write_jsonl(hybrid_extraction_predictions, hybrid_extraction_rows)
    _write_jsonl(hybrid_link_predictions, hybrid_link_rows)
    _write_json(output_dir / f"document_reference_llm_{args.tag}.hybrid_summary.json", llm_summary)

    hyb_extract_eval_json = output_dir / f"document_reference_llm_{args.tag}.hybrid_extraction_eval.json"
    hyb_extract_eval_md = output_dir / f"document_reference_llm_{args.tag}.hybrid_extraction_eval.md"
    hyb_exact_eval_json = output_dir / f"document_reference_llm_{args.tag}.hybrid_exact_link_eval.json"
    hyb_exact_eval_md = output_dir / f"document_reference_llm_{args.tag}.hybrid_exact_link_eval.md"
    hyb_same_eval_json = output_dir / f"document_reference_llm_{args.tag}.hybrid_same_proceeding_eval.json"
    hyb_same_eval_md = output_dir / f"document_reference_llm_{args.tag}.hybrid_same_proceeding_eval.md"

    _evaluate_extraction(args.gold_docref, hybrid_extraction_predictions, hyb_extract_eval_json, hyb_extract_eval_md)
    _evaluate_links(args.gold_exact_link, hybrid_link_predictions, hyb_exact_eval_json, hyb_exact_eval_md)
    _evaluate_links(args.gold_same_proceeding, hybrid_link_predictions, hyb_same_eval_json, hyb_same_eval_md)

    if args.mode == "llm":
        print("--- DOCUMENT REFERENCE FULL LLM MODE COMPLETE ---")
        print("Mode:                         llm")
        print(f"LLM results:                  {llm_results_path}")
        print(f"Hybrid extraction:            {hybrid_extraction_predictions}")
        print(f"Hybrid links:                 {hybrid_link_predictions}")
        return

    comparison_json = output_dir / f"document_reference_llm_{args.tag}.comparison.json"
    comparison_md = output_dir / f"document_reference_llm_{args.tag}.comparison.md"
    report = _comparison_report(
        deterministic_extraction_eval=_load_json(str(det_extract_eval_json)),
        hybrid_extraction_eval=_load_json(str(hyb_extract_eval_json)),
        deterministic_exact_link_eval=_load_json(str(det_exact_eval_json)),
        hybrid_exact_link_eval=_load_json(str(hyb_exact_eval_json)),
        deterministic_same_eval=_load_json(str(det_same_eval_json)),
        hybrid_same_eval=_load_json(str(hyb_same_eval_json)),
        llm_summary=llm_summary,
        paths={
            "deterministic_extraction_predictions": str(deterministic_extraction_predictions),
            "deterministic_link_predictions": str(deterministic_link_predictions),
            "llm_tasks": str(llm_tasks_path),
            "llm_results": str(llm_results_path),
            "hybrid_extraction_predictions": str(hybrid_extraction_predictions),
            "hybrid_link_predictions": str(hybrid_link_predictions),
        },
    )
    _write_json(comparison_json, report)
    comparison_md.write_text(_comparison_markdown(report), encoding="utf-8")

    print("--- DOCUMENT REFERENCE FULL LLM MODE COMPLETE ---")
    print("Mode:                         compare")
    print(f"Comparison report:            {comparison_md}")


if __name__ == "__main__":
    raise SystemExit(main())
