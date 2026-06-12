"""Canonical document-identity helpers for production and legacy compatibility.

This module defines the naming and lookup conventions used to keep the thesis
pipeline on canonical full-corpus document identities while still recognizing
older legacy names when needed for migration or historical artifacts.
"""

from __future__ import annotations

import re
from pathlib import Path


_LEGACY_NALUS_PATTERN = re.compile(
    r"^GetText\.aspx_sz_(?P<body>.+?)(?P<suffix>\.txt)?$",
    re.IGNORECASE,
)
_LEGACY_NS_SUFFIX_PATTERN = re.compile(r"_openElement$", re.IGNORECASE)
_CANONICAL_NALUS_PATTERN = re.compile(
    r"^(?:(?P<chamber>\d+)-(?P<number>\d+)-(?P<year>\d{2,4})(?:_(?P<copy>\d+))?|"
    r"(?P<plenum>Pl|St)-(?P<pl_number>\d+)-(?P<pl_year>\d{2,4})(?:_(?P<pl_copy>\d+))?)$",
    re.IGNORECASE,
)


def canonical_document_filename(source: str, document_id: str) -> str:
    """Return the canonical production filename for a document id."""
    name = Path(document_id).name
    stem = Path(name).stem
    suffix = Path(name).suffix or ".txt"

    if source == "nalus":
        legacy_match = _LEGACY_NALUS_PATTERN.match(name)
        if legacy_match:
            return f"{legacy_match.group('body')}.txt"
        return f"{stem}{suffix}"

    if source == "ns":
        canonical_stem = _LEGACY_NS_SUFFIX_PATTERN.sub("", stem).strip()
        return f"{canonical_stem}{suffix}"

    return f"{stem}{suffix}"


def canonical_document_lookup_key(source: str, document_id: str) -> str:
    """Normalize document ids from legacy and canonical names to a shared key."""
    canonical_name = canonical_document_filename(source, document_id)
    stem = Path(canonical_name).stem

    if source == "ns":
        last_four_digit = None
        for match in re.finditer(r"\d{4}", stem):
            last_four_digit = match
        if last_four_digit is not None:
            stem = stem[: last_four_digit.end()]

    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def canonical_nalus_spisova_znacka(document_id: str) -> str | None:
    """Convert canonical or legacy NALUS filenames to a spisová značka body."""
    canonical_name = canonical_document_filename("nalus", document_id)
    stem = Path(canonical_name).stem
    match = _CANONICAL_NALUS_PATTERN.match(stem)
    if not match:
        return None

    if match.group("chamber"):
        chamber = int(match.group("chamber"))
        number = match.group("number")
        year = match.group("year")
        return f"{_int_to_roman(chamber)}. ÚS {number}/{year}"

    head = match.group("plenum")
    number = match.group("pl_number")
    year = match.group("pl_year")
    if not head or not number or not year:
        return None
    if head.lower() == "pl":
        return f"Pl. ÚS {number}/{year}"
    return f"Pl. ÚS-st. {number}/{year}"


def _int_to_roman(value: int) -> str:
    numerals = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out: list[str] = []
    remaining = value
    for arabic, roman in numerals:
        while remaining >= arabic:
            out.append(roman)
            remaining -= arabic
    return "".join(out)
