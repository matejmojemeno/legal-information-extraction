#!/usr/bin/env python3
"""Compare pre-refresh and post-refresh law outputs for a final run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _count_occurrences(path: Path) -> tuple[int, int]:
    total = 0
    resolved = 0
    if not path.exists():
        return total, resolved
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            if row.get("predicted_classification") == "czech_resolved" and row.get("resolved_law_id"):
                resolved += 1
    return total, resolved


def _load_anomaly_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix == ".json":
        return len(json.loads(path.read_text(encoding="utf-8")))
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare old and refreshed law outputs for a final run."
    )
    parser.add_argument("--run-root", required=True, help="Path to the final run root.")
    parser.add_argument(
        "--backup-law-dir",
        required=True,
        help="Path to the backed-up pre-refresh law directory.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root)
    current_law_dir = run_root / "law"
    backup_law_dir = Path(args.backup_law_dir)

    current_occurrences = current_law_dir / "citation_occurrences.jsonl"
    backup_occurrences = backup_law_dir / "citation_occurrences.jsonl"
    current_anomalies = current_law_dir / "to_be_checked_by_llm.json"
    backup_anomalies = backup_law_dir / "to_be_checked_by_llm.json"

    current_total, current_resolved = _count_occurrences(current_occurrences)
    backup_total, backup_resolved = _count_occurrences(backup_occurrences)
    current_unresolved = current_total - current_resolved
    backup_unresolved = backup_total - backup_resolved

    summary = {
        "run_root": str(run_root),
        "backup_law_dir": str(backup_law_dir),
        "before": {
            "occurrences": backup_total,
            "resolved": backup_resolved,
            "unresolved": backup_unresolved,
            "anomalies": _load_anomaly_count(backup_anomalies),
        },
        "after": {
            "occurrences": current_total,
            "resolved": current_resolved,
            "unresolved": current_unresolved,
            "anomalies": _load_anomaly_count(current_anomalies),
        },
        "delta": {
            "occurrences": current_total - backup_total,
            "resolved": current_resolved - backup_resolved,
            "unresolved": current_unresolved - backup_unresolved,
            "anomalies": _load_anomaly_count(current_anomalies) - _load_anomaly_count(backup_anomalies),
        },
    }

    out_path = run_root / "law_refresh_comparison.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- LAW REFRESH COMPARISON ---")
    print(f"Run root:         {run_root}")
    print(f"Backup law dir:   {backup_law_dir}")
    print(f"Before resolved:  {backup_resolved}")
    print(f"After resolved:   {current_resolved}")
    print(f"Resolved delta:   {current_resolved - backup_resolved}")
    print(f"Before unresolved:{backup_unresolved}")
    print(f"After unresolved: {current_unresolved}")
    print(f"Unresolved delta: {current_unresolved - backup_unresolved}")
    print(f"Output:           {out_path}")


if __name__ == "__main__":
    main()
