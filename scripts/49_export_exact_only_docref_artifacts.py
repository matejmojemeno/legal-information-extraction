#!/usr/bin/env python3
"""Export exact-link-only document-reference artifacts from an existing full run.

This keeps the original mixed-scope outputs intact and derives a postprocessed
exact-only view suitable for thesis reporting.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_exact_only_unresolved_row(link_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source_document_source": link_row["source_document_source"],
        "source_document_id": link_row["source_document_id"],
        "source_document_path": link_row["source_document_path"],
        "reference_text": link_row["reference_text"],
        "reference_prefix": link_row["reference_prefix"],
        "reference_body": link_row["reference_body"],
        "reference_type": link_row["reference_type"],
        "raw_start": link_row["raw_start"],
        "raw_end": link_row["raw_end"],
        "decision_kind_hint": link_row.get("decision_kind_hint"),
        "court_hint": link_row.get("court_hint"),
        "llm_route": "exact_link_not_available",
        "candidate_targets": [],
        "exact_only_origin": "reclassified_from_non_exact_link",
        "exact_only_reason": "original deterministic link did not support one exact target",
        "original_link": {
            "target_source": link_row.get("target_source"),
            "target_document_id": link_row.get("target_document_id"),
            "target_document_path": link_row.get("target_document_path"),
            "target_match_scope": link_row.get("target_match_scope"),
            "target_proceeding_key": link_row.get("target_proceeding_key"),
            "target_group_size": link_row.get("target_group_size"),
            "link_method": link_row.get("link_method"),
            "link_key": link_row.get("link_key"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        default="data/final_runs/thesis_final_v1",
        help="Path to the final run root",
    )
    args = parser.parse_args()

    run_root = Path(args.run_root)
    docref_dir = run_root / "document_references"
    manifest_path = run_root / "run_manifest.json"

    links_path = docref_dir / "document_reference_links.jsonl"
    unresolved_path = docref_dir / "document_reference_unresolved.jsonl"

    exact_links_out = docref_dir / "document_reference_links_exact_only.jsonl"
    non_exact_links_out = docref_dir / "document_reference_non_exact_link_rows.jsonl"
    unresolved_exact_out = docref_dir / "document_reference_unresolved_exact_only.jsonl"
    summary_json_out = docref_dir / "document_reference_exact_only_summary.json"
    summary_md_out = docref_dir / "document_reference_exact_only_summary.md"

    manifest = read_json(manifest_path)

    exact_links: list[Dict[str, Any]] = []
    non_exact_links: list[Dict[str, Any]] = []
    non_exact_scope_counts: Counter[str] = Counter()

    with links_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            scope = row.get("target_match_scope")
            if scope == "exact_decision":
                exact_links.append(row)
            else:
                non_exact_links.append(row)
                non_exact_scope_counts[str(scope)] += 1

    unresolved_rows: list[Dict[str, Any]] = []
    with unresolved_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            row["exact_only_origin"] = "original_unresolved"
            unresolved_rows.append(row)

    reclassified_rows = [
        make_exact_only_unresolved_row(row) for row in non_exact_links
    ]
    unresolved_exact_rows = unresolved_rows + reclassified_rows

    write_jsonl(exact_links_out, exact_links)
    write_jsonl(non_exact_links_out, non_exact_links)
    write_jsonl(unresolved_exact_out, unresolved_exact_rows)

    summary = {
        "run_root": str(run_root),
        "original_manifest_path": str(manifest_path),
        "original_links_path": str(links_path),
        "original_unresolved_path": str(unresolved_path),
        "exact_links_path": str(exact_links_out),
        "non_exact_links_path": str(non_exact_links_out),
        "exact_only_unresolved_path": str(unresolved_exact_out),
        "original_occurrences": manifest["document_references"]["occurrences"],
        "original_linked": manifest["document_references"]["linked"],
        "original_unresolved": manifest["document_references"]["unresolved"],
        "exact_only_linked": len(exact_links),
        "reclassified_non_exact_links": len(non_exact_links),
        "exact_only_unresolved": len(unresolved_exact_rows),
        "non_exact_scope_counts": dict(non_exact_scope_counts),
        "notes": [
            "Original full-run artifacts were left unchanged.",
            "Exact-only unresolved rows combine the original unresolved export with rows reclassified from non-exact deterministic links.",
            "The exact-only export matches the exact-link-only thesis framing without requiring a full corpus rerun.",
        ],
    }
    write_json(summary_json_out, summary)

    summary_md = "\n".join(
        [
            "# Exact-Only Document-Reference Export",
            "",
            f"- run root: `{run_root}`",
            f"- original linked rows: `{manifest['document_references']['linked']}`",
            f"- original unresolved rows: `{manifest['document_references']['unresolved']}`",
            f"- exact-only linked rows: `{len(exact_links)}`",
            f"- reclassified non-exact link rows: `{len(non_exact_links)}`",
            f"- exact-only unresolved rows: `{len(unresolved_exact_rows)}`",
            "",
            "## Non-Exact Link Breakdown",
            "",
            *[
                f"- `{scope}`: `{count}`"
                for scope, count in sorted(non_exact_scope_counts.items())
            ],
            "",
            "## Outputs",
            "",
            f"- exact links: `{exact_links_out}`",
            f"- non-exact links: `{non_exact_links_out}`",
            f"- exact-only unresolved: `{unresolved_exact_out}`",
            f"- summary json: `{summary_json_out}`",
        ]
    )
    summary_md_out.write_text(summary_md + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
