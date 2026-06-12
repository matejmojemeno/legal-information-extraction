#!/usr/bin/env python3
"""Rebuild only the law stage for an existing final production run.

This helper preserves the existing document-reference outputs, backs up the
current law directory, reruns the deterministic law pipeline into the same run
root, and refreshes the law section of the run manifest.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _load_final_pipeline_module():
    script_path = Path(__file__).resolve().parent / "40_run_final_pipeline.py"
    spec = importlib.util.spec_from_file_location("final_pipeline", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load final pipeline module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backup the current law outputs for a final run and rerun only the "
            "deterministic law pipeline in place."
        )
    )
    parser.add_argument("--tag", required=True, help="Existing final run tag, e.g. thesis_final_v1")
    parser.add_argument(
        "--backup-suffix",
        default="before_law_refresh",
        help="Suffix used for backup directories (default: before_law_refresh)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N processed documents (default: 100).",
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Optional per-source cap for a smoke rerun.",
    )
    parser.add_argument(
        "--no-backup-analysis",
        action="store_true",
        help="Do not back up the analysis directory before the law rerun.",
    )
    return parser.parse_args()


def _unique_backup_path(path: Path, suffix: str) -> Path:
    candidate = path.with_name(f"{path.name}_{suffix}")
    if not candidate.exists():
        return candidate
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.name}_{suffix}_{timestamp}")


def main() -> None:
    args = parse_args()
    module = _load_final_pipeline_module()

    run_root = Path(module.PRODUCTION_FINAL_RUNS_ROOT) / args.tag
    if not run_root.exists():
        raise SystemExit(f"Run root does not exist: {run_root}")

    law_dir = run_root / "law"
    docref_dir = run_root / "document_references"
    analysis_dir = run_root / "analysis"
    manifest_path = run_root / "run_manifest.json"

    if not law_dir.exists():
        raise SystemExit(f"Law directory does not exist: {law_dir}")
    if not docref_dir.exists():
        raise SystemExit(f"Document-reference directory does not exist: {docref_dir}")

    law_backup = _unique_backup_path(law_dir, args.backup_suffix)
    print(f"[law-rerun] backing up {law_dir} -> {law_backup}", flush=True)
    shutil.move(str(law_dir), str(law_backup))

    analysis_backup = None
    if analysis_dir.exists() and not args.no_backup_analysis:
        analysis_backup = _unique_backup_path(analysis_dir, args.backup_suffix)
        print(f"[law-rerun] backing up {analysis_dir} -> {analysis_backup}", flush=True)
        shutil.move(str(analysis_dir), str(analysis_backup))

    corpus_root = Path(module.PRODUCTION_CORPUS_ROOT)
    law_dir.mkdir(parents=True, exist_ok=True)
    print(f"[law-rerun] rerunning law stage into {law_dir}", flush=True)
    law_summary = module._run_law_pipeline(
        corpus_root=corpus_root,
        law_dir=law_dir,
        limit_per_source=args.limit_per_source,
        progress_every=args.progress_every,
        resume=False,
    )

    manifest: dict[str, object] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["law"] = law_summary
    manifest["law_refresh"] = {
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "backup_law_dir": str(law_backup),
        "backup_analysis_dir": str(analysis_backup) if analysis_backup else None,
        "limit_per_source": args.limit_per_source,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- LAW-ONLY RERUN COMPLETE ---")
    print(f"Run root:        {run_root}")
    print(f"Law backup:      {law_backup}")
    if analysis_backup:
        print(f"Analysis backup: {analysis_backup}")
    print(f"Law dir:         {law_dir}")
    print(f"Manifest:        {manifest_path}")
    print(
        f"Law refs:        {law_summary['occurrences']} occurrences, "
        f"{law_summary['resolved']} resolved, {law_summary['anomalies']} anomalies"
    )
    print("Next: rebuild analysis tables with scripts/42, scripts/43, and scripts/44.")


if __name__ == "__main__":
    main()
