"""Core deterministic linker for document-to-document citation edges.

This module takes mined document-reference occurrences and maps them to
canonical corpus documents. It deliberately favors precision over recall:
- it links confidently identifiable in-corpus targets
- it builds self-identifier keys from stable local and metadata signals
- it keeps weaker proceeding-level behavior separate from exact links
- it leaves broader reporter/collection linking out of scope
"""

from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
import gzip
import json
from pathlib import Path
from typing import Callable

from src.document_ids import canonical_document_filename, canonical_nalus_spisova_znacka
from src.document_reference_extractor import (
    DocumentReferenceOccurrence,
    extract_document_references,
)


_STANDALONE_BARE_CASE_PATTERN = re.compile(
    r"(?im)^\s*(?P<body>("
    r"(?:Pl\.?\s*ÚS(?:-st\.)?|\b[IVXLCDM]+\.\s*ÚS)\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?|"
    r"\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?"
    r"))\s*$"
)

_UOHS_FILENAME_PATTERN = re.compile(r"^(?P<year>\d{4})_(?P<body>[A-Za-z0-9_-]+)$")
_UOHS_DECISION_TAIL_PATTERN = re.compile(
    r"(?:^|/)(?:VZ-)?(?P<tail>\d{1,6}/\d{2,4}/\d{1,6})$",
    re.IGNORECASE,
)
_UOHS_VZ_SHORT_DECISION_PATTERN = re.compile(
    r"^VZ/S(?P<number>\d{1,4})/(?P<proceeding_year>\d{2,4})-(?:\d{1,6}/)?(?P<decision_year>\d{2,4})(?:/\d+)?(?:[-/].*)?$",
    re.IGNORECASE,
)
_UOHS_BODY_SOURCE_PATTERN = re.compile(
    r"\b(?:UOHS|VZ/S|[SR]\s*\d{1,4}(?:[A-Z])?(?:,\d{1,4})*/\d{2,4})",
    re.IGNORECASE,
)
_NSS_AGENDA_CODES = {
    "A",
    "AD",
    "ADS",
    "AFS",
    "ANS",
    "AO",
    "AOS",
    "APS",
    "ARS",
    "AS",
    "AZS",
    "KOMP",
    "KSE",
    "KSS",
    "NAO",
    "NAD",
    "NTS",
    "PO",
    "PST",
    "VOL",
}
_NS_AGENDA_CODES = {
    "CDO",
    "CO",
    "CMO",
    "CPO",
    "CPJ",
    "ICDO",
    "ND",
    "NTD",
    "TCU",
    "TDO",
    "TO",
    "ZP",
}

_REPORTER_REFERENCE_PATTERN = re.compile(r"^\d{4}\s+a\s+\d+/\d{4}$", re.IGNORECASE)
_UOHS_R_REPORTER_PATTERN = re.compile(r"^R\s*\d+/\d{2,4}$", re.IGNORECASE)
_FOREIGN_CASE_PATTERN = re.compile(r"^(?:C|T|F)-\d+/\d{2,4}$", re.IGNORECASE)
_ADMIN_INTERNAL_MARKER_PATTERN = re.compile(
    r"\b(?:UMCP|MHMP|MZE|KSBR|LPTP|PRP|FJ|NK)\b|046100|RF-",
    re.IGNORECASE,
)
_ADMIN_INTERNAL_SHAPE_PATTERNS = (
    re.compile(r"^\d{3,6}/\d{2}$"),
    re.compile(r"^\d{1,6}/\d{1,6}/\d{2,4}$"),
    re.compile(r"^\d{1,6}/\d{4}/[A-Z]{2,5}$", re.IGNORECASE),
    re.compile(r"^\d+\s+EX\s+\d+/\d{2}-\d+$", re.IGNORECASE),
    re.compile(r"^\d+RP\d+/\d{4}-\d+$", re.IGNORECASE),
    re.compile(r"^[A-Z]-[A-Z]+\d+/\d{4}/[A-Z]{2,5}$", re.IGNORECASE),
    re.compile(r"^[A-Z]{2,}\d+/\d{2,4}(?:/[A-Z0-9-]+)+$", re.IGNORECASE),
)

@dataclass(frozen=True, slots=True)
class DocumentSelfIdentifier:
    document_id: str
    document_path: str
    source: str
    identifier_text: str
    identifier_kind: str
    origin: str
    keys: tuple[str, ...]
    source_iri: str | None = None
    decision_date: str | None = None
    decision_date_iso: str | None = None
    decision_year: int | None = None
    decision_date_precision: str | None = None
    judicate_name: str | None = None
    blob_name: str | None = None


@dataclass(frozen=True, slots=True)
class LinkedDocumentReference:
    source_document_source: str
    source_document_id: str
    source_document_path: str
    target_source: str
    target_document_id: str
    target_document_path: str
    reference_text: str
    reference_prefix: str
    reference_body: str
    reference_type: str
    raw_start: int
    raw_end: int
    link_key: str
    link_method: str
    target_match_scope: str
    target_proceeding_key: str | None
    target_group_size: int | None
    decision_kind_hint: str | None
    court_hint: str | None

    def to_dict(self) -> dict:
        return {
            "source_document_source": self.source_document_source,
            "source_document_id": self.source_document_id,
            "source_document_path": self.source_document_path,
            "target_source": self.target_source,
            "target_document_id": self.target_document_id,
            "target_document_path": self.target_document_path,
            "reference_text": self.reference_text,
            "reference_prefix": self.reference_prefix,
            "reference_body": self.reference_body,
            "reference_type": self.reference_type,
            "raw_start": self.raw_start,
            "raw_end": self.raw_end,
            "link_key": self.link_key,
            "link_method": self.link_method,
            "target_match_scope": self.target_match_scope,
            "target_proceeding_key": self.target_proceeding_key,
            "target_group_size": self.target_group_size,
            "decision_kind_hint": self.decision_kind_hint,
            "court_hint": self.court_hint,
        }


def _ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _compact_key(text: str) -> str:
    folded = _ascii_fold(text)
    folded = (
        folded.replace("–", "-")
        .replace("—", "-")
        .replace("_", "/")
        .replace("\\", "/")
    )
    folded = folded.upper()
    return re.sub(r"[^A-Z0-9]+", "", folded)


def _uohs_relaxed_key(text: str) -> str:
    folded = _ascii_fold(text).upper()
    folded = folded.replace("–", "-").replace("—", "-").replace("_", "/")
    folded = re.sub(r"\s+", "", folded)
    folded = re.sub(r"^UOHS-?", "", folded)
    parts = [part for part in re.split(r"[^A-Z0-9]+", folded) if part]
    while parts and parts[-1].isalpha() and len(parts[-1]) <= 4:
        parts.pop()
    return "".join(parts)


def _normalize_uohs_year(year_text: str) -> str:
    if len(year_text) == 4:
        return year_text
    year_value = int(year_text)
    century = "20" if year_value <= 30 else "19"
    return f"{century}{year_text}"


