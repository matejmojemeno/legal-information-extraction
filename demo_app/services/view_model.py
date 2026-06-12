"""Transform extraction outputs into UI-oriented view models."""

from __future__ import annotations

from collections import Counter
import re
from urllib.parse import quote
from typing import Any

from demo_app.models import DemoReferenceItem

SOURCE_LABELS = {
    "nalus": "Constitutional Court",
    "ns": "Supreme Court",
    "nss": "Supreme Administrative Court",
    "uohs": "Office for the Protection of Competition",
}


def _source_label(source: str | None) -> str:
    if not source:
        return "Unknown"
    return SOURCE_LABELS.get(source, source)


def source_label(source: str | None) -> str:
    return _source_label(source)


def _zakonyprolidi_law_url(law_id: str | None) -> str | None:
    if not law_id:
        return None
    match = re.fullmatch(r"(?P<number>\d{1,4})/(?P<year>\d{2}|\d{4})\s*Sb\.", law_id.strip())
    if not match:
        return None
    number = str(int(match.group("number")))
    year = match.group("year")
    if len(year) == 2:
        year_num = int(year)
        year = str(2000 + year_num if year_num <= 30 else 1900 + year_num)
    return f"https://www.zakonyprolidi.cz/cs/{year}-{number}"


def _resolved_law_ids_from_payload(payload: Any) -> set[str]:
    if isinstance(payload, str):
        return {payload}
    if isinstance(payload, dict):
        if isinstance(payload.get("law_id"), str):
            return {payload["law_id"]}
        law_ids = payload.get("law_ids")
        if isinstance(law_ids, list):
            return {value for value in law_ids if isinstance(value, str)}
    return set()


def build_law_alias_index(
    local_aliases: dict[str, str],
    runtime_aliases: dict[str, Any],
) -> dict[str, list[str]]:
    by_law_id: dict[str, set[str]] = {}
    for alias, law_id in local_aliases.items():
        if not alias or not law_id:
            continue
        by_law_id.setdefault(law_id, set()).add(alias)

    for alias, payload in runtime_aliases.items():
        if not isinstance(alias, str):
            continue
        for law_id in _resolved_law_ids_from_payload(payload):
            by_law_id.setdefault(law_id, set()).add(alias)

    return {
        law_id: sorted(
            aliases,
            key=lambda value: (-len(value), value.lower()),
        )
        for law_id, aliases in by_law_id.items()
    }


def _format_single_parsed_detail(parsed_detail: dict) -> str | None:
    parts: list[str] = []
    number = parsed_detail.get("number")
    if number:
        parts.append(f"section {number}")
    odsts = parsed_detail.get("odst") or []
    pism = parsed_detail.get("pism") or []
    if odsts:
        paragraph_parts: list[str] = []
        odst_values = [str(value) for value in odsts]
        pism_values = [str(value) for value in pism]
        if pism_values and len(pism_values) == len(odst_values):
            paragraph_parts = [
                f"paragraph {odst} letter {letter}"
                for odst, letter in zip(odst_values, pism_values)
            ]
        elif pism_values and len(pism_values) == len(odst_values) - 1:
            paragraph_parts.extend(
                f"paragraph {odst} letter {letter}"
                for odst, letter in zip(odst_values[: len(pism_values)], pism_values)
            )
            paragraph_parts.extend(
                f"paragraph {odst}"
                for odst in odst_values[len(pism_values) :]
            )
        else:
            paragraph_parts.append("paragraph " + ", ".join(odst_values))
            if pism_values:
                paragraph_parts.append("letter " + ", ".join(pism_values))
        parts.append("; ".join(paragraph_parts))
    elif pism:
        parts.append("letter " + ", ".join(str(value) for value in pism))
    detail_number = parsed_detail.get("detail_number")
    if detail_number and detail_number != number:
        parts.append(f"detail {detail_number}")
    return " | ".join(parts) if parts else None


