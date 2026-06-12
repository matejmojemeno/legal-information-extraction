"""Helpers shared by parent-project import scripts."""

from __future__ import annotations

from pathlib import Path


SOURCE_MAP = {
    "UOHS": "uohs",
    "NS": "ns",
    "NSS": "nss",
    "NALUS": "nalus",
    "US": "nalus",
}


def derive_repo_identity(blob_name: str) -> tuple[str | None, str | None]:
    """Map a parent bucket blob path to this repo's source/document_id shape.

    The parent storage layout is not uniform:
    - `NSS/230912.pdf`
    - `NALUS/1-1-01.pdf`
    - `NS/25 Cdo 3265/2025.pdf`

    For nested paths we flatten the path *after the source prefix* using `_` so
    document ids remain unique and filesystem-friendly.
    """
    path = Path(blob_name)
    parts = path.parts
    if len(parts) < 2:
        return None, None

    raw_source = parts[0].upper()
    source = SOURCE_MAP.get(raw_source, parts[0].lower())

    tail_parts = list(parts[1:])
    if not tail_parts:
        return source, None

    tail_parts[-1] = Path(tail_parts[-1]).with_suffix("").name
    flattened = "_".join(part.strip() for part in tail_parts if part.strip())
    if not flattened:
        return source, None

    return source, f"{flattened}.txt"
