"""
First-pass extraction of references to other decisions/documents.

This module is intentionally separate from law-reference extraction. It focuses
on document-style anchors such as:

- ``sp. zn. 1 Afs 96/2004``
- ``č. j. 10 Ads 99/2014 - 58``
- ``čj. 6 Aps 2/2005-60``

The goal of this first version is occurrence mining and qualitative review, not
full corpus linking yet. The main pass is anchor-first (`sp. zn.`, `sen. zn.`,
`č. j.`), with a conservative second pass for bare case numbers when strong
decision-citation cues are present nearby.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


_DOC_REF_PREFIX_BODY = r"(?:sp\.?\s*zn\.?|sen\.?\s*zn\.?|č\.?\s*j\.?|čj\.?)"
_DOC_REF_PREFIX = rf"(?<!\w)(?P<prefix>{_DOC_REF_PREFIX_BODY})\s*:?\s*"
_MAX_BODY_CHARS = 110

_BARE_CASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<body>("
    r"(?:Pl\.?\s*ÚS(?:-st\.)?|\b[IVXLCDM]+\.\s*ÚS|\bÚS)\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?|"
    r"\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?"
    r"))",
    re.IGNORECASE,
)

DOCUMENT_REFERENCE_PREFIX_PATTERN = re.compile(
    _DOC_REF_PREFIX,
    re.IGNORECASE,
)

_BODY_CUTOFF_PATTERN = re.compile(
    r"("
    r"(?:\n+\s*(?:"
    r"ČESKÁ\s+REPUBLIKA|"
    r"ROZSUDEK|"
    r"USNESEN[ÍI]|"
    r"JM[ÉE]NEM\s+REPUBLIKY|"
    r"(?:Krajsk(?:ý|ého)|Městsk(?:ý|ého)|Vrchního|Nejvyššího|Ústavního)\s+soud(?:u|em)?"
    r"))|"
    r"(?:\s+vyhlášen(?:ým|ého|á|é|ém|ou)\b)|"
    r"(?:\s+(?:Krajsk(?:ého|ý|ém|ému)|Městsk(?:ého|ý|ém|ému)|Vrchní(?:ho|m|mu)|Nejvyšší(?:ho|m|mu)|Ústavní(?:ho|m|mu)|Okresní(?:ho|m|mu))\s+soud(?:u|em)?\b)|"
    r"(?:,?\s+ze\s+dne\b)|"
    r"(?:,?\s+(?:a|či|nebo)\s+ze\s+dne\b)|"
    r"(?:\s+(?:a|či|nebo)\s+(?:usnesen[íi]|rozsud(?:ek|ku)|nález(?:u)?|rozhodnut(?:í|ím)|příkaz(?:u)?)\b)|"
    r"(?:\s+(?:a|či|nebo)\s+zejména\b)|"
    r"(?:,?\s+kter(?:ý|á|é|ou|ým|ýmž|ého|ém|ých)\b)|"
    r"(?:,?\s+veden(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+vydan(?:ého|é|ý|a|ém|ou)\b)|"
    r"(?:,?\s+týkající(?:ho|ch|mu|m|mž)?\b)|"
    r"(?:,?\s+ve\s+věci\b)|"
    r"(?:,?\s+proti\b)|"
    r"(?:,\s+(?=[a-zá-ž]{3,}\b))|"
    r"(?:,\s+č\.?\s*j\.?\b)|"
    r"(?:,\s+čj\.?\b)|"
    r"(?:,\s+sp\.?\s*zn\.?\b)|"
    r"(?:,\s+sen\.?\s*zn\.?\b)|"
    r"(?:\s+(?:proto|tak|kde|policejní|součástí|vydán|vydána|vydané|vydaným|vydal|vydala|konáno|odložil|potvrdil|zamítl|uvedl|informoval|zahájeno|stanovil)\b)|"
    rf"(?:\s+(?:a|či|nebo)\s+{_DOC_REF_PREFIX_BODY})|"
    r"(?:[;\)\]\"“„])"
    r")",
    re.IGNORECASE,
)

_TRAILING_NOISE_PATTERN = re.compile(
    r"(?:\s+(?:a|či|pak|vydaného|vedeného|týkajícího|týkající|vydané|vedené))+$",
    re.IGNORECASE,
)

_CZECH_MONTH_PATTERN = (
    r"(?:ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince)"
)

_SPISOVA_VALID_PATTERN = re.compile(
    r"^(?:"
    r"(?:Pl|[IVXLCDM]+)\.\s*ÚS\s+\d+(?:/\d+)?(?:-\d+)?|"
    r"[A-ZÁ-ŽÚŮČŘŠŽĚÍÝÓŤŇÜÖ0-9][A-Za-zÁ-Ža-z0-9./\-–— ]{1,90}\d+/\d{2,4}(?:\s*[-/–—]\s*[A-Za-z0-9\-]+)*(?:\s+[A-Z][A-Za-z0-9\-]*)*"
    r")$",
)

_CISLO_JEDNACI_VALID_PATTERN = re.compile(
    r"^[A-ZÁ-ŽÚŮČŘŠŽĚÍÝÓŤŇÜÖ0-9][A-Za-zÁ-Ža-z0-9./\-–— ]{1,90}\d+(?:/\d{2,4})(?:\s*[-/–—]\s*[A-Za-z0-9\-]+)*(?:\s+[A-Z][A-Za-z0-9\-]*)*$",
)

_DECISION_KIND_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rozsudek",
        re.compile(
            r"\brozsud(?:ek|ku|kem|ky|ků|cích|cím|cích)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "usnesení",
        re.compile(
            r"\busnesen(?:í|ím|ích|ímu)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "nález",
        re.compile(
            r"\bnález(?:u|em|y|ech)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rozhodnutí",
        re.compile(
            r"\brozhodnut(?:í|ím|ích)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "příkaz",
        re.compile(
            r"\bpříkaz(?:u|em|y|ech)?\b",
            re.IGNORECASE,
        ),
    ),
)

_COURT_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Nejvyššího správního soudu",
        re.compile(
            r"(?:\bNejvyšší(?:ho|m|mu)?\s+správní(?:ho|m|mu)?\s+soud(?:u|em)?\b|\bNSS\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "Nejvyššího soudu",
        re.compile(
            r"\bNejvyšší(?:ho|m|mu)?\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Ústavního soudu",
        re.compile(
            r"\bÚstavní(?:ho|m|mu)?\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Krajského soudu",
        re.compile(
            r"\bKrajsk(?:ého|ý|ém|ému)\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Městského soudu",
        re.compile(
            r"\bMěstsk(?:ého|ý|ém|ému)\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Vrchního soudu",
        re.compile(
            r"\bVrchní(?:ho|m|mu)?\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Okresního soudu",
        re.compile(
            r"\bOkresní(?:ho|m|mu)?\s+soud(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rozšířeného senátu",
        re.compile(
            r"\brozšířen(?:ého|ý|ém|ému)\s+senát(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "předsedy Úřadu",
        re.compile(
            r"\bpředsed(?:y|a|ou)\s+Úřadu\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Úřadu",
        re.compile(
            r"\bÚřad(?:u|em)?\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(slots=True)
class DocumentReferenceOccurrence:
    reference_text: str
    reference_prefix: str
    reference_body: str
    reference_type: str
    raw_start: int
    raw_end: int
    decision_kind_hint: str | None
    court_hint: str | None
    context: str

    def to_dict(self) -> dict:
        return {
            "reference_text": self.reference_text,
            "reference_prefix": self.reference_prefix,
            "reference_body": self.reference_body,
            "reference_type": self.reference_type,
            "raw_start": self.raw_start,
            "raw_end": self.raw_end,
            "decision_kind_hint": self.decision_kind_hint,
            "court_hint": self.court_hint,
            "context": self.context,
        }


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _reference_type(prefix: str) -> str:
    normalized = _normalize_spaces(prefix).lower().replace(" ", "")
    if normalized.startswith("sp.zn") or normalized.startswith("sen.zn"):
        return "spisova_znacka"
    return "cislo_jednaci"


def _infer_reference_type_from_body(body: str) -> str:
    normalized = _normalize_spaces(body)
    if re.match(r"^(?:Pl\.?\s*ÚS(?:-st\.)?|[IVXLCDM]+\.\s*ÚS|ÚS)\b", normalized, re.IGNORECASE):
        return "spisova_znacka"
    return "cislo_jednaci"


def _infer_court_hint_from_body(body: str) -> str | None:
    normalized = _normalize_spaces(body)
    if re.search(r"\bÚS\b", normalized, re.IGNORECASE):
        return "Ústavního soudu"
    if re.search(r"\bNSČR\b", normalized, re.IGNORECASE):
        return "Nejvyššího soudu"
    return None


def _is_incomplete_reference_body(body: str) -> bool:
    normalized = _normalize_spaces(body)
    if re.search(r"\bXY\b", normalized):
        return True
    if normalized.endswith(("-", "/", ",")):
        return True
    if re.search(r"\b(?:a|či|nebo|resp)\b$", normalized, re.IGNORECASE):
        return True
    return False


def _is_prefixed_reference_like(body: str, reference_type: str) -> bool:
    normalized = _normalize_spaces(body)
    if _is_incomplete_reference_body(normalized):
        return False
    if re.search(r"\bze\s+dne\b", normalized, re.IGNORECASE):
        return False
    if re.search(r"\d\.\s+[A-ZÁ-Ž]", normalized):
        return False
    if re.search(r",\s", normalized):
        return False
    if re.search(r"\s[a-zá-ž]{1,}\b", normalized):
        return False
    if reference_type == "spisova_znacka" and re.match(
        r"^[A-Za-zÁ-Ža-z]{1,6}\s+[IVXLCDM]+\s+\d+$",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if re.match(r"^[A-Za-zÁ-Ža-z0-9][A-Za-zÁ-Ža-z0-9.,/\-–— ]{2,100}$", normalized):
        separator_count = len(re.findall(r"[-/]", normalized))
        if re.search(r"\d+/\d{2,4}", normalized) and separator_count >= 1:
            return True
        if separator_count >= 2 and re.search(r"\d{2,4}", normalized):
            return True
    return False


def _is_reference_like(body: str, reference_type: str, *, anchored: bool = False) -> bool:
    if len(body) < 4:
        return False
    if not re.search(r"\d", body):
        return False
    if anchored and _is_prefixed_reference_like(body, reference_type):
        return True
    if reference_type == "spisova_znacka":
        return bool(_SPISOVA_VALID_PATTERN.match(body))
    return bool(_CISLO_JEDNACI_VALID_PATTERN.match(body))


def _rstrip_reference_noise(text: str) -> str:
    return text.rstrip(" \t\r\n,.:;")


def _leading_valid_prefixed_reference(
    text: str,
    reference_type: str,
) -> tuple[str, int] | None:
    candidate_text = _rstrip_reference_noise(text)
    if not candidate_text:
        return None

    for candidate_end in range(len(candidate_text), 0, -1):
        candidate = _rstrip_reference_noise(candidate_text[:candidate_end])
        if not candidate:
            continue
        if _is_reference_like(_normalize_spaces(candidate), reference_type, anchored=True):
            return candidate, len(candidate)
    return None


def _trim_before_nested_prefixed_chain(
    raw_body: str,
    reference_type: str,
) -> tuple[str, bool] | None:
    nested_matches = list(DOCUMENT_REFERENCE_PREFIX_PATTERN.finditer(raw_body))
    if not nested_matches:
        return None

    connector_pattern = re.compile(
        r"\s+(?:a|či|nebo)\s+(?:(?:[^\s,.;:()]{1,30})\s+){0,14}$",
        re.IGNORECASE,
    )

    for nested_match in nested_matches:
        if nested_match.start() <= 0:
            continue
        left = raw_body[: nested_match.start()]
        connector_match = connector_pattern.search(left)
        if connector_match is None:
            continue

        candidate = _rstrip_reference_noise(left[: connector_match.start()])
        leading = _leading_valid_prefixed_reference(candidate, reference_type)
        if leading is not None:
            return leading[0], False
        return None, True

    return None


def _split_coordinated_prefixed_body(
    raw_body: str,
    reference_type: str,
) -> list[tuple[str, int, int]] | None:
    delimiter_pattern = re.compile(r"\s+(?:a|či|nebo)\s+", re.IGNORECASE)
    delimiters = list(delimiter_pattern.finditer(raw_body))
    if not delimiters:
        return None

    segments: list[tuple[str, int, int]] = []
    segment_start = 0
    for delimiter in delimiters:
        segment_end = delimiter.start()
        segment_slice = raw_body[segment_start:segment_end]
        leading = _leading_valid_prefixed_reference(segment_slice, reference_type)
        if leading is None:
            return None
        segment_text, segment_length = leading
        raw_end = segment_start + segment_length
        segments.append((segment_text, segment_start, raw_end))
        segment_start = delimiter.end()

    final_slice = raw_body[segment_start:]
    leading_final = _leading_valid_prefixed_reference(final_slice, reference_type)
    if leading_final is None:
        return None
    final_segment, final_length = leading_final
    raw_end = segment_start + final_length
    segments.append((final_segment, segment_start, raw_end))

    return segments if len(segments) >= 2 else None


def _extract_body_candidate(
    text: str,
    body_start: int,
    reference_type: str,
) -> tuple[str, int] | None:
    snippet = text[body_start : body_start + _MAX_BODY_CHARS]
    if not snippet:
        return None

    cutoff_match = _BODY_CUTOFF_PATTERN.search(snippet)
    body_end = cutoff_match.start() if cutoff_match else len(snippet)
    hard_period_index = _hard_period_boundary(snippet)
    if hard_period_index is not None:
        body_end = min(body_end, hard_period_index)
    body = _rstrip_reference_noise(snippet[:body_end])
    body = _TRAILING_NOISE_PATTERN.sub("", body)
    body = _rstrip_reference_noise(body)
    trimmed_nested = _trim_before_nested_prefixed_chain(body, reference_type)
    if trimmed_nested is not None:
        trimmed_body, should_reject = trimmed_nested
        if should_reject:
            return None
        body = trimmed_body
    if body and _is_reference_like(_normalize_spaces(body), reference_type, anchored=True):
        return body, body_start + len(body)

    for candidate_end in range(len(snippet), 0, -1):
        candidate = _rstrip_reference_noise(snippet[:candidate_end])
        if not candidate:
            continue
        trimmed_nested = _trim_before_nested_prefixed_chain(candidate, reference_type)
        if trimmed_nested is not None:
            trimmed_body, should_reject = trimmed_nested
            if should_reject:
                continue
            candidate = trimmed_body
        if _is_reference_like(_normalize_spaces(candidate), reference_type, anchored=True):
            return candidate, body_start + len(candidate)

    return None


def _is_soft_period_boundary(snippet: str, match: re.Match[str]) -> bool:
    punctuation_index = match.start()
    if punctuation_index <= 0:
        return False

    prev_char = snippet[punctuation_index - 1]
    next_fragment = snippet[punctuation_index + 1 : punctuation_index + 6]
    next_fragment_full = snippet[punctuation_index + 1 : punctuation_index + 12]
    if prev_char.isdigit():
        left_fragment = snippet[max(0, punctuation_index - 2) : punctuation_index]
        left_match = re.search(r"\b(\d{1,2})$", left_fragment)
        if left_match:
            left_num = int(left_match.group(1))
            date_like_tail = snippet[punctuation_index + 1 : punctuation_index + 18]
            if 1 <= left_num <= 31 and re.match(
                rf"\s+{_CZECH_MONTH_PATTERN}\b",
                date_like_tail,
                re.IGNORECASE,
            ):
                return True
            next_match = re.match(r"\s+(?P<num>\d{1,4})(?P<rest>\.\s*|(?:\b|[,;)]))", date_like_tail)
            if next_match:
                next_num = int(next_match.group("num"))
                rest = next_match.group("rest")
                if rest.startswith(".") and 1 <= left_num <= 31 and 1 <= next_num <= 12:
                    return True
                if len(next_match.group("num")) == 4 and 1 <= left_num <= 12 and 1800 <= next_num <= 2100:
                    return True
    left_fragment = snippet[max(0, punctuation_index - 8) : punctuation_index + 1]
    if re.search(r"(?:^|\s)(?:Pl|[IVXLCDM]+)\.$", left_fragment, re.IGNORECASE) and re.match(
        r"\s*ÚS\b",
        next_fragment_full,
        re.IGNORECASE,
    ):
        return True

    prefix = snippet[max(0, punctuation_index - 8) : punctuation_index + 1].lower()
    if re.search(r"(?:např|atd|tj|tzv|sp|sen|zn|čj|č|odst|písm|čl)\.$", prefix):
        return True

    return False


def _hard_period_boundary(snippet: str) -> int | None:
    for match in re.finditer(r"\.(?=\s+(?:[A-ZÁ-Ž]|\d))", snippet):
        if _is_soft_period_boundary(snippet, match):
            continue
        return match.start()
    return None


def _preceding_clause(text: str, start: int, window: int = 220) -> str:
    left = max(0, start - window)
    snippet = text[left:start]
    boundary = None
    for match in re.finditer(r"(?:[.!?;:]\s+|\n{2,})", snippet):
        if match.group(0).startswith(".") and _is_soft_period_boundary(snippet, match):
            continue
        boundary = match
    if boundary is not None:
        snippet = snippet[boundary.end() :]
    return snippet


def _has_hard_boundary(text: str) -> bool:
    for match in re.finditer(r"(?:[.!?;:]\s+|\n{2,})", text):
        if match.group(0).startswith(".") and _is_soft_period_boundary(text, match):
            continue
        return True
    return False


def _extract_hint(
    candidates: tuple[tuple[str, re.Pattern[str]], ...],
    text: str,
) -> str | None:
    best_label = None
    best_start = -1
    best_end = -1
    best_length = -1
    for label, pattern in candidates:
        for match in pattern.finditer(text):
            candidate_end = match.end()
            candidate_start = match.start()
            candidate_length = len(match.group(0))
            if candidate_end > best_end or (
                candidate_end == best_end
                and (
                    candidate_length > best_length
                    or (
                        candidate_length == best_length
                        and candidate_start >= best_start
                    )
                )
            ):
                best_label = label
                best_start = candidate_start
                best_end = candidate_end
                best_length = candidate_length
    return best_label


def _overlaps_existing(
    start: int,
    end: int,
    spans: list[tuple[int, int]],
) -> bool:
    for existing_start, existing_end in spans:
        if start < existing_end and end > existing_start:
            return True
    return False


def _looks_like_prefixed_reference_context(text: str, start: int) -> bool:
    left = text[max(0, start - 20) : start]
    last_match = None
    for match in DOCUMENT_REFERENCE_PREFIX_PATTERN.finditer(left):
        last_match = match
    if last_match is None:
        return False
    gap = left[last_match.end() :]
    return bool(re.fullmatch(r"[\s:]*", gap))


def _bare_case_is_supported(body: str) -> bool:
    normalized = _normalize_spaces(body)
    return bool(
        re.match(
            r"^(?:Pl\.?\s*ÚS(?:-st\.)?|[IVXLCDM]+\.\s*ÚS|ÚS|\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4})",
            normalized,
            re.IGNORECASE,
        )
    )


def _is_standalone_line(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip() == text[start:end].strip()


def _previous_nonspace_char(text: str, index: int) -> str | None:
    for pos in range(index - 1, -1, -1):
        if not text[pos].isspace():
            return text[pos]
    return None


def _next_nonspace_char(text: str, index: int) -> str | None:
    for pos in range(index, len(text)):
        if not text[pos].isspace():
            return text[pos]
    return None


def _is_repeated_standalone_header_like_bare_case(
    text: str,
    start: int,
    end: int,
    body: str,
    body_counts: Counter[str],
) -> bool:
    if body_counts.get(body, 0) < 2:
        return False
    if not _is_standalone_line(text, start, end):
        return False

    prev_char = _previous_nonspace_char(text, start)
    next_char = _next_nonspace_char(text, end)
    if prev_char is None or next_char is None:
        return False

    continues_sentence = (prev_char.islower() or prev_char in ",;") and next_char.islower()
    if continues_sentence:
        return True
    return start < 1500


def _is_top_of_document_header_like_bare_case(
    text: str,
    start: int,
    end: int,
) -> bool:
    if start > 1200:
        return False
    nearby = text[max(0, start - 120) : min(len(text), end + 180)]
    if re.search(
        r"(?:\bSoudce\s+zpravodaj\b|\bU\s*S\s*N\s*E\s*S\s*E\s*N\s*Í\b|\bR\s*O\s*Z\s*S\s*U\s*D\s*E\s*K\b|\bČESKÁ\s+REPUBLIKA\b|\bJ\s*M\s*É\s*N\s*E\s*M\b)",
        nearby,
        re.IGNORECASE,
    ):
        return True
    if _is_standalone_line(text, start, end):
        next_snippet = text[end : min(len(text), end + 40)]
        if re.match(r"\s*(?:\[\d+\]|U\s*S\s*N\s*E\s*S\s*E\s*N\s*Í|R\s*O\s*Z\s*S\s*U\s*D\s*E\s*K)", next_snippet):
            return True
    return False


def _has_bare_reference_cue(preceding: str) -> bool:
    normalized = _normalize_spaces(preceding)
    if DOCUMENT_REFERENCE_PREFIX_PATTERN.search(normalized):
        return True
    if re.search(
        r"(?:"
        r"(?:Pl\.?\s*ÚS(?:-st\.)?|[IVXLCDM]+\.\s*ÚS|ÚS)\s+\d+/\d{2,4}|"
        r"\d+\s+[A-Za-zÁ-Ž]{1,8}\s+\d+/\d{2,4}(?:\s*[-–—]\s*\d+)?"
        r")(?:\s*,\s*)$",
        normalized,
        re.IGNORECASE,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:srov|viz|např|například|obdobně|podobně|veden(?:ého|á|é)?\s+pod|spisovou\s+značk(?:ou|a)|reg\.?\s*č\.?)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def _extract_bare_document_references(
    text: str,
    *,
    context_radius: int,
    occupied_spans: list[tuple[int, int]],
) -> list[DocumentReferenceOccurrence]:
    out: list[DocumentReferenceOccurrence] = []
    body_counts = Counter(
        _normalize_spaces(match.group("body"))
        for match in _BARE_CASE_PATTERN.finditer(text)
    )

    for match in _BARE_CASE_PATTERN.finditer(text):
        start = match.start("body")
        end = match.end("body")
        if _overlaps_existing(start, end, occupied_spans):
            continue
        if _looks_like_prefixed_reference_context(text, start):
            continue

        raw_body = match.group("body")
        body = _normalize_spaces(raw_body)
        if not _bare_case_is_supported(body):
            continue
        if _is_repeated_standalone_header_like_bare_case(text, start, end, body, body_counts):
            continue
        if _is_top_of_document_header_like_bare_case(text, start, end):
            continue
        prev_nonspace = _previous_nonspace_char(text, start)
        if prev_nonspace is not None and prev_nonspace.isdigit() and start < 1500:
            continue

        preceding = _preceding_clause(text, start)
        decision_kind_hint = _extract_hint(_DECISION_KIND_HINTS, preceding)
        court_hint = _extract_hint(_COURT_HINTS, preceding)
        if decision_kind_hint is None and court_hint is None and not _has_bare_reference_cue(preceding):
            continue
        inferred_court_hint = _infer_court_hint_from_body(body)
        if inferred_court_hint is not None and court_hint in {None, "Úřadu"}:
            court_hint = inferred_court_hint

        reference_type = _infer_reference_type_from_body(body)
        if not _is_reference_like(body, reference_type):
            continue

        ctx_start = max(0, start - context_radius)
        ctx_end = min(len(text), end + context_radius)
        context = text[ctx_start:ctx_end].replace("\n", " ")

        out.append(
            DocumentReferenceOccurrence(
                reference_text=body,
                reference_prefix="",
                reference_body=body,
                reference_type=reference_type,
                raw_start=start,
                raw_end=end,
                decision_kind_hint=decision_kind_hint,
                court_hint=court_hint,
                context=context,
            )
        )
        occupied_spans.append((start, end))

    return out


def extract_document_references(
    text: str,
    context_radius: int = 220,
) -> list[DocumentReferenceOccurrence]:
    out: list[DocumentReferenceOccurrence] = []
    occupied_spans: list[tuple[int, int]] = []
    recent_decision_kind_hint: str | None = None
    recent_court_hint: str | None = None
    recent_reference_end: int | None = None

    for match in DOCUMENT_REFERENCE_PREFIX_PATTERN.finditer(text):
        prefix = _normalize_spaces(match.group("prefix"))
        reference_type = _reference_type(prefix)
        body_candidate = _extract_body_candidate(text, match.end(), reference_type)
        if body_candidate is None:
            continue
        raw_body, end = body_candidate
        body = _normalize_spaces(raw_body)
        if not _is_reference_like(body, reference_type, anchored=True):
            continue

        start = match.start("prefix")
        full = _normalize_spaces(text[start:end])
        ctx_start = max(0, start - context_radius)
        ctx_end = min(len(text), end + context_radius)
        context = text[ctx_start:ctx_end].replace("\n", " ")

        preceding = _preceding_clause(text, start)
        decision_kind_hint = _extract_hint(_DECISION_KIND_HINTS, preceding)
        court_hint = _extract_hint(_COURT_HINTS, preceding)

        shared_chain = False
        if recent_reference_end is not None:
            gap = text[recent_reference_end:start]
            if len(gap) <= 180 and not _has_hard_boundary(gap):
                shared_chain = True
        if shared_chain:
            if decision_kind_hint is None:
                decision_kind_hint = recent_decision_kind_hint
            if court_hint is None:
                court_hint = recent_court_hint

        split_segments = _split_coordinated_prefixed_body(raw_body, reference_type)
        if split_segments is None:
            split_segments = [(raw_body, 0, len(raw_body))]

        for segment_text, segment_start_offset, segment_end_offset in split_segments:
            normalized_segment_text = _normalize_spaces(segment_text)
            segment_end = match.end() + segment_end_offset
            segment_full = _normalize_spaces(f"{prefix} {normalized_segment_text}")
            segment_ctx_end = min(len(text), segment_end + context_radius)
            segment_context = text[ctx_start:segment_ctx_end].replace("\n", " ")
            out.append(
                DocumentReferenceOccurrence(
                    reference_text=segment_full,
                    reference_prefix=prefix,
                    reference_body=normalized_segment_text,
                    reference_type=reference_type,
                    raw_start=start,
                    raw_end=segment_end,
                    decision_kind_hint=decision_kind_hint,
                    court_hint=court_hint,
                    context=segment_context,
                )
            )
            occupied_spans.append((start, segment_end))
        if decision_kind_hint is not None:
            recent_decision_kind_hint = decision_kind_hint
        if court_hint is not None:
            recent_court_hint = court_hint
        recent_reference_end = end

    bare_refs = _extract_bare_document_references(
        text,
        context_radius=context_radius,
        occupied_spans=occupied_spans,
    )
    out.extend(bare_refs)
    out.sort(key=lambda item: (item.raw_start, item.raw_end))

    return out
