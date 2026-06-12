#!/usr/bin/env python3
"""Official production script: run the canonical full-corpus pipeline.

This wrapper orchestrates the thesis production pipeline over the canonical
imported corpus. It runs deterministic law-reference extraction/resolution,
document-reference extraction, deterministic document-reference linking, and
optionally the law-reference Step 6 LLM stage.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.alias_extractor import extract_local_aliases
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import (
    append_anomalies_to_file,
    append_occurrences_to_jsonl,
    extract_citation_occurrences,
    occurrences_to_resolved_and_anomalies,
)
from src.document_metadata import load_document_dates_index, load_law_timelines
from src.document_reference_extractor import extract_document_references
from src.document_reference_linker import stream_corpus_document_references
from src.production_paths import (
    PRODUCTION_AUDITED_ALIASES_PATH,
    PRODUCTION_CANONICAL_LAWS_PATH,
    PRODUCTION_CORPUS_ROOT,
    PRODUCTION_DATE_METADATA_PATH,
    PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH,
    PRODUCTION_FINAL_RUNS_ROOT,
    PRODUCTION_GLOBAL_ALIASES_PATH,
    PRODUCTION_SEED_ALIASES_PATH,
    PRODUCTION_AMBIGUOUS_ALIASES_PATH,
)


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iter_source_file_groups(corpus_root: Path, limit_per_source: int | None) -> list[tuple[str, list[Path]]]:
    groups: list[tuple[str, list[Path]]] = []
    for source_dir in _iter_source_dirs(corpus_root):
        source_files = sorted(
            path for path in source_dir.iterdir() if path.is_file() and path.suffix == ".txt"
        )
        if limit_per_source is not None:
            source_files = source_files[:limit_per_source]
        groups.append((source_dir.name, source_files))
    return groups


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Official production pipeline: run deterministic extraction/linking "
            "on the canonical full corpus and write a final run under data/final_runs/."
        )
    )
    parser.add_argument("--tag", required=True, help="Run tag, used for the final output directory.")
    parser.add_argument(
        "--corpus-root",
        default=PRODUCTION_CORPUS_ROOT,
        help="Canonical corpus root with per-source .txt documents.",
    )
    parser.add_argument(
        "--external-self-id-metadata",
        default=PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH,
        help="Canonical metadata JSONL(.gz) used for external self-identifier enrichment during linking.",
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Optional per-source cap for a smoke run before the full corpus run.",
    )
    parser.add_argument(
        "--run-law-step6",
        action="store_true",
        help="Also run the optional law-reference Step 6 LLM stage over the produced anomaly queue.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N processed documents per stage (default: 100).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from existing checkpointable outputs when possible (default: true).",
    )
    parser.add_argument(
        "--docref-use-fuzzy-candidates",
        action="store_true",
        help=(
            "Enable fuzzy candidate generation when writing unresolved document-reference rows. "
            "Disabled by default for the deterministic thesis-final corpus run."
        ),
    )
    return parser.parse_args()


def _iter_source_dirs(corpus_root: Path) -> list[Path]:
    return [path for path in (corpus_root / "nalus", corpus_root / "ns", corpus_root / "nss", corpus_root / "uohs") if path.is_dir()]


def _iter_text_paths(corpus_root: Path, limit_per_source: int | None) -> list[Path]:
    paths: list[Path] = []
    for _, source_files in _iter_source_file_groups(corpus_root, limit_per_source):
        paths.extend(source_files)
    return paths


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_completed_document_keys(path: Path) -> set[tuple[str | None, str]]:
    completed: set[tuple[str | None, str]] = set()
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            document_id = row.get("document_id")
            if not isinstance(document_id, str):
                continue
            source = row.get("source")
            completed.add((source if isinstance(source, str) else None, document_id))
    return completed


def _append_occurrence_rows(
    output_path: Path,
    *,
    source: str,
    document_id: str,
    document_path: str,
    occurrences: list,
) -> None:
    rows: list[dict] = []
    for occ in occurrences:
        row = occ.to_dict()
        row["document_id"] = document_id
        row["source"] = source
        row["document_path"] = document_path
        rows.append(row)
    _append_jsonl(output_path, rows)


def _append_law_anomaly_checkpoint_rows(
    output_path: Path,
    anomalies: list[dict],
) -> None:
    _append_jsonl(output_path, anomalies)


def _finalize_anomaly_checkpoint_json(
    checkpoint_path: Path,
    final_json_path: Path,
) -> int:
    if not checkpoint_path.exists():
        return 0
    rows: list[dict] = []
    seen: set[str] = set()
    with checkpoint_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            entry_id = row.get("entry_id")
            dedupe_key = entry_id if isinstance(entry_id, str) else json.dumps(row, ensure_ascii=False, sort_keys=True)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(row)
    final_json_path.parent.mkdir(parents=True, exist_ok=True)
    final_json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=4), encoding="utf-8")
    return len(rows)


def _build_document_lookup(corpus_root: Path) -> dict[str, list[tuple[str, str]]]:
    lookup: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source, source_files in _iter_source_file_groups(corpus_root, None):
        for path in source_files:
            lookup[path.name].append((source, os.path.relpath(path.resolve(), Path.cwd())))
    return lookup


def _rebuild_law_anomaly_checkpoint_from_occurrences(
    *,
    corpus_root: Path,
    occurrences_path: Path,
    checkpoint_path: Path,
) -> int:
    if checkpoint_path.exists() or not occurrences_path.exists():
        return 0
    document_lookup = _build_document_lookup(corpus_root)
    rebuilt_rows: list[dict] = []
    with occurrences_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("predicted_classification") != "unresolved":
                continue
            document_id = row.get("document_id")
            source = row.get("source")
            document_path = row.get("document_path")
            if isinstance(document_id, str) and (not isinstance(source, str) or not isinstance(document_path, str)):
                candidates = document_lookup.get(document_id, [])
                if len(candidates) == 1:
                    source = candidates[0][0]
                    document_path = candidates[0][1]

            raw_span = row.get("raw_span") or {}
            rebuilt_rows.append(
                {
                    "target_reference": row.get("citation_text"),
                    "context_block": row.get("context"),
                    "candidates": row.get("candidate_law_ids") or [],
                    "resolver_stage": row.get("resolver_stage"),
                    "confidence": row.get("confidence"),
                    "citation_type": row.get("citation_type"),
                    "raw_start": raw_span.get("start"),
                    "raw_end": raw_span.get("end"),
                    "document_id": document_id,
                    "document_path": document_path,
                    "source": source,
                }
            )
    _append_jsonl(checkpoint_path, rebuilt_rows)
    return len(rebuilt_rows)


def _run_law_pipeline(
    *,
    corpus_root: Path,
    law_dir: Path,
    limit_per_source: int | None,
    progress_every: int,
    resume: bool,
) -> dict[str, int | str]:
    occurrences_path = law_dir / "citation_occurrences.jsonl"
    anomaly_queue_path = law_dir / "to_be_checked_by_llm.json"
    anomaly_checkpoint_path = law_dir / "to_be_checked_by_llm.checkpoint.jsonl"
    if occurrences_path.exists() and not resume:
        occurrences_path.unlink()
    if anomaly_queue_path.exists() and not resume:
        anomaly_queue_path.unlink()
    if anomaly_checkpoint_path.exists() and not resume:
        anomaly_checkpoint_path.unlink()

    rebuilt_from_occurrences = _rebuild_law_anomaly_checkpoint_from_occurrences(
        corpus_root=corpus_root,
        occurrences_path=occurrences_path,
        checkpoint_path=anomaly_checkpoint_path,
    )
    completed_docs = _load_completed_document_keys(occurrences_path) if resume else set()

    alias_map, alias_source = load_runtime_aliases(
        audited_path=PRODUCTION_AUDITED_ALIASES_PATH,
        global_path=PRODUCTION_GLOBAL_ALIASES_PATH,
        seeded_path=PRODUCTION_SEED_ALIASES_PATH,
        ambiguous_path=PRODUCTION_AMBIGUOUS_ALIASES_PATH,
    )
    document_dates_index = load_document_dates_index()
    law_timelines = load_law_timelines()

    total_occurrences = 0
    total_resolved = 0
    total_anomalies = 0
    file_count = 0
    total_files = sum(len(paths) for _, paths in _iter_source_file_groups(corpus_root, limit_per_source))

    print("--- LAW PIPELINE ---", flush=True)
    print(f"Corpus root: {corpus_root}", flush=True)
    print(f"Documents to process: {total_files}", flush=True)
    if completed_docs:
        print(f"[law] resume enabled, completed docs already on disk: {len(completed_docs)}", flush=True)
    if rebuilt_from_occurrences:
        print(f"[law] rebuilt anomaly checkpoint rows from existing occurrences: {rebuilt_from_occurrences}", flush=True)

    for source, source_files in _iter_source_file_groups(corpus_root, limit_per_source):
        print(f"[law] source={source} docs={len(source_files)}", flush=True)
        for source_index, path in enumerate(source_files, start=1):
            document_id = path.name
            if (source, document_id) in completed_docs or (None, document_id) in completed_docs:
                file_count += 1
                if source_index == len(source_files) or file_count % progress_every == 0:
                    print(
                        f"[law] processed={file_count}/{total_files} source={source} "
                        f"source_docs={source_index}/{len(source_files)} resumed_skip=1",
                        flush=True,
                    )
                continue
            text = path.read_text(encoding="utf-8")
            document_path = os.path.relpath(path.resolve(), Path.cwd())
            local_aliases = extract_local_aliases(text)
            occurrences = extract_citation_occurrences(
                text,
                local_aliases,
                alias_map,
                document_metadata=document_dates_index.get((source, document_id)),
                law_timelines=law_timelines or None,
            )
            _append_occurrence_rows(
                occurrences_path,
                source=source,
                document_id=document_id,
                document_path=document_path,
                occurrences=occurrences,
            )
            resolved, anomalies = occurrences_to_resolved_and_anomalies(
                occurrences,
                text,
                include_low_confidence_anomalies=False,
                document_id=document_id,
                document_path=document_path,
                document_source=source,
            )
            total_occurrences += len(occurrences)
            total_resolved += len(resolved)
            total_anomalies += len(anomalies)
            _append_law_anomaly_checkpoint_rows(anomaly_checkpoint_path, anomalies)
            file_count += 1

            if (
                source_index == len(source_files)
                or file_count % progress_every == 0
            ):
                print(
                    f"[law] processed={file_count}/{total_files} "
                    f"source={source} source_docs={source_index}/{len(source_files)} "
                    f"occurrences={total_occurrences} resolved={total_resolved} anomalies={total_anomalies}",
                        flush=True,
                )

    finalized_anomaly_count = _finalize_anomaly_checkpoint_json(
        anomaly_checkpoint_path,
        anomaly_queue_path,
    )
    print(
        f"[law] complete docs={file_count} occurrences={total_occurrences} "
        f"resolved={total_resolved} anomalies={finalized_anomaly_count}",
        flush=True,
    )
    return {
        "runtime_alias_source": alias_source,
        "occurrences": total_occurrences,
        "resolved": total_resolved,
        "anomalies": finalized_anomaly_count,
        "occurrences_path": str(occurrences_path),
        "anomaly_queue_path": str(anomaly_queue_path),
    }


def _run_document_reference_pipeline(
    *,
    corpus_root: Path,
    docref_dir: Path,
    limit_per_source: int | None,
    external_self_id_metadata: str,
    progress_every: int,
    resume: bool,
    use_fuzzy_candidates: bool,
) -> dict[str, int | str]:
    occurrence_output = docref_dir / "document_reference_occurrences.jsonl"
    if occurrence_output.exists() and not resume:
        occurrence_output.unlink()

    total_files = sum(len(paths) for _, paths in _iter_source_file_groups(corpus_root, limit_per_source))
    total_occurrences = 0
    file_count = 0
    extraction_needed = True

    if resume and occurrence_output.exists():
        completed_docs = _load_completed_document_keys(occurrence_output)
        if len(completed_docs) >= total_files:
            extraction_needed = False
            print(f"[docref] extraction resume: using existing occurrence file {occurrence_output}", flush=True)
        else:
            print(f"[docref] extraction resume enabled, completed docs already on disk: {len(completed_docs)}", flush=True)
    else:
        completed_docs = set()

    print("--- DOCUMENT-REFERENCE PIPELINE ---", flush=True)
    print(f"Corpus root: {corpus_root}", flush=True)
    print(f"Documents to process: {total_files}", flush=True)
    if extraction_needed:
        for source, source_files in _iter_source_file_groups(corpus_root, limit_per_source):
            print(f"[docref] source={source} docs={len(source_files)}", flush=True)
            for source_index, path in enumerate(source_files, start=1):
                document_id = path.name
                if (source, document_id) in completed_docs or (None, document_id) in completed_docs:
                    file_count += 1
                    if (
                        source_index == len(source_files)
                        or file_count % progress_every == 0
                    ):
                        print(
                            f"[docref] processed={file_count}/{total_files} "
                            f"source={source} source_docs={source_index}/{len(source_files)} resumed_skip=1",
                            flush=True,
                        )
                    continue
                text = path.read_text(encoding="utf-8")
                refs = extract_document_references(text)
                total_occurrences += len(refs)
                rows: list[dict] = []
                for ref in refs:
                    row = ref.to_dict()
                    row["document_id"] = document_id
                    row["source"] = source
                    row["document_path"] = os.path.relpath(path.resolve(), Path.cwd())
                    rows.append(row)
                _append_jsonl(occurrence_output, rows)
                file_count += 1
                if (
                    source_index == len(source_files)
                    or file_count % progress_every == 0
                ):
                    print(
                        f"[docref] processed={file_count}/{total_files} "
                        f"source={source} source_docs={source_index}/{len(source_files)} "
                        f"occurrences={total_occurrences}",
                            flush=True,
                    )
        print(f"[docref] extraction complete docs={file_count} occurrences={total_occurrences}", flush=True)
    else:
        with occurrence_output.open("r", encoding="utf-8") as handle:
            total_occurrences = sum(1 for _ in handle if _.strip())
        print(f"[docref] extraction already complete occurrences={total_occurrences}", flush=True)

    if limit_per_source is None:
        link_root = str(corpus_root)
    else:
        staging_root = docref_dir / "_corpus_slice"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        print(f"[docref] building link slice under {staging_root}", flush=True)
        for path in _iter_text_paths(corpus_root, limit_per_source):
            dest = staging_root / path.parent.name / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        link_root = str(staging_root)

    print(f"[docref] linking from root={link_root}", flush=True)
    checkpoint_path = docref_dir / "document_reference_linking_checkpoint.jsonl"
    link_summary = stream_corpus_document_references(
        processed_root=link_root,
        linked_output_path=str(docref_dir / "document_reference_links.jsonl"),
        unresolved_output_path=str(docref_dir / "document_reference_unresolved.jsonl"),
        self_id_output_path=str(docref_dir / "document_self_identifiers.jsonl"),
        external_metadata_path=external_self_id_metadata,
        checkpoint_path=str(checkpoint_path),
        progress_every_docs=progress_every,
        resume=resume,
        use_fuzzy_candidates=use_fuzzy_candidates,
    )
    print(
        f"[docref] complete occurrences={total_occurrences} linked={link_summary['linked']} "
        f"unresolved={link_summary['unresolved']} self_identifiers={link_summary['self_identifiers']}",
        flush=True,
    )
    return {
        "occurrences": total_occurrences,
        "linked": link_summary["linked"],
        "unresolved": link_summary["unresolved"],
        "self_identifiers": link_summary["self_identifiers"],
        "occurrences_path": str(occurrence_output),
        "linked_output_path": str(docref_dir / "document_reference_links.jsonl"),
        "unresolved_output_path": str(docref_dir / "document_reference_unresolved.jsonl"),
        "self_id_output_path": str(docref_dir / "document_self_identifiers.jsonl"),
        "link_checkpoint_path": str(checkpoint_path),
    }


def _run_step6(anomaly_queue_path: str, law_dir: Path) -> str:
    output_path = law_dir / "llm_reference_resolutions.jsonl"
    print(f"[step6] starting anomaly_queue={anomaly_queue_path}", flush=True)
    cmd = [
        sys.executable,
        "scripts/06_llm_resolve_references.py",
        "--input",
        anomaly_queue_path,
        "--output",
        str(output_path),
        "--canonical",
        PRODUCTION_CANONICAL_LAWS_PATH,
        "--audited-aliases",
        PRODUCTION_AUDITED_ALIASES_PATH,
        "--global-aliases",
        PRODUCTION_GLOBAL_ALIASES_PATH,
        "--ambiguous-aliases",
        PRODUCTION_AMBIGUOUS_ALIASES_PATH,
        "--seed-aliases",
        PRODUCTION_SEED_ALIASES_PATH,
    ]
    subprocess.run(cmd, check=True)
    print(f"[step6] complete output={output_path}", flush=True)
    return str(output_path)


def main() -> None:
    args = parse_args()
    corpus_root = Path(args.corpus_root)
    run_root = Path(PRODUCTION_FINAL_RUNS_ROOT) / args.tag
    law_dir = run_root / "law"
    docref_dir = run_root / "document_references"
    run_root.mkdir(parents=True, exist_ok=True)

    law_summary = _run_law_pipeline(
        corpus_root=corpus_root,
        law_dir=law_dir,
        limit_per_source=args.limit_per_source,
        progress_every=args.progress_every,
        resume=args.resume,
    )
    docref_summary = _run_document_reference_pipeline(
        corpus_root=corpus_root,
        docref_dir=docref_dir,
        limit_per_source=args.limit_per_source,
        external_self_id_metadata=args.external_self_id_metadata,
        progress_every=args.progress_every,
        resume=args.resume,
        use_fuzzy_candidates=args.docref_use_fuzzy_candidates,
    )

    step6_output = None
    if args.run_law_step6:
        step6_output = _run_step6(str(law_dir / "to_be_checked_by_llm.json"), law_dir)

    manifest = {
        "tag": args.tag,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus_root": str(corpus_root),
        "limit_per_source": args.limit_per_source,
        "external_self_id_metadata": args.external_self_id_metadata,
        "docref_use_fuzzy_candidates": args.docref_use_fuzzy_candidates,
        "law": law_summary,
        "document_references": docref_summary,
        "step6_output": step6_output,
        "runtime_artifacts": {
            "canonical_laws": PRODUCTION_CANONICAL_LAWS_PATH,
            "global_aliases": PRODUCTION_GLOBAL_ALIASES_PATH,
            "audited_aliases": PRODUCTION_AUDITED_ALIASES_PATH,
            "seed_aliases": PRODUCTION_SEED_ALIASES_PATH,
            "ambiguous_aliases": PRODUCTION_AMBIGUOUS_ALIASES_PATH,
        },
    }
    manifest_path = run_root / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- FINAL PRODUCTION PIPELINE COMPLETE ---")
    print(f"Run root:   {run_root}")
    print(f"Manifest:   {manifest_path}")
    print(f"Law refs:   {law_summary['occurrences']} occurrences, {law_summary['anomalies']} anomalies")
    print(f"Doc refs:   {docref_summary['occurrences']} occurrences, {docref_summary['linked']} linked")
    if step6_output:
        print(f"Step 6:     {step6_output}")


if __name__ == "__main__":
    main()