def _normalize_uohs_numbers(numbers_text: str) -> str:
    parts = re.split(r"\s*,\s*", numbers_text.strip())
    normalized_parts = [str(int(part)) for part in parts if part.strip()]
    return ",".join(normalized_parts)


def _uohs_root_body(text: str) -> str | None:
    folded = _ascii_fold(text).upper()
    folded = folded.replace("–", "-").replace("—", "-").replace("_", "/")
    folded = re.sub(r"\s+", "", folded)
    folded = re.sub(r"^UOHS-?", "", folded)
    folded = re.sub(r"^VZ/", "", folded)

    appeal_match = re.match(
        r"^S(?P<number>\d{1,4}(?:,\d{1,4})*)-R/(?P<year>\d{2,4})(?:[-/].*)?$",
        folded,
    )
    if appeal_match:
        number = _normalize_uohs_numbers(appeal_match.group("number"))
        year = _normalize_uohs_year(appeal_match.group("year"))
        return f"S{number}/{year}"

    standard_match = re.match(
        r"^(?P<letter>[SR])(?P<number>\d{1,4}(?:,\d{1,4})*)/(?P<year>\d{2,4})(?:/(?P<agenda>[A-Z]{2,3}))?(?:[-/].*)?$",
        folded,
    )
    if standard_match:
        letter = standard_match.group("letter")
        number = _normalize_uohs_numbers(standard_match.group("number"))
        year = _normalize_uohs_year(standard_match.group("year"))
        return f"{letter}{number}/{year}"

    return None


def _uohs_decision_suffix_body(text: str) -> str | None:
    folded = _ascii_fold(text).upper()
    folded = folded.replace("–", "-").replace("—", "-").replace("_", "/")
    folded = re.sub(r"\s+", "", folded)
    folded = re.sub(r"^UOHS-?", "", folded)

    match = _UOHS_DECISION_TAIL_PATTERN.search(folded)
    if not match:
        return None
    return f"UOHS-{match.group('tail')}"


def _uohs_short_vz_decision_body(text: str) -> str | None:
    folded = _ascii_fold(text).upper()
    folded = folded.replace("–", "-").replace("—", "-").replace("_", "/")
    folded = re.sub(r"\s+", "", folded)

    match = _UOHS_VZ_SHORT_DECISION_PATTERN.fullmatch(folded)
    if not match:
        return None

    number = str(int(match.group("number")))
    decision_year = _normalize_uohs_year(match.group("decision_year"))[-2:]
    return f"VZ/S{number}/{decision_year}"


def _is_uohs_root_like_reference(text: str) -> bool:
    folded = _ascii_fold(text).upper()
    folded = folded.replace("–", "-").replace("—", "-").replace("_", "/")
    folded = re.sub(r"\s+", "", folded)
    folded = re.sub(r"^UOHS-?", "", folded)
    return bool(
        re.fullmatch(
            r"(?:"
            r"S\d{1,4}(?:,\d{1,4})*/\d{2,4}(?:/[A-Z0-9]{2,4})?|"
            r"S\d{1,4}(?:,\d{1,4})*-R/\d{2,4}|"
            r"R\d{1,4}(?:,\d{1,4})*/\d{2,4}(?:/[A-Z0-9]{2,4})?"
            r")",
            folded,
        )
    )


def _ns_filename_body(stem: str) -> str | None:
    base = re.sub(r"_openElement$", "", stem, flags=re.IGNORECASE).strip()
    match = re.match(r"^(?P<head>.+?)\s+(?P<number>\d+)_(?P<year>\d{4})$", base)
    if not match:
        return None
    head = re.sub(r"\s+", " ", match.group("head")).strip()
    number = match.group("number")
    year = match.group("year")
    return f"{head} {number}/{year}"


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


def _nalus_filename_body(stem: str) -> str | None:
    return canonical_nalus_spisova_znacka(f"{stem}.txt")


def _nalus_parent_name_bodies(text: str) -> list[str]:
    match = re.fullmatch(r"(?P<chamber>\d+)-(?P<number>\d+)-(?P<year>\d{2,4})(?:_\d+)?", text)
    if match:
        chamber = int(match.group("chamber"))
        if chamber >= 1:
            return [f"{_int_to_roman(chamber)}. ÚS {match.group('number')}/{match.group('year')}"]

    match = re.fullmatch(r"Pl-(?P<number>\d+)-(?P<year>\d{2,4})(?:_\d+)?", text, re.IGNORECASE)
    if match:
        return [f"Pl. ÚS {match.group('number')}/{match.group('year')}"]

    match = re.fullmatch(r"St-(?P<number>\d+)-(?P<year>\d{2,4})(?:_\d+)?", text, re.IGNORECASE)
    if match:
        return [f"Pl. ÚS-st. {match.group('number')}/{match.group('year')}"]

    return []


