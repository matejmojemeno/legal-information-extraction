#!/usr/bin/env python3
"""
Build normalized law timeline metadata from the raw e-Sbírka dump.

Input:
  data/dicts/002PravniAkt.json

Output:
  data/dicts/law_timelines.json

The generated artifact keeps law-level timeline facts separate from the
human-facing canonical name dictionary so the resolver can later use:
- law effective/version start dates
- first/last known effective dates
- version counts and version IRIs
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_INPUT = "data/dicts/002PravniAkt.json"
DEFAULT_OUTPUT = "data/dicts/law_timelines.json"


def _parse_version_date_from_iri(iri: str | None) -> str | None:
    if not iri or "/" not in iri:
        return None
    date_part = iri.rsplit("/", 1)[-1]
    if date_part == "0000-00-00":
        return None
    if len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
        return date_part
    return None


def _normalize_law_timeline(item: dict) -> tuple[str, dict] | None:
    law_id = item.get("akt-citace")
    if not isinstance(law_id, str) or not law_id.strip():
        return None

    versions = item.get("právní-akt-znění") or []
    normalized_versions: list[dict] = []

    for version in versions:
        if not isinstance(version, dict):
            continue
        iri = version.get("iri")
        normalized_versions.append(
            {
                "iri": iri,
                "version_document_id": version.get("znění-dokument-id"),
                "effective_from": _parse_version_date_from_iri(iri),
                "last_change_at": version.get("datum-čas-poslední-změny"),
                "publication_state": version.get("cis-esb-stav-vyhlášení-aktu-položka"),
            }
        )

    normalized_versions.sort(
        key=lambda row: (
            row["effective_from"] is None,
            row["effective_from"] or "",
        )
    )

    timeline_rows: list[dict] = []
    for i, row in enumerate(normalized_versions):
        next_row = normalized_versions[i + 1] if i + 1 < len(normalized_versions) else None
        timeline_rows.append(
            {
                **row,
                "effective_to_exclusive": next_row["effective_from"] if next_row else None,
            }
        )

    dated_rows = [row for row in normalized_versions if row["effective_from"] is not None]

    payload = {
        "law_id": law_id,
        "official_name": item.get("akt-název-vyhlášený"),
        "collection_code": item.get("akt-sbírka-kód"),
        "law_iri": item.get("akt-iri"),
        "law_code": item.get("akt-kód"),
        "effective_from": dated_rows[0]["effective_from"] if dated_rows else None,
        "effective_to_exclusive": timeline_rows[-1]["effective_to_exclusive"] if timeline_rows else None,
        "first_known_effective_from": dated_rows[0]["effective_from"] if dated_rows else None,
        "last_known_effective_from": dated_rows[-1]["effective_from"] if dated_rows else None,
        "has_placeholder_version": any(row["effective_from"] is None for row in normalized_versions),
        "version_count": len(normalized_versions),
        "timeline": timeline_rows,
    }
    return law_id, payload


def build_law_timelines(input_path: str, output_path: str) -> dict[str, dict]:
    raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
    items = raw.get("položky")
    if not isinstance(items, list):
        raise SystemExit(f"Unexpected input structure in {input_path}: missing 'položky' list")

    result: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_law_timeline(item)
        if normalized is None:
            continue
        law_id, payload = normalized
        result[law_id] = payload

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build normalized law timeline metadata.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Raw e-Sbírka JSON path.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    args = parser.parse_args()

    result = build_law_timelines(args.input, args.output)
    print(f"Built law timelines: {len(result)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
