#!/usr/bin/env python3
"""Official analysis script: build analysis-ready tables from a production run.

This script is the bridge between the production pipeline and the downstream
analysis layer. It reads final-run law and document-reference outputs and
materializes canonical analysis tables used by the law and citation-graph
summary scripts.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

# Ensure project root is importable when the script is run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.document_metadata import load_document_dates_index
from src.production_paths import (
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_CORPUS_ROOT,
    PRODUCTION_DATE_METADATA_PATH,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Official analysis stage: build canonical law and citation tables "
            "from a completed production run."
        )
    )
    parser.add_argument(
        "--run-root",
        required=True,
        help="Path to a final run root created by scripts/40_run_final_pipeline.py.",
    )
    parser.add_argument(
        "--corpus-root",
        default=PRODUCTION_CORPUS_ROOT,
        help="Canonical production corpus root used to recover source/path metadata.",
    )
    parser.add_argument(
        "--metadata-path",
        default=PRODUCTION_DATE_METADATA_PATH,
        help="Canonical document metadata JSONL(.gz) used to enrich analysis tables.",
    )
    parser.add_argument(
        "--canonical-laws",
        default=PRODUCTION_CANONICAL_LAWS_PATH,
        help="Canonical laws JSON mapping law_id to names.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <run-root>/analysis.",
    )
    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Also emit CSV alongside JSONL. Disabled by default because some tables can become large.",
    )
    return parser.parse_args()


def _jsonl_iter(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield json.loads(line)


def _load_canonical_law_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    if isinstance(raw, dict):
        for law_id, value in raw.items():
            if isinstance(value, list) and value:
                first = next((item for item in value if isinstance(item, str) and item.strip()), None)
                if first:
                    names[law_id] = first
            elif isinstance(value, str) and value.strip():
                names[law_id] = value
    return names


def _build_document_lookup(corpus_root: Path) -> dict[str, tuple[str, str]]:
    lookup: dict[str, tuple[str, str]] = {}
    for source in ("nalus", "ns", "nss", "uohs"):
        source_dir = corpus_root / source
        if not source_dir.is_dir():
            continue
        for path in source_dir.glob("*.txt"):
            rel = str(path.resolve().relative_to(Path.cwd().resolve()))
            lookup[path.name] = (source, rel)
    return lookup


def _open_maybe_csv(path: Path | None, fieldnames: list[str]):
    if path is None:
        return None, None
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    return handle, writer


def _normalize_csv_row(row: dict[str, Any], fieldnames: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in fieldnames:
        value = row.get(key)
        if isinstance(value, (list, dict)):
            out[key] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            out[key] = ""
        else:
            out[key] = value
    return out


def _write_jsonl_row(handle, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _quality_reason_from_legacy_row(row: dict[str, Any]) -> str | None:
    classification = str(row.get("predicted_classification") or "")
    stage = str(row.get("resolver_stage") or "")
    try:
        confidence = float(row.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if classification == "czech_unresolved":
        return "unresolved"
    if classification == "foreign_law":
        return "foreign_law"
    if confidence >= 0.75:
        return None
    if "Ambiguous" in stage:
        return "ambiguous_alias"
    if "Short Structural" in stage:
        return "short_structural_reference"
    if "Carryover" in stage or "Implicit Generic" in stage:
        return "weak_contextual_evidence"
    return "low_confidence"


def _resolve_doc_identity(
    row: dict[str, Any],
    *,
    id_key: str,
    source_key: str,
    path_key: str,
    lookup: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None]:
    source = row.get(source_key)
    path = row.get(path_key)
    if isinstance(source, str) and isinstance(path, str):
        return source, path
    document_id = row.get(id_key)
    if isinstance(document_id, str):
        return lookup.get(document_id, (source if isinstance(source, str) else None, path if isinstance(path, str) else None))
    return source if isinstance(source, str) else None, path if isinstance(path, str) else None


LAW_FIELDS = [
    "document_id",
    "document_path",
    "source",
    "decision_date_iso",
    "decision_year",
    "decision_date_precision",
    "citation_text",
    "citation_type",
    "raw_start",
    "raw_end",
    "normalized_start",
    "normalized_end",
    "classification",
    "resolved_law_id",
    "resolved_law_name",
    "detail_number",
    "detail_odst",
    "detail_pism",
    "detail_bod",
    "resolver_stage",
    "confidence",
    "quality_flag",
    "quality_reason",
    "context",
    "candidate_law_ids",
    "is_resolved",
]


DOC_OCC_FIELDS = [
    "document_id",
    "document_path",
    "source",
    "decision_date_iso",
    "decision_year",
    "decision_date_precision",
    "reference_text",
    "reference_prefix",
    "reference_body",
    "reference_type",
    "raw_start",
    "raw_end",
    "decision_kind_hint",
    "court_hint",
    "context",
]


EDGE_FIELDS = [
    "source_document_id",
    "source_document_path",
    "source",
    "source_decision_date_iso",
    "source_decision_year",
    "target_document_id",
    "target_document_path",
    "target",
    "target_decision_date_iso",
    "target_decision_year",
    "link_scope",
    "reference_text",
    "reference_prefix",
    "reference_body",
    "reference_type",
    "raw_start",
    "raw_end",
    "link_method",
    "link_key",
    "target_proceeding_key",
    "target_group_size",
    "decision_kind_hint",
    "court_hint",
    "is_same_proceeding",
]


NODE_FIELDS = [
    "document_id",
    "document_path",
    "source",
    "decision_date_iso",
    "decision_year",
    "decision_date_precision",
    "display_identifier",
    "identifier_kind",
    "identifier_origin",
    "source_iri",
    "in_degree_exact",
    "in_degree_same_proceeding",
    "out_degree_exact",
    "out_degree_same_proceeding",
    "has_exact_incoming",
    "has_same_proceeding_incoming",
    "has_exact_outgoing",
    "has_same_proceeding_outgoing",
]


def build_law_table(
    *,
    run_root: Path,
    output_dir: Path,
    write_csv: bool,
    lookup: dict[str, tuple[str, str]],
    metadata_index,
    law_names: dict[str, str],
) -> int:
    input_path = run_root / "law" / "citation_occurrences.jsonl"
    if not input_path.exists():
        print(f"[analysis] skip law table, missing input: {input_path}")
        return 0

    output_jsonl = output_dir / "law_reference_table.jsonl"
    output_csv = output_dir / "law_reference_table.csv" if write_csv else None
    output_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    csv_handle, csv_writer = _open_maybe_csv(output_csv, LAW_FIELDS)
    with output_jsonl.open("w", encoding="utf-8") as jsonl_handle:
        try:
            for row in _jsonl_iter(input_path):
                source, document_path = _resolve_doc_identity(
                    row,
                    id_key="document_id",
                    source_key="source",
                    path_key="document_path",
                    lookup=lookup,
                )
                document_id = row.get("document_id")
                metadata = metadata_index.get((source, document_id)) if isinstance(source, str) and isinstance(document_id, str) else None
                raw_span = row.get("raw_span") or {}
                normalized_span = row.get("normalized_span") or {}
                parsed_detail = row.get("parsed_detail") or {}
                resolved_law_id = row.get("resolved_law_id")
                quality_reason = row.get("quality_reason") or _quality_reason_from_legacy_row(row)
                quality_flag = row.get("quality_flag", row.get("needs_review"))
                if quality_flag is None:
                    quality_flag = quality_reason is not None
                out = {
                    "document_id": document_id,
                    "document_path": document_path,
                    "source": source,
                    "decision_date_iso": metadata.get("decision_date_iso") if metadata else None,
                    "decision_year": metadata.get("decision_year") if metadata else None,
                    "decision_date_precision": metadata.get("decision_date_precision") if metadata else None,
                    "citation_text": row.get("citation_text"),
                    "citation_type": row.get("citation_type"),
                    "raw_start": raw_span.get("start"),
                    "raw_end": raw_span.get("end"),
                    "normalized_start": normalized_span.get("start"),
                    "normalized_end": normalized_span.get("end"),
                    "classification": row.get("predicted_classification"),
                    "resolved_law_id": resolved_law_id,
                    "resolved_law_name": law_names.get(resolved_law_id) if isinstance(resolved_law_id, str) else None,
                    "detail_number": parsed_detail.get("number"),
                    "detail_odst": parsed_detail.get("odst"),
                    "detail_pism": parsed_detail.get("pism"),
                    "detail_bod": parsed_detail.get("bod"),
                    "resolver_stage": row.get("resolver_stage"),
                    "confidence": row.get("confidence"),
                    "quality_flag": quality_flag,
                    "quality_reason": quality_reason,
                    "context": row.get("context"),
                    "candidate_law_ids": row.get("candidate_law_ids"),
                    "is_resolved": row.get("predicted_classification") == "czech_resolved",
                }
                _write_jsonl_row(jsonl_handle, out)
                if csv_writer is not None:
                    csv_writer.writerow(_normalize_csv_row(out, LAW_FIELDS))
                count += 1
        finally:
            if csv_handle is not None:
                csv_handle.close()
    return count


def build_document_occurrence_table(
    *,
    run_root: Path,
    output_dir: Path,
    write_csv: bool,
    metadata_index,
) -> int:
    input_path = run_root / "document_references" / "document_reference_occurrences.jsonl"
    if not input_path.exists():
        print(f"[analysis] skip document occurrence table, missing input: {input_path}")
        return 0

    output_jsonl = output_dir / "document_reference_occurrence_table.jsonl"
    output_csv = output_dir / "document_reference_occurrence_table.csv" if write_csv else None

    count = 0
    csv_handle, csv_writer = _open_maybe_csv(output_csv, DOC_OCC_FIELDS)
    with output_jsonl.open("w", encoding="utf-8") as jsonl_handle:
        try:
            for row in _jsonl_iter(input_path):
                source = row.get("source")
                document_id = row.get("document_id")
                metadata = metadata_index.get((source, document_id)) if isinstance(source, str) and isinstance(document_id, str) else None
                out = {
                    "document_id": document_id,
                    "document_path": row.get("document_path"),
                    "source": source,
                    "decision_date_iso": metadata.get("decision_date_iso") if metadata else None,
                    "decision_year": metadata.get("decision_year") if metadata else None,
                    "decision_date_precision": metadata.get("decision_date_precision") if metadata else None,
                    "reference_text": row.get("reference_text"),
                    "reference_prefix": row.get("reference_prefix"),
                    "reference_body": row.get("reference_body"),
                    "reference_type": row.get("reference_type"),
                    "raw_start": row.get("raw_start"),
                    "raw_end": row.get("raw_end"),
                    "decision_kind_hint": row.get("decision_kind_hint"),
                    "court_hint": row.get("court_hint"),
                    "context": row.get("context"),
                }
                _write_jsonl_row(jsonl_handle, out)
                if csv_writer is not None:
                    csv_writer.writerow(_normalize_csv_row(out, DOC_OCC_FIELDS))
                count += 1
        finally:
            if csv_handle is not None:
                csv_handle.close()
    return count


def build_document_edge_and_node_tables(
    *,
    run_root: Path,
    output_dir: Path,
    write_csv: bool,
    metadata_index,
) -> tuple[int, int]:
    links_path = run_root / "document_references" / "document_reference_links.jsonl"
    self_ids_path = run_root / "document_references" / "document_self_identifiers.jsonl"
    if not links_path.exists():
        print(f"[analysis] skip document edge/node tables, missing input: {links_path}")
        return 0, 0

    edge_jsonl = output_dir / "document_reference_edge_table.jsonl"
    edge_csv = output_dir / "document_reference_edge_table.csv" if write_csv else None
    node_jsonl = output_dir / "document_reference_node_table.jsonl"
    node_csv = output_dir / "document_reference_node_table.csv" if write_csv else None

    node_meta: dict[tuple[str, str], dict[str, Any]] = {}
    if self_ids_path.exists():
        for row in _jsonl_iter(self_ids_path):
            key = (row.get("source"), row.get("document_id"))
            if not isinstance(key[0], str) or not isinstance(key[1], str):
                continue
            existing = node_meta.get(key)
            if existing is None:
                node_meta[key] = {
                    "document_id": row.get("document_id"),
                    "document_path": row.get("document_path"),
                    "source": row.get("source"),
                    "display_identifier": row.get("identifier_text"),
                    "identifier_kind": row.get("identifier_kind"),
                    "identifier_origin": row.get("origin"),
                    "source_iri": row.get("source_iri"),
                }

    out_exact: defaultdict[tuple[str, str], int] = defaultdict(int)
    out_same: defaultdict[tuple[str, str], int] = defaultdict(int)
    in_exact: defaultdict[tuple[str, str], int] = defaultdict(int)
    in_same: defaultdict[tuple[str, str], int] = defaultdict(int)
    seen_nodes: set[tuple[str, str]] = set()

    edge_count = 0
    edge_csv_handle, edge_csv_writer = _open_maybe_csv(edge_csv, EDGE_FIELDS)
    with edge_jsonl.open("w", encoding="utf-8") as edge_handle:
        try:
            for row in _jsonl_iter(links_path):
                src_source = row.get("source_document_source")
                src_id = row.get("source_document_id")
                tgt_source = row.get("target_source")
                tgt_id = row.get("target_document_id")
                src_path = row.get("source_document_path")
                tgt_path = row.get("target_document_path")
                src_meta = metadata_index.get((src_source, src_id)) if isinstance(src_source, str) and isinstance(src_id, str) else None
                tgt_meta = metadata_index.get((tgt_source, tgt_id)) if isinstance(tgt_source, str) and isinstance(tgt_id, str) else None
                link_scope = row.get("target_match_scope")
                is_same = link_scope == "same_proceeding"
                out = {
                    "source_document_id": src_id,
                    "source_document_path": row.get("source_document_path"),
                    "source": src_source,
                    "source_decision_date_iso": src_meta.get("decision_date_iso") if src_meta else None,
                    "source_decision_year": src_meta.get("decision_year") if src_meta else None,
                    "target_document_id": tgt_id,
                    "target_document_path": row.get("target_document_path"),
                    "target": tgt_source,
                    "target_decision_date_iso": tgt_meta.get("decision_date_iso") if tgt_meta else None,
                    "target_decision_year": tgt_meta.get("decision_year") if tgt_meta else None,
                    "link_scope": link_scope,
                    "reference_text": row.get("reference_text"),
                    "reference_prefix": row.get("reference_prefix"),
                    "reference_body": row.get("reference_body"),
                    "reference_type": row.get("reference_type"),
                    "raw_start": row.get("raw_start"),
                    "raw_end": row.get("raw_end"),
                    "link_method": row.get("link_method"),
                    "link_key": row.get("link_key"),
                    "target_proceeding_key": row.get("target_proceeding_key"),
                    "target_group_size": row.get("target_group_size"),
                    "decision_kind_hint": row.get("decision_kind_hint"),
                    "court_hint": row.get("court_hint"),
                    "is_same_proceeding": is_same,
                }
                _write_jsonl_row(edge_handle, out)
                if edge_csv_writer is not None:
                    edge_csv_writer.writerow(_normalize_csv_row(out, EDGE_FIELDS))
                edge_count += 1

                src_key = (src_source, src_id)
                tgt_key = (tgt_source, tgt_id)
                if isinstance(src_source, str) and isinstance(src_id, str):
                    seen_nodes.add(src_key)
                    info = node_meta.setdefault(src_key, {"document_id": src_id, "source": src_source})
                    if src_path and not info.get("document_path"):
                        info["document_path"] = src_path
                if isinstance(tgt_source, str) and isinstance(tgt_id, str):
                    seen_nodes.add(tgt_key)
                    info = node_meta.setdefault(tgt_key, {"document_id": tgt_id, "source": tgt_source})
                    if tgt_path and not info.get("document_path"):
                        info["document_path"] = tgt_path
                if isinstance(src_source, str) and isinstance(src_id, str) and isinstance(tgt_source, str) and isinstance(tgt_id, str):
                    if is_same:
                        out_same[src_key] += 1
                        in_same[tgt_key] += 1
                    else:
                        out_exact[src_key] += 1
                        in_exact[tgt_key] += 1
        finally:
            if edge_csv_handle is not None:
                edge_csv_handle.close()

    node_count = 0
    node_csv_handle, node_csv_writer = _open_maybe_csv(node_csv, NODE_FIELDS)
    with node_jsonl.open("w", encoding="utf-8") as node_handle:
        try:
            for source, document_id in sorted(seen_nodes):
                meta = metadata_index.get((source, document_id))
                info = node_meta.get((source, document_id), {})
                out = {
                    "document_id": document_id,
                    "document_path": info.get("document_path"),
                    "source": source,
                    "decision_date_iso": meta.get("decision_date_iso") if meta else None,
                    "decision_year": meta.get("decision_year") if meta else None,
                    "decision_date_precision": meta.get("decision_date_precision") if meta else None,
                    "display_identifier": info.get("display_identifier") or document_id,
                    "identifier_kind": info.get("identifier_kind"),
                    "identifier_origin": info.get("identifier_origin"),
                    "source_iri": info.get("source_iri"),
                    "in_degree_exact": in_exact.get((source, document_id), 0),
                    "in_degree_same_proceeding": in_same.get((source, document_id), 0),
                    "out_degree_exact": out_exact.get((source, document_id), 0),
                    "out_degree_same_proceeding": out_same.get((source, document_id), 0),
                    "has_exact_incoming": in_exact.get((source, document_id), 0) > 0,
                    "has_same_proceeding_incoming": in_same.get((source, document_id), 0) > 0,
                    "has_exact_outgoing": out_exact.get((source, document_id), 0) > 0,
                    "has_same_proceeding_outgoing": out_same.get((source, document_id), 0) > 0,
                }
                _write_jsonl_row(node_handle, out)
                if node_csv_writer is not None:
                    node_csv_writer.writerow(_normalize_csv_row(out, NODE_FIELDS))
                node_count += 1
        finally:
            if node_csv_handle is not None:
                node_csv_handle.close()

    return edge_count, node_count


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root)
    output_dir = Path(args.output_dir) if args.output_dir else run_root / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_root = Path(args.corpus_root)
    lookup = _build_document_lookup(corpus_root)
    metadata_index = load_document_dates_index(args.metadata_path)
    law_names = _load_canonical_law_names(Path(args.canonical_laws))

    law_count = build_law_table(
        run_root=run_root,
        output_dir=output_dir,
        write_csv=args.write_csv,
        lookup=lookup,
        metadata_index=metadata_index,
        law_names=law_names,
    )
    doc_occ_count = build_document_occurrence_table(
        run_root=run_root,
        output_dir=output_dir,
        write_csv=args.write_csv,
        metadata_index=metadata_index,
    )
    edge_count, node_count = build_document_edge_and_node_tables(
        run_root=run_root,
        output_dir=output_dir,
        write_csv=args.write_csv,
        metadata_index=metadata_index,
    )

    summary = {
        "run_root": str(run_root),
        "output_dir": str(output_dir),
        "law_reference_rows": law_count,
        "document_reference_occurrence_rows": doc_occ_count,
        "document_reference_edge_rows": edge_count,
        "document_reference_node_rows": node_count,
        "csv_written": bool(args.write_csv),
    }
    (output_dir / "analysis_table_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("--- ANALYSIS TABLE BUILD COMPLETE ---")
    print(f"Output dir: {output_dir}")
    print(f"Law references: {law_count}")
    print(f"Document-reference occurrences: {doc_occ_count}")
    print(f"Document-reference edges: {edge_count}")
    print(f"Document-reference nodes: {node_count}")


if __name__ == "__main__":
    main()