def _format_grouped_details(parsed_details: list[dict]) -> str | None:
    if not parsed_details:
        return None
    if len(parsed_details) == 1:
        return _format_single_parsed_detail(parsed_details[0])

    formatted_details: list[str] = []
    for detail in parsed_details:
        formatted = _format_single_parsed_detail(detail)
        if formatted and formatted not in formatted_details:
            formatted_details.append(formatted)

    if not formatted_details:
        return None

    simple_section_pattern = re.compile(r"^section\s+[0-9A-Za-z]+$")
    if all(simple_section_pattern.fullmatch(detail) for detail in formatted_details):
        unique_numbers = [detail.removeprefix("section ").strip() for detail in formatted_details]
        return "sections " + ", ".join(unique_numbers)

    return "; ".join(formatted_details)


_SECTION_TAIL_PATTERN = re.compile(
    r"""
    ^
    (?P<body>
        (?:§{1,2}|čl\.|článek)\s*\d+[a-z]?
        (?:
            (?:
                \s*odst\.?\s*[0-9lI]+[a-z]?
                (?:
                    \s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*[0-9lI]+[a-z]?(?=$|[\s,.;:)])
                )*
                (?:
                    \s*písm\.?\s*[a-z]\)?(?=$|[\s,.;:])
                    (?:
                        \s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*[a-z]\)?(?=$|[\s,.;:])
                    )*
                )?
            )
            |
            (?:
                \s*(?:,|\ba\b)\s*odst\.?\s*[0-9lI]+[a-z]?
                (?:
                    \s*písm\.?\s*[a-z]\)?(?=$|[\s,.;:])
                    (?:
                        \s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*[a-z]\)?(?=$|[\s,.;:])
                    )*
                )?
            )
        )*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _expanded_law_span(text: str, row: dict) -> tuple[int, int]:
    raw_span = row.get("raw_span") or {}
    start = int(raw_span.get("start", 0) or 0)
    end = int(raw_span.get("end", 0) or 0)
    citation_type = str(row.get("citation_type") or "")
    if citation_type not in {"section", "article"}:
        return start, end

    tail_match = _SECTION_TAIL_PATTERN.match(text[start:])
    if not tail_match:
        return start, end
    return start, start + len(tail_match.group("body"))


def _connector_only(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b|\bor\b|\band\b)|(?:ve\s+spojen[ií]\s+s))?\s*",
            text,
            re.IGNORECASE,
        )
    )


def _alias_pattern(alias: str) -> str:
    pattern = re.escape(alias)
    pattern = pattern.replace(r"\ ", r"\s+")
    pattern = pattern.replace(r"\.", r"\.\s*")
    return pattern


def _inflected_alias_pattern(alias: str) -> str:
    parts: list[str] = []
    tokens = re.split(r"(\s+|[.\-–—/()])", alias)
    for token in tokens:
        if not token:
            continue
        if re.fullmatch(r"\s+", token):
            parts.append(r"\s+")
            continue
        if re.fullmatch(r"[.\-–—/()]", token):
            parts.append(re.escape(token))
            continue
        if re.fullmatch(r"[A-Za-zÀ-ž]+", token):
            if len(token) >= 3:
                stem = re.escape(token[:-1])
                parts.append(rf"{stem}[A-Za-zÀ-ž]{{1,6}}")
            else:
                parts.append(re.escape(token))
            continue
        parts.append(re.escape(token))
    return "".join(parts)


def _expand_tail_with_alias_or_law(
    text: str,
    end: int,
    law_id: str | None,
    law_alias_index: dict[str, list[str]],
) -> int:
    if not law_id:
        return end

    lookahead = text[end : min(len(text), end + 96)]
    if not lookahead:
        return end

    structural_gap = (
        r"(?:\s+(?:věty?|odst\.?|písm\.?|bodu?|části|hlavy)\s+[A-Za-zÀ-ž0-9()./\-]+){0,3}\s*"
    )
    explicit_match = re.match(
        rf"^\s*{structural_gap}(?:zákona?|vyhlášky|nařízení|směrnice|předpisu)?\s*(?:č\.\s*)?{re.escape(law_id)}",
        lookahead,
        re.IGNORECASE,
    )
    if explicit_match:
        return end + explicit_match.end()

    for alias in law_alias_index.get(law_id, []):
        alias_match = re.match(
            rf"^\s*{structural_gap}(?:,\s*)?(?:{_alias_pattern(alias)})(?=$|[\s,.;:)])",
            lookahead,
            re.IGNORECASE,
        )
        if alias_match:
            return end + alias_match.end()
        inflected_alias_match = re.match(
            rf"^\s*{structural_gap}(?:,\s*)?(?:{_inflected_alias_pattern(alias)})(?=$|[\s,.;:)])",
            lookahead,
            re.IGNORECASE,
        )
        if inflected_alias_match:
            return end + inflected_alias_match.end()
    return end


def _expand_tail_with_any_candidate_alias(
    text: str,
    end: int,
    candidate_law_ids: list[str],
    law_alias_index: dict[str, list[str]],
) -> int:
    expanded = end
    for law_id in candidate_law_ids:
        if isinstance(law_id, str):
            expanded = max(expanded, _expand_tail_with_alias_or_law(text, end, law_id, law_alias_index))
    return expanded


def _expand_tail_with_foreign_law_phrase(text: str, end: int) -> int:
    suffix = text[end:]
    match = re.match(
        r"\s+(?:Smlouvy?\s+o\s+fungování\s+Evropské\s+unie|SFEU|"
        r"Listiny\s+základních\s+práv\s+Evropské\s+unie|"
        r"Evropské\s+úmluvy\s+o\s+lidských\s+právech|"
        r"Úmluvy\s+o\s+ochraně\s+lidských\s+práv(?:\s+a\s+základních\s+svobod)?)",
        suffix,
        flags=re.IGNORECASE,
    )
    if match:
        return end + match.end()
    return end


def _rows_share_ambiguity_group(row: dict, next_row: dict) -> bool:
    left = [value for value in (row.get("candidate_law_ids") or []) if isinstance(value, str)]
    right = [value for value in (next_row.get("candidate_law_ids") or []) if isinstance(value, str)]
    if not left or not right:
        return False
    return tuple(sorted(left)) == tuple(sorted(right))


def build_law_items(
    text: str,
    law_occurrences: list[dict],
    canonical_law_names: dict[str, str],
    law_alias_index: dict[str, list[str]],
) -> list[DemoReferenceItem]:
    items: list[DemoReferenceItem] = []
    rows = sorted(
        law_occurrences,
        key=lambda row: (
            int((row.get("raw_span") or {}).get("start", 0) or 0),
            int((row.get("raw_span") or {}).get("end", 0) or 0),
        ),
    )
    item_index = 0
    idx = 0
    while idx < len(rows):
        row = rows[idx]

        classification = row.get("predicted_classification") or "unknown"
        if classification == "non_citation":
            idx += 1
            continue
        law_id = row.get("resolved_law_id")
        law_name = canonical_law_names.get(law_id, "") if isinstance(law_id, str) else ""
        ai_assisted = bool(row.get("ai_assisted"))
        item_index += 1
        title = law_id or "Unresolved law reference"
        detail_parts = [classification]
        if law_name:
            detail_parts.append(law_name)
        citation_type = str(row.get("citation_type") or "")

        start, end = _expanded_law_span(text, row)
        parsed_details: list[dict] = []
        if isinstance(row.get("parsed_detail"), dict):
            parsed_details.append(row["parsed_detail"])

        last_section_idx = idx
        if citation_type in {"section", "article"}:
            probe = idx + 1
            while probe < len(rows):
                next_row = rows[probe]
                next_type = str(next_row.get("citation_type") or "")
                next_law_id = next_row.get("resolved_law_id")
                next_span = next_row.get("raw_span") or {}
                next_start = int(next_span.get("start", 0) or 0)
                gap_text = text[end:next_start]
                if (
                    next_type in {"section", "article"}
                    and (
                        (isinstance(law_id, str) and next_law_id == law_id)
                        or (
                            law_id is None
                            and next_law_id is None
                            and _rows_share_ambiguity_group(row, next_row)
                        )
                    )
                    and next_start >= end
                    and next_start - end <= 24
                    and _connector_only(gap_text)
                ):
                    last_section_idx = probe
                    _, end = _expanded_law_span(text, next_row)
                    if isinstance(next_row.get("parsed_detail"), dict):
                        parsed_details.append(next_row["parsed_detail"])
                    probe += 1
                    continue
                break

            tail_probe = last_section_idx + 1
            if isinstance(law_id, str) and tail_probe < len(rows):
                tail_row = rows[tail_probe]
                tail_law_id = tail_row.get("resolved_law_id")
                tail_type = str(tail_row.get("citation_type") or "")
                tail_span = tail_row.get("raw_span") or {}
                tail_start = int(tail_span.get("start", 0) or 0)
                tail_end = int(tail_span.get("end", 0) or 0)
                gap_text = text[end:tail_start]
                if (
                    tail_type == "other_normative"
                    and tail_law_id == law_id
                    and tail_start >= end
                    and tail_start - end <= 40
                    and re.search(r"(?:zákona?|vyhlášky|nařízení|směrnice|předpisu|č\.)", gap_text, re.IGNORECASE)
                ):
                    end = tail_end
                    idx = tail_probe
                else:
                    end = _expand_tail_with_alias_or_law(text, end, law_id, law_alias_index)
                    idx = last_section_idx
            else:
                if isinstance(law_id, str):
                    end = _expand_tail_with_alias_or_law(text, end, law_id, law_alias_index)
                elif law_id is None:
                    if classification == "foreign_law":
                        end = _expand_tail_with_foreign_law_phrase(text, end)
                    else:
                        end = _expand_tail_with_any_candidate_alias(
                            text,
                            end,
                            [value for value in (row.get("candidate_law_ids") or []) if isinstance(value, str)],
                            law_alias_index,
                        )
                idx = last_section_idx
        else:
            if isinstance(law_id, str):
                end = _expand_tail_with_alias_or_law(text, end, law_id, law_alias_index)
            elif classification == "foreign_law":
                end = _expand_tail_with_foreign_law_phrase(text, end)

        detail_text = _format_grouped_details(parsed_details)
        if detail_text:
            detail_parts.append(detail_text)
        if ai_assisted:
            detail_parts.append("BRL-reviewed")

        status = "resolved" if law_id else "unresolved"
        surface = text[start:end] if 0 <= start < end <= len(text) else str(row.get("citation_text") or "")
        items.append(
            DemoReferenceItem(
                id=f"law_{item_index}",
                kind="law_reference",
                subtype=citation_type or "law_reference",
                start=start,
                end=end,
                surface=surface,
                status=status,
                badge="Law",
                color_class="ref-law ref-ai" if ai_assisted else "ref-law",
                title=title,
                detail=" | ".join(detail_parts),
                resolved_label=law_name or law_id,
                external_url=_zakonyprolidi_law_url(law_id if isinstance(law_id, str) else None),
                ai_assisted=ai_assisted,
            )
        )
        idx += 1
    return items


def build_document_items(document_occurrences: list[dict], linked_by_index: dict[int, dict]) -> list[DemoReferenceItem]:
    return build_document_items_with_metadata(document_occurrences, linked_by_index, {})


def build_document_items_with_metadata(
    document_occurrences: list[dict],
    linked_by_index: dict[int, dict],
    metadata_by_document: dict[tuple[str, str], dict],
    ai_rows: list[dict[str, Any]] | None = None,
) -> list[DemoReferenceItem]:
    items: list[DemoReferenceItem] = []
    ai_by_index = {
        int(row.get("reference_index")): row
        for row in (ai_rows or [])
        if row.get("reference_index") is not None
    }
    for idx, row in enumerate(document_occurrences, start=1):
        ai_row = ai_by_index.get(idx - 1)
        ai_result = ai_row.get("result") if isinstance(ai_row, dict) and isinstance(ai_row.get("result"), dict) else {}
        ai_route = str(ai_row.get("llm_route") or "") if isinstance(ai_row, dict) else ""
        ai_assisted = False

        if ai_route == "extraction_presence_check" and ai_result.get("decision") == "not_reference":
            continue

        linked = linked_by_index.get(idx - 1)
        if linked is None and ai_route in {"link_disambiguation", "link_normalization_or_target_recovery"}:
            decision = ai_result.get("decision")
            target_document_id = ai_result.get("target_document_id")
            if decision in {"exact_target", "same_proceeding"} and isinstance(target_document_id, str):
                candidate_targets = ai_row.get("candidate_targets") or []
                chosen = next(
                    (
                        candidate
                        for candidate in candidate_targets
                        if candidate.get("target_document_id") == target_document_id
                    ),
                    None,
                )
                if chosen is not None:
                    linked = {
                        "target_source": chosen.get("target_source"),
                        "target_document_id": chosen.get("target_document_id"),
                        "target_document_path": chosen.get("target_document_path"),
                        "target_match_scope": "same_proceeding" if decision == "same_proceeding" else "exact_decision",
                        "link_method": f"ai_{decision}",
                    }
                    ai_assisted = True

        reference_type = str(row.get("reference_type") or "document_reference")
        if ai_route == "extraction_presence_check":
            ai_type = ai_result.get("reference_type")
            if isinstance(ai_type, str) and ai_type in {"spisova_znacka", "cislo_jednaci"}:
                reference_type = ai_type
        start = int(row.get("raw_start", 0) or 0)
        end = int(row.get("raw_end", 0) or 0)
        status = "linked" if linked else "unresolved"
        target_url = None
        external_url = None
        resolved_label = None
        target_source = None
        target_document_id = None
        detail_parts = [reference_type]
        if linked:
            target_source = linked.get("target_source")
            target_document_id = linked.get("target_document_id")
            target_scope = linked.get("target_match_scope") or "exact_decision"
            detail_parts.append(target_scope)
            resolved_label = f"{target_document_id} ({_source_label(target_source)})"
            target_url = (
                f"/target?source={quote(str(target_source or ''))}"
                f"&document_id={quote(str(target_document_id or ''))}"
            )
            metadata = metadata_by_document.get((str(target_source or ""), str(target_document_id or ""))) or {}
            external_url = metadata.get("judicate_iri") if isinstance(metadata.get("judicate_iri"), str) else None
        elif ai_row is not None:
            ai_assisted = True
            decision = ai_result.get("decision")
            if isinstance(decision, str):
                detail_parts.append(f"BRL reviewed: {decision}")
        court_hint = row.get("court_hint")
        if court_hint:
            detail_parts.append(str(court_hint))
        items.append(
            DemoReferenceItem(
                id=f"docref_{idx}",
                kind="document_reference",
                subtype=reference_type,
                start=start,
                end=end,
                surface=str(row.get("reference_text") or ""),
                status=status,
                badge="Decision",
                color_class="ref-doc ref-ai" if ai_assisted else "ref-doc",
                title=str(row.get("reference_body") or row.get("reference_text") or "Decision reference"),
                detail=" | ".join(detail_parts),
                resolved_label=resolved_label,
                target_source=target_source,
                target_document_id=target_document_id,
                target_url=target_url,
                external_url=external_url,
                ai_assisted=ai_assisted,
            )
        )
    return items


def build_stats(law_items: list[DemoReferenceItem], doc_items: list[DemoReferenceItem]) -> dict[str, int]:
    doc_status_counts = Counter(item.status for item in doc_items)
    return {
        "law_reference_count": len(law_items),
        "resolved_law_reference_count": sum(1 for item in law_items if item.status == "resolved"),
        "ai_law_reference_count": sum(1 for item in law_items if item.ai_assisted),
        "document_reference_count": len(doc_items),
        "linked_document_reference_count": doc_status_counts.get("linked", 0),
        "ai_document_reference_count": sum(1 for item in doc_items if item.ai_assisted),
    }