def _nalus_chamberless_body(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    patterns = [
        r"(?:[IVXLCDM]+\.\s*)?ÚS\s+(?P<number>\d+)/(?P<year>\d{2,4})(?:\s*[-–—]\s*\d+)?",
        r"Pl\.?\s*ÚS(?:-st\.)?\s+(?P<number>\d+)/(?P<year>\d{2,4})(?:\s*[-–—]\s*\d+)?",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, normalized, re.IGNORECASE)
        if match:
            return f"ÚS {int(match.group('number'))}/{match.group('year')[-2:]}"
    return None


def _nalus_reference_without_chamber_body(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    match = re.fullmatch(
        r"ÚS\s+(?P<number>\d+)/(?P<year>\d{2,4})(?:\s*[-–—]\s*\d+)?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    return f"ÚS {int(match.group('number'))}/{match.group('year')[-2:]}"


def _nalus_short_year_body(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    patterns = [
        (
            r"(?P<head>(?:[IVXLCDM]+\.\s*)?ÚS)\s+(?P<number>\d+)/(?P<year>\d{4})(?P<tail>(?:\s*[-–—]\s*\d+)?)",
            "{head} {number}/{year}{tail}",
        ),
        (
            r"(?P<head>Pl\.?\s*ÚS(?:-st\.)?)\s+(?P<number>\d+)/(?P<year>\d{4})(?P<tail>(?:\s*[-–—]\s*\d+)?)",
            "{head} {number}/{year}{tail}",
        ),
    ]
    for pattern, template in patterns:
        match = re.fullmatch(pattern, normalized, re.IGNORECASE)
        if match:
            return template.format(
                head=match.group("head"),
                number=int(match.group("number")),
                year=match.group("year")[-2:],
                tail=match.group("tail") or "",
            )
    return None


def _body_without_decision_tail(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    patterns = [
        r"(?P<base>\d+\s+[A-Za-zÁ-Ž]{1,12}\s+\d+/\d{2,4})\s*[-–—]\s*\d+",
        r"(?P<base>(?:[IVXLCDM]+\.\s*)?ÚS\s+\d+/\d{2,4})\s*[-–—]\s*\d+",
        r"(?P<base>Pl\.?\s*ÚS(?:-st\.)?\s+\d+/\d{2,4})\s*[-–—]\s*\d+",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, normalized, re.IGNORECASE)
        if match:
            return match.group("base")
    return None


def _uohs_filename_bodies(stem: str) -> list[str]:
    match = _UOHS_FILENAME_PATTERN.match(stem)
    if not match:
        return []
    year = match.group("year")
    raw_body = match.group("body")
    if raw_body.lower().startswith("pis"):
        return []

    bodies: list[str] = []
    split_parts = [part for part in re.split(r"[-_]", raw_body) if part]
    if split_parts and re.fullmatch(r"[A-Za-z]\d+", split_parts[0]):
        letter = split_parts[0][0]
        number_parts = [split_parts[0][1:]]
        number_parts.extend(part for part in split_parts[1:] if part.isdigit())
        if number_parts:
            return [f"{letter}{part}/{year}" for part in number_parts]

    bodies.append(f"{raw_body}/{year}")
    return bodies


def _uohs_merged_component_bodies_from_document_id(document_id: str) -> list[str]:
    stem = Path(document_id).stem
    bodies = _uohs_filename_bodies(stem)
    return bodies if len(bodies) > 1 else []


def _repeated_header_bodies(text: str) -> list[str]:
    counts: Counter[str] = Counter()
    bodies_by_key: dict[str, str] = {}
    for match in _STANDALONE_BARE_CASE_PATTERN.finditer(text):
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        key = _compact_key(body)
        counts[key] += 1
        bodies_by_key.setdefault(key, body)
    return [bodies_by_key[key] for key, count in counts.items() if count >= 2]


def _identifier_keys(identifier_text: str, source: str) -> set[str]:
    keys = {_compact_key(identifier_text)}
    without_tail = _body_without_decision_tail(identifier_text)
    if without_tail:
        keys.add(_compact_key(without_tail))
    if source == "nalus":
        chamberless = _nalus_chamberless_body(identifier_text)
        if chamberless:
            keys.add(_compact_key(chamberless))
        short_year = _nalus_short_year_body(identifier_text)
        if short_year:
            keys.add(_compact_key(short_year))
    if source == "uohs":
        relaxed = _uohs_relaxed_key(identifier_text)
        if relaxed:
            keys.add(relaxed)
        root_body = _uohs_root_body(identifier_text)
        if root_body:
            keys.add(_compact_key(root_body))
        decision_suffix_body = _uohs_decision_suffix_body(identifier_text)
        if decision_suffix_body:
            decision_suffix_key = _uohs_relaxed_key(decision_suffix_body)
            if decision_suffix_key:
                keys.add(decision_suffix_key)
        short_vz_decision_body = _uohs_short_vz_decision_body(identifier_text)
        if short_vz_decision_body:
            keys.add(_compact_key(short_vz_decision_body))
    return {key for key in keys if key}


def extract_document_self_identifiers(document_path: str, text: str) -> list[DocumentSelfIdentifier]:
    path = Path(document_path)
    source = path.parent.name
    document_id = path.name

    identifiers: list[DocumentSelfIdentifier] = []
    seen: set[tuple[str, str]] = set()

    def add_identifier(identifier_text: str, identifier_kind: str, origin: str) -> None:
        normalized_text = re.sub(r"\s+", " ", identifier_text).strip()
        if not normalized_text:
            return
        key_tuple = tuple(sorted(_identifier_keys(normalized_text, source)))
        if not key_tuple:
            return
        dedupe_key = (origin, normalized_text)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        identifiers.append(
            DocumentSelfIdentifier(
                document_id=document_id,
                document_path=str(path.resolve()),
                source=source,
                identifier_text=normalized_text,
                identifier_kind=identifier_kind,
                origin=origin,
                keys=key_tuple,
                source_iri=None,
            )
        )

    stem = path.stem
    if source == "ns":
        body = _ns_filename_body(stem)
        if body:
            add_identifier(body, "spisova_znacka", "filename_ns")
    elif source == "nalus":
        body = _nalus_filename_body(stem)
        if body:
            add_identifier(body, "spisova_znacka", "filename_nalus")
    elif source == "uohs":
        for body in _uohs_filename_bodies(stem):
            add_identifier(body, "spisova_znacka", "filename_uohs")

    for body in _repeated_header_bodies(text):
        add_identifier(body, "cislo_jednaci", "repeated_header_line")

    return identifiers


def _open_jsonl_maybe_gzip(path: str):
    jsonl_path = Path(path)
    if jsonl_path.suffix == ".gz":
        return gzip.open(jsonl_path, "rt", encoding="utf-8")
    return jsonl_path.open("r", encoding="utf-8")


def _resolve_document_path_from_row(
    source: str,
    document_id: str,
    processed_rel_path: str | None,
    processed_root: Path,
) -> Path:
    direct_candidate = (processed_root / source / document_id).resolve()
    if not processed_rel_path:
        return direct_candidate

    rel_path = Path(processed_rel_path)
    if rel_path.is_absolute():
        return rel_path.resolve()

    candidates = [direct_candidate, (processed_root / rel_path).resolve()]
    if processed_root.name == "processed" and len(processed_root.parents) >= 2:
        candidates.append((processed_root.parents[1] / rel_path).resolve())

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return direct_candidate


def _external_identifier_variants(
    source: str,
    identifier_text: str,
    origin: str,
) -> list[tuple[str, str, str]]:
    normalized_text = re.sub(r"\s+", " ", identifier_text).strip()
    if not normalized_text:
        return []

    variants = [(normalized_text, "unknown", origin)]

    if source == "nalus" and origin in {"parent_metadata_name", "external_name"}:
        for body in _nalus_parent_name_bodies(normalized_text):
            variants.append((body, "spisova_znacka", f"{origin}_normalized_nalus"))

    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for text_variant, identifier_kind, variant_origin in variants:
        key = (text_variant, identifier_kind, variant_origin)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _load_external_self_identifiers(
    metadata_path: str,
    processed_root: str,
) -> list[DocumentSelfIdentifier]:
    processed_root_path = Path(processed_root).resolve()
    identifiers: list[DocumentSelfIdentifier] = []
    seen: set[tuple[str, str, str, str]] = set()

    with _open_jsonl_maybe_gzip(metadata_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)

            source = row.get("source")
            document_id = row.get("document_id")
            if not source or not document_id:
                continue

            processed_rel_path = row.get("processed_rel_path")
            document_path = _resolve_document_path_from_row(
                source=source,
                document_id=document_id,
                processed_rel_path=processed_rel_path,
                processed_root=processed_root_path,
            )

            candidate_fields = [
                ("self_identifier_text", "external_self_identifier"),
                ("identifier_text", "external_identifier"),
                ("judicate_name", "parent_metadata_name"),
                ("name", "external_name"),
            ]
            for field_name, origin in candidate_fields:
                identifier_text = row.get(field_name)
                if not isinstance(identifier_text, str):
                    continue
                row_identifier_kind = row.get("identifier_kind")
                if not isinstance(row_identifier_kind, str) or not row_identifier_kind:
                    row_identifier_kind = "unknown"

                for text_variant, variant_kind, variant_origin in _external_identifier_variants(
                    source=source,
                    identifier_text=identifier_text,
                    origin=origin,
                ):
                    key_tuple = tuple(sorted(_identifier_keys(text_variant, source)))
                    if not key_tuple:
                        continue

                    dedupe_key = (
                        str(document_path),
                        text_variant,
                        variant_origin,
                        row.get("judicate_iri") or row.get("iri") or "",
                    )
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)

                    identifier_kind = variant_kind if variant_kind != "unknown" else row_identifier_kind
                    identifiers.append(
                        DocumentSelfIdentifier(
                            document_id=document_id,
                            document_path=str(document_path),
                            source=source,
                            identifier_text=text_variant,
                            identifier_kind=identifier_kind,
                            origin=variant_origin,
                            keys=key_tuple,
                            source_iri=row.get("judicate_iri") or row.get("iri"),
                            decision_date=row.get("decision_date"),
                            decision_date_iso=row.get("decision_date_iso"),
                            decision_year=row.get("decision_year"),
                            decision_date_precision=row.get("decision_date_precision"),
                            judicate_name=row.get("judicate_name"),
                            blob_name=row.get("blob_name"),
                        )
                    )

    return identifiers


def build_document_self_id_index(
    document_paths: list[str],
    external_metadata_path: str | None = None,
    processed_root: str | None = None,
    *,
    collect_all_identifiers: bool = True,
    identifier_sink: Callable[[DocumentSelfIdentifier], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, list[DocumentSelfIdentifier]], list[DocumentSelfIdentifier]]:
    index: dict[str, list[DocumentSelfIdentifier]] = defaultdict(list)
    all_identifiers: list[DocumentSelfIdentifier] = []

    total_documents = len(document_paths)
    for idx, document_path in enumerate(document_paths, start=1):
        with open(document_path, "r", encoding="utf-8") as f:
            text = f.read()
        identifiers = extract_document_self_identifiers(document_path, text)
        for identifier in identifiers:
            if collect_all_identifiers:
                all_identifiers.append(identifier)
            if identifier_sink is not None:
                identifier_sink(identifier)
            for key in identifier.keys:
                index[key].append(identifier)
        if progress_callback is not None:
            progress_callback(idx, total_documents)

    if external_metadata_path:
        if processed_root:
            root_for_external = processed_root
        elif document_paths:
            root_for_external = str(Path(document_paths[0]).resolve().parents[1])
        else:
            raise ValueError("processed_root is required when loading external self-id metadata without local documents.")
        external_identifiers = _load_external_self_identifiers(
            metadata_path=external_metadata_path,
            processed_root=root_for_external,
        )
        for identifier in external_identifiers:
            if collect_all_identifiers:
                all_identifiers.append(identifier)
            if identifier_sink is not None:
                identifier_sink(identifier)
            for key in identifier.keys:
                index[key].append(identifier)

    return dict(index), all_identifiers


def _reference_link_keys(reference: DocumentReferenceOccurrence) -> list[tuple[str, str]]:
    body = reference.reference_body or reference.reference_text
    normalized_body = body.strip()
    if not normalized_body or normalized_body.endswith(("-", "–", "—", "/", ":")):
        return []
    keys: list[tuple[str, str]] = []

    strict = _compact_key(normalized_body)
    if strict:
        keys.append(("body_exact", strict))

    chamberless_nalus = _nalus_reference_without_chamber_body(normalized_body)
    if chamberless_nalus:
        chamberless_key = _compact_key(chamberless_nalus)
        if chamberless_key and chamberless_key not in {key for _, key in keys}:
            keys.append(("body_nalus_without_chamber", chamberless_key))

    short_year_nalus = _nalus_short_year_body(normalized_body)
    if short_year_nalus:
        short_year_key = _compact_key(short_year_nalus)
        if short_year_key and short_year_key not in {key for _, key in keys}:
            keys.append(("body_nalus_short_year", short_year_key))

    relaxed_uohs = _uohs_relaxed_key(normalized_body)
    if relaxed_uohs and relaxed_uohs != strict:
        keys.append(("body_relaxed_uohs", relaxed_uohs))

    decision_suffix_body = _uohs_decision_suffix_body(normalized_body)
    if decision_suffix_body:
        decision_suffix_key = _uohs_relaxed_key(decision_suffix_body)
        if decision_suffix_key and decision_suffix_key not in {key for _, key in keys}:
            keys.append(("body_uohs_decision_suffix", decision_suffix_key))

    short_vz_decision_body = _uohs_short_vz_decision_body(normalized_body)
    if short_vz_decision_body:
        short_vz_decision_key = _compact_key(short_vz_decision_body)
        if short_vz_decision_key and short_vz_decision_key not in {key for _, key in keys}:
            keys.append(("body_uohs_vz_short_decision", short_vz_decision_key))

    uohs_root_body = _uohs_root_body(normalized_body)
    if uohs_root_body and _is_uohs_root_like_reference(normalized_body):
        root_key = _compact_key(uohs_root_body)
        if root_key and root_key not in {key for _, key in keys}:
            keys.append(("body_uohs_root", root_key))

    if uohs_root_body and not _is_uohs_root_like_reference(normalized_body):
        proceeding_key = _compact_key(uohs_root_body)
        if proceeding_key and proceeding_key not in {key for _, key in keys}:
            keys.append(("body_uohs_same_proceeding", proceeding_key))

    return keys


def _canonical_document_group_id(identifier: DocumentSelfIdentifier) -> str:
    if identifier.source == "nalus":
        canonical_spis = canonical_nalus_spisova_znacka(identifier.document_id)
        if canonical_spis:
            return _compact_key(canonical_spis).lower()
    return Path(canonical_document_filename(identifier.source, identifier.document_id)).stem.lower()


def _candidate_preference(identifier: DocumentSelfIdentifier) -> tuple[int, int, int, str]:
    stem = Path(identifier.document_id).stem
    has_iri = 0 if identifier.source_iri else 1
    is_gettext = 1 if stem.lower().startswith("gettext.aspx_sz_") else 0
    is_open_element = 1 if stem.lower().endswith("_openelement") else 0
    nalus_copy_match = re.search(r"_(\d+)$", stem) if identifier.source == "nalus" else None
    nalus_copy_preference = -int(nalus_copy_match.group(1)) if nalus_copy_match else 0
    canonical_name = canonical_document_filename(identifier.source, identifier.document_id).lower()
    return (has_iri, is_gettext, is_open_element, nalus_copy_preference, canonical_name)


def _normalize_fuzzy_text(text: str) -> str:
    folded = re.sub(r"\s+", " ", _ascii_fold(text)).strip().upper()
    return (
        folded.replace("–", "-")
        .replace("—", "-")
        .replace("_", "/")
        .replace("\\", "/")
    )


def _digit_groups(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d{1,6}", text))


def _year_groups(text: str) -> tuple[str, ...]:
    return tuple(group for group in _digit_groups(text) if len(group) in {2, 4})


def _agenda_code_from_body(body: str) -> str | None:
    normalized = _normalize_fuzzy_text(body)
    match = re.search(r"\b\d+\s+([A-Z]{1,8})\s+\d+/\d{2,4}\b", normalized)
    if not match:
        return None
    return match.group(1)


def _preferred_sources_for_reference(reference: DocumentReferenceOccurrence) -> tuple[str, ...]:
    body = _normalize_fuzzy_text(reference.reference_body)
    court_hint = (reference.court_hint or "").lower()

    if any(token in court_hint for token in ("vrchní", "krajsk", "městsk", "mestsk", "okresn")):
        return ()
    if "ústav" in court_hint or re.search(r"\b(?:PL\.?\s*ÚS(?:-ST\.)?|[IVXLCDM]+\.\s*ÚS|ÚS)\b", body):
        return ("nalus",)
    if "správní" in court_hint:
        return ("nss",)
    if "nejvyššího soudu" in court_hint:
        return ("ns",)
    if "úřadu" in court_hint or "UOHS" in body or body.startswith(("VZ/S", "S", "R")):
        if _UOHS_BODY_SOURCE_PATTERN.search(body):
            return ("uohs",)

    agenda = _agenda_code_from_body(reference.reference_body)
    if agenda in _NSS_AGENDA_CODES:
        return ("nss",)
    if agenda in _NS_AGENDA_CODES:
        return ("ns",)
    if re.search(r"\b(?:PL\.?\s*ÚS(?:-ST\.)?|[IVXLCDM]+\.\s*ÚS|ÚS)\b", body):
        return ("nalus",)
    if _UOHS_BODY_SOURCE_PATTERN.search(body):
        return ("uohs",)
    if agenda:
        return ("nss", "ns")
    return ()


def build_fuzzy_identifier_buckets(
    identifiers: list[DocumentSelfIdentifier],
) -> dict[str, list[dict[str, object]]]:
    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for identifier in identifiers:
        normalized = _normalize_fuzzy_text(identifier.identifier_text)
        digits = _digit_groups(normalized)
        if not normalized or not digits:
            continue
        buckets[identifier.source].append(
            {
                "identifier": identifier,
                "normalized": normalized,
                "digits": digits,
                "years": _year_groups(normalized),
            }
        )
    return dict(buckets)


def _fuzzy_score_reference_to_identifier(
    reference: DocumentReferenceOccurrence,
    candidate_row: dict[str, object],
) -> float:
    ref_text = _normalize_fuzzy_text(reference.reference_body)
    if not ref_text:
        return 0.0

    ref_digits = set(_digit_groups(ref_text))
    cand_digits = set(candidate_row.get("digits") or ())
    shared_digits = ref_digits & cand_digits
    if not shared_digits:
        return 0.0

    ref_years = set(_year_groups(ref_text))
    cand_years = set(candidate_row.get("years") or ())
    if ref_years and cand_years and not (ref_years & cand_years):
        return 0.0

    numeric_overlap = len(shared_digits) / max(1, len(ref_digits))
    if len(ref_digits) >= 2 and numeric_overlap < 0.5:
        return 0.0

    ratio = SequenceMatcher(None, ref_text, str(candidate_row["normalized"])).ratio()
    score = ratio
    score += min(0.12, 0.04 * len(shared_digits))
    if ref_years & cand_years:
        score += 0.05
    identifier = candidate_row["identifier"]
    if isinstance(identifier, DocumentSelfIdentifier) and identifier.identifier_kind == reference.reference_type:
        score += 0.03

    if len(ref_text) <= 12 and ratio < 0.92:
        return 0.0
    if len(ref_text) <= 18 and score < 0.83:
        return 0.0
    if score < 0.74:
        return 0.0
    return score


def _candidate_target_row(
    candidate: DocumentSelfIdentifier,
    *,
    match_method: str,
    match_key: str | None = None,
    candidate_retrieval: str = "deterministic",
    candidate_score: float | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "target_document_id": candidate.document_id,
        "target_document_path": candidate.document_path,
        "target_source": candidate.source,
        "target_identifier": candidate.identifier_text,
        "target_identifier_kind": candidate.identifier_kind,
        "target_origin": candidate.origin,
        "target_source_iri": candidate.source_iri,
        "match_method": match_method,
    }
    if candidate.decision_date:
        row["target_decision_date"] = candidate.decision_date
    if candidate.decision_date_iso:
        row["target_decision_date_iso"] = candidate.decision_date_iso
    if candidate.decision_year is not None:
        row["target_decision_year"] = candidate.decision_year
    if candidate.decision_date_precision:
        row["target_decision_date_precision"] = candidate.decision_date_precision
    if candidate.judicate_name:
        row["target_judicate_name"] = candidate.judicate_name
    if candidate.blob_name:
        row["target_blob_name"] = candidate.blob_name
    if match_key:
        row["match_key"] = match_key
    merged_bodies = _uohs_merged_component_bodies_from_document_id(candidate.document_id)
    if merged_bodies:
        row["target_merged_case_bodies"] = merged_bodies
    if candidate_retrieval != "deterministic":
        row["candidate_retrieval"] = candidate_retrieval
    if candidate_score is not None:
        row["candidate_score"] = round(min(candidate_score, 1.0), 4)
    return row


def _add_duplicate_candidate_hints(
    candidate_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in candidate_rows:
        source = str(row.get("target_source") or "")
        identifier = str(row.get("target_identifier") or "")
        date = str(row.get("target_decision_date_iso") or row.get("target_decision_date") or "")
        judicate_name = str(row.get("target_judicate_name") or "")
        identifier_key = _compact_key(judicate_name or identifier)
        if not source or not identifier_key or not date:
            continue
        grouped[(source, identifier_key, date, judicate_name or identifier)].append(row)

    for group_rows in grouped.values():
        document_ids = sorted({
            str(row.get("target_document_id") or "")
            for row in group_rows
            if row.get("target_document_id")
        })
        if len(document_ids) <= 1:
            continue
        canonical_id = min(
            document_ids,
            key=lambda document_id: canonical_document_filename(
                str(group_rows[0].get("target_source") or ""),
                document_id,
            ).lower(),
        )
        for row in group_rows:
            row["target_duplicate_group_size"] = len(document_ids)
            row["target_duplicate_document_ids"] = document_ids
            row["target_duplicate_canonical_document_id"] = canonical_id
            row["target_is_duplicate_canonical"] = (
                row.get("target_document_id") == canonical_id
            )
    return candidate_rows


def _dedupe_candidate_target_rows(
    candidate_rows: list[dict[str, object]],
    max_candidates: int,
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in sorted(candidate_rows, key=lambda item: item["_pref"]):
        group_id = str(row.get("_group_id") or "")
        current = grouped.get(group_id)
        if current is None or row["_pref"] < current["_pref"]:
            grouped[group_id] = row
    deduped = [
        {k: v for k, v in row.items() if k not in {"_pref", "_group_id"}}
        for row in sorted(grouped.values(), key=lambda item: item["_pref"])[:max_candidates]
    ]
    return _add_duplicate_candidate_hints(deduped)


def build_reference_candidate_targets(
    *,
    reference: DocumentReferenceOccurrence,
    source_document_path: str,
    source_document_source: str = "",
    self_id_index: dict[str, list[DocumentSelfIdentifier]],
    all_identifiers: list[DocumentSelfIdentifier] | None = None,
    fuzzy_buckets: dict[str, list[dict[str, object]]] | None = None,
    max_candidates: int = 12,
    use_fuzzy_candidates: bool = True,
) -> dict[str, object]:
    source_path_resolved = str(Path(source_document_path).resolve())
    scope_filter_reason = _scope_filter_reason(
        reference=reference,
        source_document_source=source_document_source,
        source_document_path=source_path_resolved,
        self_id_index=self_id_index,
    )
    if scope_filter_reason is not None:
        return {
            "candidate_targets": [],
            "llm_route": "filtered_out_of_scope",
            "filtered_reason": scope_filter_reason,
        }

    candidate_rows: dict[tuple[str, str], dict[str, object]] = {}
    group_ids: set[str] = set()

    for match_method, key in _reference_link_keys(reference):
        for candidate in self_id_index.get(key, []):
            if candidate.document_path == source_path_resolved:
                continue
            row_key = (candidate.source, candidate.document_id)
            row = _candidate_target_row(candidate, match_method=match_method, match_key=key)
            row["_pref"] = _candidate_preference(candidate)
            row["_group_id"] = _canonical_document_group_id(candidate)
            current = candidate_rows.get(row_key)
            if current is None or row["_pref"] < current["_pref"]:
                candidate_rows[row_key] = row
            group_ids.add(_canonical_document_group_id(candidate))

    candidate_target_rows = list(candidate_rows.values())
    candidate_targets = _dedupe_candidate_target_rows(
        candidate_target_rows,
        max_candidates,
    )

    if use_fuzzy_candidates and not candidate_targets and all_identifiers is not None:
        preferred_sources = _preferred_sources_for_reference(reference)
        if preferred_sources and len(_normalize_fuzzy_text(reference.reference_body)) >= 8:
            buckets = fuzzy_buckets or build_fuzzy_identifier_buckets(all_identifiers)
            scored_rows: list[tuple[float, DocumentSelfIdentifier]] = []
            for source in preferred_sources:
                for candidate_row in buckets.get(source, []):
                    identifier = candidate_row["identifier"]
                    if not isinstance(identifier, DocumentSelfIdentifier):
                        continue
                    if identifier.document_path == source_path_resolved:
                        continue
                    score = _fuzzy_score_reference_to_identifier(reference, candidate_row)
                    if score > 0.0:
                        scored_rows.append((score, identifier))
            scored_rows.sort(key=lambda item: (-item[0], _candidate_preference(item[1])))
            seen_documents: set[tuple[str, str]] = set()
            for score, identifier in scored_rows:
                row_key = (identifier.source, identifier.document_id)
                if row_key in seen_documents:
                    continue
                seen_documents.add(row_key)
                candidate_target_rows.append(
                    {
                        **_candidate_target_row(
                            identifier,
                            match_method="fuzzy_identifier_similarity",
                            candidate_retrieval="fuzzy",
                            candidate_score=score,
                        ),
                        "_pref": _candidate_preference(identifier),
                        "_group_id": _canonical_document_group_id(identifier),
                    }
                )
                group_ids.add(_canonical_document_group_id(identifier))
                candidate_targets = _dedupe_candidate_target_rows(candidate_target_rows, max_candidates)
                if len(candidate_targets) >= max_candidates:
                    break

    if candidate_targets:
        llm_route = "link_disambiguation" if len(group_ids) > 1 else "link_normalization_or_target_recovery"
    else:
        llm_route = "extraction_presence_check"

    return {
        "candidate_targets": candidate_targets,
        "llm_route": llm_route,
    }


def _uohs_proceeding_key(identifier: DocumentSelfIdentifier) -> str | None:
    if identifier.source != "uohs":
        return None
    root_body = _uohs_root_body(identifier.identifier_text)
    if not root_body:
        return None
    return _compact_key(root_body)


@lru_cache(maxsize=512)
def _uohs_source_proceeding_keys(source_document_path: str) -> frozenset[str]:
    path = Path(source_document_path)
    if not path.exists():
        return frozenset()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()

    keys = {
        proceeding_key
        for identifier in extract_document_self_identifiers(str(path), text)
        if identifier.source == "uohs"
        for proceeding_key in [_uohs_proceeding_key(identifier)]
        if proceeding_key
    }
    return frozenset(keys)


def _scope_filter_reason(
    *,
    reference: DocumentReferenceOccurrence,
    source_document_source: str,
    source_document_path: str,
    self_id_index: dict[str, list[DocumentSelfIdentifier]] | None = None,
) -> str | None:
    normalized_body = re.sub(r"\s+", " ", reference.reference_body or "").strip()
    if not normalized_body:
        return None

    if _REPORTER_REFERENCE_PATTERN.fullmatch(normalized_body):
        return "reporter_citation"

    ascii_body = _ascii_fold(normalized_body).upper().replace("–", "-").replace("—", "-")
    if _FOREIGN_CASE_PATTERN.fullmatch(ascii_body):
        return "foreign_case_out_of_scope"

    if _UOHS_R_REPORTER_PATTERN.fullmatch(normalized_body):
        return "uohs_reporter_citation"

    if _ADMIN_INTERNAL_MARKER_PATTERN.search(ascii_body):
        return "administrative_internal_identifier"

    if any(pattern.fullmatch(normalized_body) for pattern in _ADMIN_INTERNAL_SHAPE_PATTERNS):
        return "administrative_internal_identifier"

    if source_document_source == "uohs":
        proceeding_body = _uohs_root_body(normalized_body)
        if proceeding_body:
            proceeding_key = _compact_key(proceeding_body)
            source_proceeding_keys = set(_uohs_source_proceeding_keys(source_document_path))
            if self_id_index is not None:
                relevant_keys = {proceeding_key}
                relevant_keys.update(key for _, key in _reference_link_keys(reference) if key)
                for key in relevant_keys:
                    for candidate in self_id_index.get(key, []):
                        if candidate.document_path != source_document_path:
                            continue
                        source_candidate_proceeding = _uohs_proceeding_key(candidate)
                        if source_candidate_proceeding:
                            source_proceeding_keys.add(source_candidate_proceeding)
            if proceeding_key and proceeding_key in source_proceeding_keys:
                return "uohs_self_reference"

    return None


def _build_linked_reference(
    *,
    source_document_source: str,
    source_document_id: str,
    source_path_resolved: str,
    reference: DocumentReferenceOccurrence,
    target: DocumentSelfIdentifier,
    key: str,
    link_method: str,
    target_match_scope: str,
    target_proceeding_key: str | None,
    target_group_size: int | None,
) -> LinkedDocumentReference:
    return LinkedDocumentReference(
        source_document_source=source_document_source,
        source_document_id=source_document_id,
        source_document_path=source_path_resolved,
        target_source=target.source,
        target_document_id=target.document_id,
        target_document_path=target.document_path,
        reference_text=reference.reference_text,
        reference_prefix=reference.reference_prefix,
        reference_body=reference.reference_body,
        reference_type=reference.reference_type,
        raw_start=reference.raw_start,
        raw_end=reference.raw_end,
        link_key=key,
        link_method=link_method,
        target_match_scope=target_match_scope,
        target_proceeding_key=target_proceeding_key,
        target_group_size=target_group_size,
        decision_kind_hint=reference.decision_kind_hint,
        court_hint=reference.court_hint,
    )


def _resolve_uohs_same_proceeding_fallback(
    *,
    source_document_source: str,
    source_document_id: str,
    source_path_resolved: str,
    reference: DocumentReferenceOccurrence,
    self_id_index: dict[str, list[DocumentSelfIdentifier]],
) -> LinkedDocumentReference | None:
    if source_document_source != "uohs" or not reference.reference_body:
        return None

    proceeding_body = _uohs_root_body(reference.reference_body)
    if not proceeding_body:
        return None

    proceeding_key = _compact_key(proceeding_body)
    if not proceeding_key:
        return None

    candidates = self_id_index.get(proceeding_key, [])
    if not candidates:
        return None

    same_proceeding_candidates = [
        candidate
        for candidate in candidates
        if _uohs_proceeding_key(candidate) == proceeding_key
    ]
    if not same_proceeding_candidates:
        return None

    non_source_candidates = [
        candidate
        for candidate in same_proceeding_candidates
        if candidate.document_path != source_path_resolved
    ]
    if non_source_candidates:
        target = min(non_source_candidates, key=_candidate_preference)
        target_group_size = len({item.document_path for item in non_source_candidates})
        link_method = "body_uohs_same_proceeding_fallback"
    else:
        source_candidates = [
            candidate
            for candidate in same_proceeding_candidates
            if candidate.document_path == source_path_resolved
        ]
        if not source_candidates:
            return None
        target = min(source_candidates, key=_candidate_preference)
        target_group_size = len({item.document_path for item in same_proceeding_candidates})
        link_method = "body_uohs_same_proceeding_self_anchor"

    return _build_linked_reference(
        source_document_source=source_document_source,
        source_document_id=source_document_id,
        source_path_resolved=source_path_resolved,
        reference=reference,
        target=target,
        key=proceeding_key,
        link_method=link_method,
        target_match_scope="same_proceeding",
        target_proceeding_key=proceeding_key,
        target_group_size=target_group_size,
    )


def link_document_reference(
    source_document_path: str,
    source_document_source: str,
    source_document_id: str,
    reference: DocumentReferenceOccurrence,
    self_id_index: dict[str, list[DocumentSelfIdentifier]],
) -> LinkedDocumentReference | None:
    if not reference.reference_body:
        return None

    source_path_resolved = str(Path(source_document_path).resolve())
    if _scope_filter_reason(
        reference=reference,
        source_document_source=source_document_source,
        source_document_path=source_path_resolved,
        self_id_index=self_id_index,
    ) is not None:
        return None

    for link_method, key in _reference_link_keys(reference):
        candidates = self_id_index.get(key, [])
        filtered = [
            candidate
            for candidate in candidates
            if candidate.document_path != source_path_resolved
        ]
        unique_paths = {candidate.document_path for candidate in filtered}
        target: DocumentSelfIdentifier | None = None
        resolved_method = link_method
        target_match_scope = "exact_decision"
        target_proceeding_key: str | None = None
        target_group_size: int | None = 1

        if link_method == "body_uohs_same_proceeding":
            grouped_by_proceeding: dict[str, list[DocumentSelfIdentifier]] = defaultdict(list)
            for candidate in filtered:
                proceeding_key = _uohs_proceeding_key(candidate)
                if proceeding_key:
                    grouped_by_proceeding[proceeding_key].append(candidate)
            if len(grouped_by_proceeding) == 1:
                target_proceeding_key, grouped_candidates = next(iter(grouped_by_proceeding.items()))
                target = min(grouped_candidates, key=_candidate_preference)
                target_match_scope = "same_proceeding"
                target_group_size = len({item.document_path for item in grouped_candidates})
            if target is None:
                continue
        elif link_method == "body_uohs_vz_short_decision":
            if len(unique_paths) == 1:
                target = min(filtered, key=_candidate_preference)
                target_match_scope = "same_proceeding"
                target_proceeding_key = key
                target_group_size = len(unique_paths)
            elif len(unique_paths) > 1:
                grouped_candidates: dict[str, list[DocumentSelfIdentifier]] = defaultdict(list)
                for candidate in filtered:
                    grouped_candidates[_canonical_document_group_id(candidate)].append(candidate)
                if len(grouped_candidates) == 1:
                    target = min(filtered, key=_candidate_preference)
                    resolved_method = f"{link_method}_canonical_document"
                    target_match_scope = "same_proceeding"
                    target_proceeding_key = key
                    target_group_size = len(unique_paths)
            if target is None:
                continue
        elif len(unique_paths) == 1:
            target = min(filtered, key=_candidate_preference)
        elif len(unique_paths) > 1:
            grouped_candidates: dict[str, list[DocumentSelfIdentifier]] = defaultdict(list)
            for candidate in filtered:
                grouped_candidates[_canonical_document_group_id(candidate)].append(candidate)
            if len(grouped_candidates) == 1:
                target = min(filtered, key=_candidate_preference)
                resolved_method = f"{link_method}_canonical_document"
                target_group_size = len(unique_paths)
        if target is None:
            continue
        return _build_linked_reference(
            source_document_source=source_document_source,
            source_document_id=source_document_id,
            source_path_resolved=source_path_resolved,
            reference=reference,
            target=target,
            key=key,
            link_method=resolved_method,
            target_match_scope=target_match_scope,
            target_proceeding_key=target_proceeding_key,
            target_group_size=target_group_size,
        )
    return _resolve_uohs_same_proceeding_fallback(
        source_document_source=source_document_source,
        source_document_id=source_document_id,
        source_path_resolved=source_path_resolved,
        reference=reference,
        self_id_index=self_id_index,
    )


def iter_corpus_documents(processed_root: str) -> list[str]:
    return [
        str(path.resolve())
        for path in sorted(Path(processed_root).rglob("*.txt"))
    ]


def link_corpus_document_references(
    processed_root: str,
    external_metadata_path: str | None = None,
) -> tuple[list[LinkedDocumentReference], list[dict], list[DocumentSelfIdentifier]]:
    document_paths = iter_corpus_documents(processed_root)
    self_id_index, self_identifiers = build_document_self_id_index(
        document_paths,
        external_metadata_path=external_metadata_path,
        processed_root=processed_root,
    )
    fuzzy_buckets = build_fuzzy_identifier_buckets(self_identifiers)

    linked: list[LinkedDocumentReference] = []
    unresolved: list[dict] = []

    for document_path in document_paths:
        with open(document_path, "r", encoding="utf-8") as f:
            text = f.read()
        refs = extract_document_references(text)
        document_id = os.path.basename(document_path)
        source_document_source = Path(document_path).resolve().parent.name
        for ref in refs:
            resolved = link_document_reference(
                source_document_path=document_path,
                source_document_source=source_document_source,
                source_document_id=document_id,
                reference=ref,
                self_id_index=self_id_index,
            )
            if resolved is not None:
                linked.append(resolved)
            else:
                candidate_info = build_reference_candidate_targets(
                    reference=ref,
                    source_document_path=document_path,
                    source_document_source=source_document_source,
                    self_id_index=self_id_index,
                    all_identifiers=self_identifiers,
                    fuzzy_buckets=fuzzy_buckets,
                )
                if candidate_info.get("llm_route") == "filtered_out_of_scope":
                    continue
                unresolved.append(
                    {
                        "source_document_source": source_document_source,
                        "source_document_id": document_id,
                        "source_document_path": document_path,
                        "reference_text": ref.reference_text,
                        "reference_prefix": ref.reference_prefix,
                        "reference_body": ref.reference_body,
                        "reference_type": ref.reference_type,
                        "raw_start": ref.raw_start,
                        "raw_end": ref.raw_end,
                        "decision_kind_hint": ref.decision_kind_hint,
                        "court_hint": ref.court_hint,
                        "llm_route": candidate_info["llm_route"],
                        "candidate_targets": candidate_info["candidate_targets"],
                    }
                )

    return linked, unresolved, self_identifiers


def _append_jsonl_row(path: str, row: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_checkpoint_completed_documents(checkpoint_path: str | None) -> set[tuple[str, str]]:
    if not checkpoint_path:
        return set()
    path = Path(checkpoint_path)
    if not path.exists():
        return set()
    completed: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = row.get("source_document_source")
            document_id = row.get("source_document_id")
            if isinstance(source, str) and isinstance(document_id, str):
                completed.add((source, document_id))
    return completed


def stream_corpus_document_references(
    processed_root: str,
    *,
    linked_output_path: str,
    unresolved_output_path: str,
    self_id_output_path: str,
    external_metadata_path: str | None = None,
    checkpoint_path: str | None = None,
    progress_every_docs: int = 1000,
    resume: bool = False,
    use_fuzzy_candidates: bool = True,
) -> dict[str, int]:
    document_paths = iter_corpus_documents(processed_root)
    total_documents = len(document_paths)
    completed_docs = _load_checkpoint_completed_documents(checkpoint_path) if resume else set()

    for output_path in [linked_output_path, unresolved_output_path, checkpoint_path]:
        if not output_path:
            continue
        path = Path(output_path)
        if path.exists() and not resume:
            path.unlink()
    self_id_path = Path(self_id_output_path)
    if self_id_path.exists():
        self_id_path.unlink()

    self_identifier_count = 0
    last_index_progress = 0
    def _self_id_sink(identifier: DocumentSelfIdentifier) -> None:
        nonlocal self_identifier_count
        self_identifier_count += 1
        _append_jsonl_row(
            self_id_output_path,
            {
                "document_id": identifier.document_id,
                "document_path": identifier.document_path,
                "source": identifier.source,
                "identifier_text": identifier.identifier_text,
                "identifier_kind": identifier.identifier_kind,
                "origin": identifier.origin,
                "keys": list(identifier.keys),
                "source_iri": identifier.source_iri,
            },
        )

    def _index_progress(processed_docs: int, total_docs: int) -> None:
        nonlocal last_index_progress
        if processed_docs == total_docs or processed_docs - last_index_progress >= progress_every_docs:
            last_index_progress = processed_docs
            print(
                f"[docref-link] self-id-indexed={processed_docs}/{total_docs} "
                f"self_identifiers={self_identifier_count}",
                flush=True,
            )

    self_id_index, all_identifiers = build_document_self_id_index(
        document_paths,
        external_metadata_path=external_metadata_path,
        processed_root=processed_root,
        collect_all_identifiers=True,
        identifier_sink=_self_id_sink,
        progress_callback=_index_progress,
    )
    fuzzy_buckets = build_fuzzy_identifier_buckets(all_identifiers) if use_fuzzy_candidates else None

    linked_count = 0
    unresolved_count = 0
    processed_documents = 0
    skipped_documents = 0

    for index, document_path in enumerate(document_paths, start=1):
        path_obj = Path(document_path)
        source_document_source = path_obj.resolve().parent.name
        document_id = path_obj.name

        if (source_document_source, document_id) in completed_docs:
            skipped_documents += 1
            processed_documents += 1
            if processed_documents % progress_every_docs == 0 or processed_documents == total_documents:
                print(
                    f"[docref-link] processed={processed_documents}/{total_documents} "
                    f"skipped={skipped_documents} linked={linked_count} unresolved={unresolved_count}",
                    flush=True,
                )
            continue

        with open(document_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        refs = extract_document_references(text)
        doc_linked = 0
        doc_unresolved = 0
        for ref in refs:
            resolved = link_document_reference(
                source_document_path=document_path,
                source_document_source=source_document_source,
                source_document_id=document_id,
                reference=ref,
                self_id_index=self_id_index,
            )
            if resolved is not None:
                _append_jsonl_row(linked_output_path, resolved.to_dict())
                linked_count += 1
                doc_linked += 1
            else:
                candidate_info = build_reference_candidate_targets(
                    reference=ref,
                    source_document_path=document_path,
                    source_document_source=source_document_source,
                    self_id_index=self_id_index,
                    all_identifiers=all_identifiers,
                    fuzzy_buckets=fuzzy_buckets,
                    use_fuzzy_candidates=use_fuzzy_candidates,
                )
                if candidate_info.get("llm_route") == "filtered_out_of_scope":
                    continue
                _append_jsonl_row(
                    unresolved_output_path,
                    {
                        "source_document_source": source_document_source,
                        "source_document_id": document_id,
                        "source_document_path": document_path,
                        "reference_text": ref.reference_text,
                        "reference_prefix": ref.reference_prefix,
                        "reference_body": ref.reference_body,
                        "reference_type": ref.reference_type,
                        "raw_start": ref.raw_start,
                        "raw_end": ref.raw_end,
                        "decision_kind_hint": ref.decision_kind_hint,
                        "court_hint": ref.court_hint,
                        "llm_route": candidate_info["llm_route"],
                        "candidate_targets": candidate_info["candidate_targets"],
                    },
                )
                unresolved_count += 1
                doc_unresolved += 1

        if checkpoint_path:
            _append_jsonl_row(
                checkpoint_path,
                {
                    "source_document_source": source_document_source,
                    "source_document_id": document_id,
                    "source_document_path": document_path,
                    "reference_count": len(refs),
                    "linked_count": doc_linked,
                    "unresolved_count": doc_unresolved,
                },
            )

        processed_documents += 1
        if processed_documents % progress_every_docs == 0 or processed_documents == total_documents:
            print(
                f"[docref-link] processed={processed_documents}/{total_documents} "
                f"skipped={skipped_documents} linked={linked_count} unresolved={unresolved_count}",
                flush=True,
            )

    return {
        "linked": linked_count,
        "unresolved": unresolved_count,
        "self_identifiers": self_identifier_count,
        "processed_documents": processed_documents,
        "skipped_documents": skipped_documents,
        "total_documents": total_documents,
    }
