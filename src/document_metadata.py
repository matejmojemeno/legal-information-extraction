"""Load canonical document metadata used by the core pipeline.

This module provides the production-facing metadata loaders used by the thesis
system core. The main current use cases are:
- decision-date lookup for conservative temporal disambiguation
- access to canonical metadata rows keyed by source and document identity
- loading normalized law-timeline metadata
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from src.document_ids import canonical_document_lookup_key
from src.production_paths import (
    PRODUCTION_DATE_METADATA_PATH,
    PRODUCTION_LAW_TIMELINES_PATH,
)

DEFAULT_DOCUMENT_DATES_PATH = PRODUCTION_DATE_METADATA_PATH
DEFAULT_LAW_TIMELINES_PATH = PRODUCTION_LAW_TIMELINES_PATH


def _open_maybe_gzip(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return path.open(mode, encoding="utf-8")


class DocumentDatesIndex:
    def __init__(
        self,
        exact: dict[tuple[str, str], dict[str, Any]],
        canonical: dict[tuple[str, str], dict[str, Any] | None],
    ) -> None:
        self._exact = exact
        self._canonical = canonical

    def get(
        self,
        key: tuple[str, str],
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self._exact.get(key)
        if row is not None:
            return row

        if (
            isinstance(key, tuple)
            and len(key) == 2
            and isinstance(key[0], str)
            and isinstance(key[1], str)
        ):
            canonical_key = (key[0], canonical_document_lookup_key(key[0], key[1]))
            row = self._canonical.get(canonical_key)
            if row is not None:
                return row

        return default

    def __len__(self) -> int:
        return len(self._exact)

    def __bool__(self) -> bool:
        return bool(self._exact)


def load_document_dates_index(
    path: str = DEFAULT_DOCUMENT_DATES_PATH,
) -> DocumentDatesIndex:
    file_path = Path(path)
    if not file_path.exists():
        return DocumentDatesIndex({}, {})

    index: dict[tuple[str, str], dict[str, Any]] = {}
    canonical_index: dict[tuple[str, str], dict[str, Any] | None] = {}
    with _open_maybe_gzip(file_path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            source = row.get("source")
            document_id = row.get("document_id")
            if not isinstance(source, str) or not isinstance(document_id, str):
                continue
            key = (source, document_id)
            index[key] = row

            canonical_key = (source, canonical_document_lookup_key(source, document_id))
            existing = canonical_index.get(canonical_key)
            if existing is None and canonical_key in canonical_index:
                continue
            if existing is not None and existing != row:
                canonical_index[canonical_key] = None
            else:
                canonical_index[canonical_key] = row
    return DocumentDatesIndex(index, canonical_index)


def load_law_timelines(
    path: str = DEFAULT_LAW_TIMELINES_PATH,
) -> dict[str, dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))
