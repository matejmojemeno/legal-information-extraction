"""
Citation extraction and resolution for Czech legal texts.

Rule-first pipeline stages:
1. candidate detection
2. detail parsing
3. resolution waterfall
4. false-positive / review routing

Primary output is occurrence-level citation objects (`CitationOccurrence`).
Anomaly queue entries are derived from those occurrences.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from src.citation_models import CitationOccurrence
from src.normalization import normalize_for_matching

# Context windows
FORWARD_CONTEXT_MIN_CHARS = 140
FORWARD_CONTEXT_MAX_CHARS = 480
ANOMALY_CONTEXT_RADIUS = 500
LOW_CONFIDENCE_THRESHOLD = 0.75
NEAR_CONTEXT_CHARS = 160
BACKWARD_CONTEXT_CHARS = 90
BACKWARD_RESOLUTION_CHARS = 280
RECENT_STRONG_LAW_MAX_DISTANCE = 1400
RECENT_CONTEXT_LAW_MAX_DISTANCE = 7000
GENERIC_TOPIC_OVERRIDE_DISTANCE = 300
RECENT_SECTION_REFERENCE_MAX_DISTANCE = 1200
CHAIN_CARRYOVER_MAX_DISTANCE = 520
LOCAL_LAW_CUE_WINDOW_CHARS = 96

# Candidate detection
SECTION_PATTERN = re.compile(r"(?:§{1,2}|čl\.|článek)\s*\d+[a-z]?", re.IGNORECASE)

# Resolution patterns
LAW_ID_PATTERN = re.compile(r"(\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.)")
LAW_ID_OCR_PATTERN = re.compile(
    r"(?:zák(?:on)?\.?\s*č\.?\s*)(\d{1,4})\s+(\d{4})\s*Sb\.",
    re.IGNORECASE,
)
ANAPHORA_PATTERN = re.compile(
    r"\b(?:"
    r"téhož\s+zákona|tohoto\s+zákona|uvedeného\s+zákona|citovaného\s+zákona|"
    r"cit\.\s*zák\.?|"
    r"téže\s+vyhlášky|této\s+vyhlášky|uvedené\s+vyhlášky|citované\s+vyhlášky|"
    r"této\s+směrnice|téže\s+směrnice|"
    r"téhož\s+předpisu|tohoto\s+předpisu|uvedeného\s+předpisu|citovaného\s+předpisu|"
    r"cit\.\s*předpisu"
    r")\b",
    re.IGNORECASE,
)
SECTION_ANAPHORA_PATTERN = re.compile(
    r"\b(?:citovaného|uvedeného|tohoto|téhož)\s+§\s*\d+[a-z]?\b",
    re.IGNORECASE,
)
GENERIC_REFERENCE_PATTERN = re.compile(
    r"\b(?:zákona|vyhlášky|nařízení|směrnice|předpisu|ust\.?)\b",
    re.IGNORECASE,
)
LOCAL_LAW_CUE_PATTERN = re.compile(
    r"\b(?:zákona|zákon|řádu|vyhlášky|nařízení|směrnice|předpisu|Listiny|Ústavy)\b",
    re.IGNORECASE,
)
FOREIGN_LAW_PATTERN = re.compile(
    r"\b(?:"
    r"směrnice|nařízení\s+eu|"
    r"smlouvy?\s+o\s+fungování\s+evropské\s+unie|"
    r"sfeu|"
    r"úmluvy?\s+o\s+ochraně\s+lidských\s+práv(?:\s+a\s+základních\s+svobod)?|"
    r"mezinárodního\s+paktu(?:\s+osn)?\s+o\s+občanských\s+a\s+politických\s+právech"
    r")\b",
    re.IGNORECASE,
)
ARBITRATION_RULES_PATTERN = re.compile(
    r"\bpravidel\s+o\s+nákladech\s+rozhodčího\s+řízení\b",
    re.IGNORECASE,
)
ARBITRATION_INTERNAL_PATTERN = re.compile(
    r"\b(?:řád(?:u)?\s+rozhodčího\s+soudu|pravidl(?:a|ech|y|el)\s+o\s+nákladech(?:\s+rozhodčího\s+řízení)?)\b",
    re.IGNORECASE,
)
ARBITRATION_INSTITUTION_PATTERN = re.compile(
    r"\b(?:rozhodčího\s+soudu|hospodářské\s+komoře|agrární\s+komoře)\b",
    re.IGNORECASE,
)
ARBITRATION_ANAPHORA_PATTERN = re.compile(
    r"\b(?:jeho|tohoto|citovaného)\s+(?:řádu|pravidel)\b",
    re.IGNORECASE,
)
AMENDMENT_CONTEXT_PATTERN = re.compile(
    r"(?:ve\s+znění|novel(?:a|y|ou)?|kterým\s+se\s+mění|mění\s+zákon|doplňuje)",
    re.IGNORECASE,
)
NON_STATUTE_COLLECTION_PATTERN = re.compile(
    r"\b(?:Sb\.\s*NSS|sbírk[ay]\s+rozhodnutí)\b",
    re.IGNORECASE,
)
GENERIC_LAW_DEFINITION_PATTERN = re.compile(
    r"(?:zákona?|zákonem)\s+č\.?\s*(\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.).{0,700}?dále\s+jen\s+[\"„“]zákon[\"“”]",
    re.IGNORECASE | re.DOTALL,
)
GENERIC_LAW_ALIAS_PATTERN = re.compile(
    r"dále\s+jen\s+[\"„“]zákon[\"“”]",
    re.IGNORECASE,
)
GENERIC_LAW_INTRO_PATTERN = re.compile(
    r"(?:zákona?|zákonem)\s+č\.?\s*(\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.)",
    re.IGNORECASE,
)
CONTINUATION_TOKEN_PATTERN = re.compile(
    r"""
    (?P<ws>\s*)
    (?:
        (?P<sep>[,.;]) |
        (?P<conn>a|či|nebo|resp\.?)
    )
    (?P<gap>\s*)
    (?P<number>\d+[a-z]?)
    """,
    re.IGNORECASE | re.VERBOSE,
)
RANGE_TOKEN_PATTERN = re.compile(
    r"""
    ^(?P<lead>\s*až\s*)
    (?:(?P<marker>§{1,2}|čl\.|článek)\s*)?
    (?P<number>\d+[a-z]?)
    """,
    re.IGNORECASE | re.VERBOSE,
)
HARD_BOUNDARY_PATTERN = re.compile(
    r"(?:[!?;:]\s+|(?<!odst)(?<!písm)(?<!čl)(?<!sp)(?<!bod)(?<!věta)(?<!č)\.\s+)",
    re.IGNORECASE,
)
NON_STATUTE_PUBLICATION_PATTERN = re.compile(
    r"\b(?:SbNU|sp\.\s*zn\.|nález(?:u)?\s+ze\s+dne)\b",
    re.IGNORECASE,
)


_STAGE_CONFIDENCE = {
    "Law ID Mention": 0.99,
    "Typed: Foreign Law": 0.82,
    "Filtered: Internal Regulation": 0.99,
    "Filtered: Non-Statutory Collection": 0.99,
    "Level 1: Direct Match": 0.99,
    "Level 2: Explicit Anaphora": 0.90,
    "Level 2A: Section Anaphora": 0.92,
    "Level 3: Implicit Generic Reference": 0.62,
    "Level 3B: Citation Chain Carryover": 0.72,
    "Level 3C: Backward Context Carryover": 0.70,
    "Level 4: Short Structural Reference": 0.55,
    "Level 5: Local Dictionary": 0.93,
    "Level 5A: Ambiguous Local Alias + Context": 0.84,
    "Level 6: Global Dictionary": 0.86,
    "Level 6A: Ambiguous Global Alias + Context": 0.78,
    "Level 7: Unresolved": 0.0,
}


def _normalize_law_id(law_id: str) -> str:
    normalized = re.sub(r"\s+", " ", law_id).strip()
    match = re.fullmatch(r"(\d{1,4})/(\d{2}|\d{4})\s*Sb\.", normalized)
    if not match:
        return normalized
    law_no, year = match.groups()
    if len(year) == 4:
        return f"{law_no}/{year} Sb."
    year_num = int(year)
    century = 2000 if year_num <= 30 else 1900
    return f"{law_no}/{century + year_num} Sb."


def _confidence_for_stage(stage: str) -> float:
    if stage == "Law ID Mention":
        return _STAGE_CONFIDENCE["Law ID Mention"]
    if stage == "Typed: Foreign Law":
        return _STAGE_CONFIDENCE["Typed: Foreign Law"]
    if stage == "Filtered: Internal Regulation":
        return _STAGE_CONFIDENCE["Filtered: Internal Regulation"]
    if stage == "Filtered: Non-Statutory Collection":
        return _STAGE_CONFIDENCE["Filtered: Non-Statutory Collection"]
    if stage.startswith("Level 5A"):
        return _STAGE_CONFIDENCE["Level 5A: Ambiguous Local Alias + Context"]
    if stage.startswith("Level 5"):
        return _STAGE_CONFIDENCE["Level 5: Local Dictionary"]
    if stage.startswith("Level 6A"):
        return _STAGE_CONFIDENCE["Level 6A: Ambiguous Global Alias + Context"]
    if stage.startswith("Level 6"):
        return _STAGE_CONFIDENCE["Level 6: Global Dictionary"]
    return _STAGE_CONFIDENCE.get(stage, 0.0)


@dataclass(frozen=True, slots=True)
class AliasMatcher:
    alias: str
    law_ids: tuple[str, ...]
    needle: str | None
    match_mode: str
    source: str

    @property
    def is_ambiguous(self) -> bool:
        return len(self.law_ids) > 1


def _alias_default_match_mode(alias: str) -> str:
    compact = re.sub(r"[^\wá-ž]+", "", alias)
    if not compact:
        return "exact"
    tokens = alias.split()
    if "." in alias and not (
        len(tokens) >= 2
        and tokens[0].endswith(".")
        and all(token.replace(".", "").isalpha() for token in tokens[1:])
    ):
        return "exact"
    if "/" in alias:
        return "exact"
    if compact.isupper() and len(compact) <= 6:
        return "exact"
    if any(ch.isupper() for ch in compact[1:]):
        return "exact"
    return "inflect"


def _inflectional_word_pattern(word: str) -> str:
    lower = word.lower()
    escaped = re.escape(word)
    if lower == "zákon":
        stem = re.escape(word)
        return stem + r"(?:|a|u|em|e)"
    if lower == "zákoník":
        stem = re.escape(word)
        return stem + r"(?:|u|em|a|e|ovi)"
    if lower == "nařízení":
        stem = re.escape(word)
        return stem + r"(?:|m|ch|mi)"
    if lower == "řízení":
        stem = re.escape(word)
        return stem + r"(?:|m|ch|mi)"
    if lower.endswith("řád"):
        stem = re.escape(word)
        return stem + r"(?:|u|em|a|e)"
    if len(word) <= 3:
        return escaped
    if lower.endswith("a"):
        stem = re.escape(word[:-1])
        return stem + r"(?:a|y|ě|e|u|ou)"
    if lower.endswith(("ý", "í")):
        stem = re.escape(word[:-1])
        if lower.endswith("í"):
            return stem + r"(?:í|ího|ímu|ím|ímž|ích|ími)"
        return stem + r"(?:ý|ého|ému|ém|ým|á|é|ou)"
    if lower.endswith("ík"):
        stem = re.escape(word)
        return stem + r"(?:|u|em|a|ovi)"
    if lower.endswith("on"):
        stem = re.escape(word)
        return stem + r"(?:|a|u|em|ě)"
    if lower.endswith("soud"):
        stem = re.escape(word)
        return stem + r"(?:|u|em|a)"
    return escaped


@lru_cache(maxsize=8192)
def _alias_regex(alias: str, match_mode: str = "inflect") -> re.Pattern:
    words = alias.split()
    regex_parts = []
    for word in words:
        if not word.isalpha():
            escaped = re.escape(word)
            escaped = escaped.replace(r"\.", r"\.\s*")
            regex_parts.append(escaped)
        elif match_mode == "exact":
            regex_parts.append(re.escape(word))
        else:
            regex_parts.append(_inflectional_word_pattern(word))
    alias_regex = r"(?<!\w)" + r"\s*".join(regex_parts) + r"(?!\w)"
    return re.compile(alias_regex, re.IGNORECASE)


def check_alias_in_context(alias: str, context: str, match_mode: str = "inflect") -> bool:
    return bool(_alias_regex(alias, match_mode).search(context))


def _dedupe_anomalies(entries: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in entries:
        if item.get("entry_id"):
            key = ("entry_id", str(item.get("entry_id")))
        elif item.get("document_path") and item.get("raw_start") is not None and item.get("raw_end") is not None:
            key = (
                "document_span",
                str(item.get("document_path")),
                int(item.get("raw_start")),
                int(item.get("raw_end")),
                str(item.get("citation_type", "")),
            )
        else:
            key = (
                item.get("target_reference", "").strip(),
                item.get("context_block", "").strip(),
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _anomaly_entry_id(
    document_source: str | None,
    document_id: str | None,
    raw_start: int,
    raw_end: int,
    citation_type: str,
) -> str | None:
    if not document_id:
        return None
    source = document_source or "unknown"
    return f"{source}::{document_id}::{raw_start}:{raw_end}:{citation_type}"


def append_anomalies_to_file(anomalies: list[dict], output_anomaly_json: str) -> None:
    if not anomalies:
        return

    output_dir = os.path.dirname(output_anomaly_json)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    existing_anomalies = []
    if os.path.exists(output_anomaly_json):
        with open(output_anomaly_json, "r", encoding="utf-8") as f:
            try:
                existing_anomalies = json.load(f)
            except json.JSONDecodeError:
                pass

    existing_anomalies.extend(anomalies)
    existing_anomalies = _dedupe_anomalies(existing_anomalies)

    with open(output_anomaly_json, "w", encoding="utf-8") as f:
        json.dump(existing_anomalies, f, ensure_ascii=False, indent=4)


def _is_short_structural_reference(context: str) -> bool:
    snippet = context.strip()
    if not snippet:
        return False
    if snippet[-1] not in ".,;:)":
        return False
    words = re.findall(r"[a-zá-ž]+", snippet, flags=re.IGNORECASE)
    if not words:
        return False
    return all(len(word) <= 4 for word in words)


def _extract_forward_context(text: str, start_idx: int) -> str:
    max_end = min(len(text), start_idx + FORWARD_CONTEXT_MAX_CHARS)
    snippet = text[start_idx:max_end]

    if len(snippet) <= FORWARD_CONTEXT_MIN_CHARS:
        return snippet

    tail = snippet[FORWARD_CONTEXT_MIN_CHARS:]
    boundary = re.search(r"(?:[.!?;:]\s+|\n{2,})", tail)
    if boundary:
        cut = FORWARD_CONTEXT_MIN_CHARS + boundary.end()
        return snippet[:cut]
    return snippet


def _truncate_sentence_context(context: str, limit: int) -> str:
    snippet = context[:limit]
    boundary = re.search(r"(?:[.!?;:]\s+|\n{2,})", snippet)
    if boundary:
        return snippet[: boundary.start()]
    return snippet


def _trim_backward_resolution_context(context: str) -> str:
    """
    Keep only the most recent clause-sized tail for backward resolution.

    We deliberately ignore plain periods here because legal abbreviations such as
    ``čl.`` or ``o. s. ř.`` would otherwise break the context too aggressively.
    """
    boundary = None
    for match in re.finditer(r"(?:[;:!?]\s+|\n{2,})", context):
        boundary = match
    if boundary is None:
        return context
    return context[boundary.end() :]


def _extract_document_generic_law(text: str) -> str | None:
    alias_match = GENERIC_LAW_ALIAS_PATTERN.search(text)
    if alias_match:
        window = text[max(0, alias_match.start() - 900) : alias_match.start()]
        intro_match = GENERIC_LAW_INTRO_PATTERN.search(window)
        if intro_match:
            return _normalize_law_id(intro_match.group(1))

    match = GENERIC_LAW_DEFINITION_PATTERN.search(text)
    if not match:
        return None
    return _normalize_law_id(match.group(1))


def _citation_type(anchor: str) -> str:
    a = anchor.lower().strip()
    if a.startswith("§"):
        return "section"
    if a.startswith("čl"):
        return "article"
    return "other_normative"


def _normalize_alias_entry(alias: str, payload: object, source: str) -> AliasMatcher | None:
    law_ids: list[str] = []
    match_mode = _alias_default_match_mode(alias)

    if isinstance(payload, str):
        law_ids = [_normalize_law_id(payload)]
    elif isinstance(payload, list):
        law_ids = [_normalize_law_id(str(item)) for item in payload if str(item).strip()]
    elif isinstance(payload, dict):
        if isinstance(payload.get("law_id"), str):
            law_ids = [_normalize_law_id(payload["law_id"])]
        elif isinstance(payload.get("law_ids"), list):
            law_ids = [
                _normalize_law_id(str(item))
                for item in payload["law_ids"]
                if str(item).strip()
            ]
        if isinstance(payload.get("match_mode"), str):
            match_mode = payload["match_mode"]

    deduped_law_ids = tuple(dict.fromkeys(law_ids))
    if not deduped_law_ids:
        return None

    tokens = alias.split()
    first = tokens[0] if tokens else alias
    normalized = re.sub(r"[^\wá-ž]+", "", first.lower())
    needle = None
    if normalized.isalpha():
        if len(normalized) <= 4 and len(normalized) >= 3:
            needle = normalized
        elif len(normalized) > 4:
            needle = normalized[:-1]
    elif len(normalized) >= 3:
        needle = normalized

    return AliasMatcher(
        alias=alias,
        law_ids=deduped_law_ids,
        needle=needle,
        match_mode=match_mode,
        source=source,
    )


def _build_alias_matchers(alias_map: dict[str, object], source: str) -> list[AliasMatcher]:
    matchers: list[AliasMatcher] = []
    for alias, payload in sorted(alias_map.items(), key=lambda item: len(item[0]), reverse=True):
        matcher = _normalize_alias_entry(alias, payload, source)
        if matcher is not None:
            matchers.append(matcher)
    return matchers


def _match_alias_in_context(
    context: str,
    alias_matchers: list[AliasMatcher],
) -> tuple[tuple[str, ...], str] | None:
    context_lower = context.lower()
    for matcher in alias_matchers:
        if matcher.needle and matcher.needle not in context_lower:
            continue
        if check_alias_in_context(matcher.alias, context, matcher.match_mode):
            return (matcher.law_ids, matcher.alias)
    return None


def _find_alias_in_context(
    context: str,
    alias_matchers: list[AliasMatcher],
) -> tuple[tuple[str, ...], str, int, str] | None:
    context_lower = context.lower()
    best: tuple[tuple[str, ...], str, int, str] | None = None
    for matcher in alias_matchers:
        if matcher.needle and matcher.needle not in context_lower:
            continue
        match = _alias_regex(matcher.alias, matcher.match_mode).search(context)
        if not match:
            continue
        candidate = (matcher.law_ids, matcher.alias, match.start(), matcher.source)
        if best is None or candidate[2] < best[2] or (
            candidate[2] == best[2] and len(matcher.alias) > len(best[1])
        ):
            best = candidate
    return best


def _find_last_alias_in_context(
    context: str,
    alias_matchers: list[AliasMatcher],
) -> tuple[tuple[str, ...], str, int, str] | None:
    context_lower = context.lower()
    best: tuple[tuple[str, ...], str, int, str] | None = None
    for matcher in alias_matchers:
        if matcher.needle and matcher.needle not in context_lower:
            continue
        match = None
        for candidate_match in _alias_regex(matcher.alias, matcher.match_mode).finditer(context):
            match = candidate_match
        if not match:
            continue
        candidate = (matcher.law_ids, matcher.alias, match.start(), matcher.source)
        if best is None or candidate[2] > best[2] or (
            candidate[2] == best[2] and len(matcher.alias) > len(best[1])
        ):
            best = candidate
    return best


def _is_amendment_law_context(context: str, law_start: int) -> bool:
    prefix = context[max(0, law_start - 45) : law_start]
    suffix = context[law_start : min(len(context), law_start + 60)]
    suffix_amendment = re.search(
        r"(?:kterým\s+se\s+mění|mění\s+zákon|doplňuje)",
        suffix,
        flags=re.IGNORECASE,
    )
    return bool(AMENDMENT_CONTEXT_PATTERN.search(prefix) or suffix_amendment)


def _find_non_amendment_law_id(context: str) -> tuple[str, int] | None:
    for match in LAW_ID_PATTERN.finditer(context):
        if _is_amendment_law_context(context, match.start()):
            continue
        return (_normalize_law_id(match.group(1)), match.start())
    for match in LAW_ID_OCR_PATTERN.finditer(context):
        if _is_amendment_law_context(context, match.start()):
            continue
        law_id = _normalize_law_id(f"{match.group(1)}/{match.group(2)} Sb.")
        return (law_id, match.start())
    return None


def _find_last_non_amendment_law_id(context: str) -> tuple[str, int] | None:
    candidates: list[tuple[str, int]] = []
    for match in LAW_ID_PATTERN.finditer(context):
        if _is_amendment_law_context(context, match.start()):
            continue
        candidates.append((_normalize_law_id(match.group(1)), match.start()))
    for match in LAW_ID_OCR_PATTERN.finditer(context):
        if _is_amendment_law_context(context, match.start()):
            continue
        candidates.append((_normalize_law_id(f"{match.group(1)}/{match.group(2)} Sb."), match.start()))
    return candidates[-1] if candidates else None


def _has_hard_boundary(text: str) -> bool:
    return bool(HARD_BOUNDARY_PATTERN.search(text))


def _looks_internal_regulation_context(context: str) -> bool:
    if not ARBITRATION_INTERNAL_PATTERN.search(context):
        return False
    return bool(ARBITRATION_INSTITUTION_PATTERN.search(context))


def _looks_internal_regulation_context_broad(near_context: str, broad_context: str) -> bool:
    if _looks_internal_regulation_context(near_context):
        return True
    if ARBITRATION_RULES_PATTERN.search(near_context):
        return True
    near_lower = near_context.lower()
    if (
        "pravidel" in near_lower
        and "nákladech" in near_lower
        and "rozhodčího řízení" in near_lower
        and "jejich §" in near_lower
    ):
        return True
    if ARBITRATION_ANAPHORA_PATTERN.search(near_context) and ARBITRATION_INSTITUTION_PATTERN.search(broad_context):
        return True
    if re.search(r"\břádu\s+stálého\s+rozhodčího\s+soudu\b", near_context, flags=re.IGNORECASE):
        return True
    return False


def _looks_non_statutory_collection(context: str) -> bool:
    return bool(NON_STATUTE_COLLECTION_PATTERN.search(context))


def _looks_foreign_law_context(context: str) -> bool:
    return bool(FOREIGN_LAW_PATTERN.search(context))


def _foreign_law_cue_position(context: str) -> int | None:
    match = FOREIGN_LAW_PATTERN.search(context)
    if not match:
        return None
    return match.start()


def _extract_group_continuations(
    citation_type: str,
    segment_text: str,
    segment_start: int,
    occupied_starts: set[int],
) -> list[tuple[int, int, str]]:
    """
    Expand grouped citations such as:
    - § 24, 25 o. s. ř.
    - §§ 63, 30 a 31 zák. č. ...

    Returns tuples of (start, end, number_text) for implied continuations.
    """
    if citation_type not in {"section", "article"}:
        return []

    continuations: list[tuple[int, int, str]] = []
    cursor = 0
    found_any = False

    while cursor < len(segment_text):
        match = CONTINUATION_TOKEN_PATTERN.match(segment_text, cursor)
        if not match:
            break

        prefix_text = segment_text[cursor : match.start("number")]
        if "§" in prefix_text.lower() or "čl" in prefix_text.lower():
            break
        if re.search(r"(?:odst|písm|bodu?|věta)\.?\s*$", prefix_text, flags=re.IGNORECASE):
            break

        number_start = segment_start + match.start("number")
        number_end = segment_start + match.end("number")
        number_text = match.group("number")

        if number_start not in occupied_starts:
            continuations.append((number_start, number_end, number_text))

        cursor = match.end("number")
        found_any = True

        tail_after = segment_text[cursor:]
        if re.match(r"\s*(?:zák|o\.\s|s\.\s|AT\b|Listin|Úmluv|Ústav|Řád|Pravidla|téhož|tohoto|citovaného)", tail_after, flags=re.IGNORECASE):
            continue

    if not found_any:
        return []
    return continuations


def _extract_range_continuation(
    anchor: str,
    citation_type: str,
    segment_text: str,
    segment_start: int,
) -> tuple[int | None, int | None, str, str] | None:
    """
    Expand range citations such as:
    - § 1 až § 14 ...
    - čl. 3 až čl. 5 ...

    Returns:
      (second_anchor_start_or_none, second_anchor_end_or_none, number_text, range_surface)
    """
    if citation_type not in {"section", "article"}:
        return None

    match = RANGE_TOKEN_PATTERN.match(segment_text)
    if not match:
        return None

    second_start = None
    second_end = None
    marker = match.group("marker")
    if marker:
        second_start = segment_start + match.start("marker")
        second_end = segment_start + match.end("number")

    range_surface = f"{anchor}{segment_text[:match.end()]}"
    return (second_start, second_end, match.group("number"), range_surface.strip())


def _extract_numeric_continuations(fragment: str, base: str | None = None) -> list[str]:
    pairs = re.findall(
        r"(,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*(\d+[a-z]?)",
        fragment,
        flags=re.IGNORECASE,
    )
    values: list[str] = []
    previous: str | None = _normalize_odst_token(base) if base else None
    for connector, token in pairs:
        connector = connector.strip().lower()
        normalized = _normalize_odst_token(token)
        if connector == "až" and previous and previous.isdigit() and normalized.isdigit():
            start = int(previous)
            end = int(normalized)
            if start < end and end - start <= 10:
                values.extend([str(number) for number in range(start + 1, end + 1)])
            else:
                values.append(normalized)
        else:
            values.append(normalized)
        previous = normalized
    return values


def _extract_letter_continuations(fragment: str, base: str | None = None) -> list[str]:
    pairs = re.findall(
        r"(,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*([a-z])\s*[/)]?(?=\W|$)",
        fragment,
        flags=re.IGNORECASE,
    )
    values: list[str] = []
    previous: str | None = base.lower() if base else None
    for connector, token in pairs:
        connector = connector.strip().lower()
        normalized = token.lower()
        if connector == "až" and previous:
            start_ord = ord(previous)
            end_ord = ord(normalized)
            if start_ord < end_ord and end_ord - start_ord <= 8:
                values.extend([chr(code) for code in range(start_ord + 1, end_ord + 1)])
            else:
                values.append(normalized)
        else:
            values.append(normalized)
        previous = normalized
    return values


def _detail_anchor_start(tail: str) -> str:
    marker = re.search(r"\b(?:odst|písm|bodu?|věta)\.?\b", tail, flags=re.IGNORECASE)
    if not marker:
        return ""
    prefix = tail[: marker.start()]
    if len(prefix) > 24:
        return ""
    if re.search(r"[A-Za-zÁ-ž]{3,}", prefix):
        return ""
    segment = tail[marker.start() : marker.start() + 120]
    # Stop before outer list enumerators such as ", c." that do not belong to
    # the citation detail itself.
    segment = re.split(r",\s+[a-z]\.\s", segment, maxsplit=1, flags=re.IGNORECASE)[0]
    return segment


def _normalize_odst_token(value: str) -> str:
    token = value.strip()
    if token in {"l", "I"}:
        return "1"
    return token


def _parse_reference_detail(anchor: str, detail_context: str) -> dict:
    detail: dict[str, object] = {}
    number_match = re.search(r"\d+[a-z]?", anchor, flags=re.IGNORECASE)
    if number_match:
        detail["number"] = number_match.group(0)

    tail = _detail_anchor_start(detail_context[:180])
    if not tail:
        return detail

    odsts: list[str] = []
    for match in re.finditer(
        r"odst\.?\s*([0-9lI]+[a-z]?)(?=(?:\s|[,.;)]|písm))((?:\s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*\d+[a-z]?)*)",
        tail,
        flags=re.IGNORECASE,
    ):
        first = _normalize_odst_token(match.group(1))
        values = [first] + _extract_numeric_continuations(match.group(2), base=first)
        odsts.extend(values)

    pismena: list[str] = []
    for match in re.finditer(
        r"písm\.?\s*([a-z])\s*[/)]?(?=\W|$)((?:\s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*[a-z]\s*[/)]?(?=\W|$))*)",
        tail,
        flags=re.IGNORECASE,
    ):
        first = match.group(1).lower()
        values = [first]
        values.extend(_extract_letter_continuations(match.group(2), base=first))
        pismena.extend(values)

    body: list[str] = []
    for match in re.finditer(
        r"bodu?\s*(\d+[a-z]?)((?:\s*(?:,|\ba\b|\bči\b|\bnebo\b|\baž\b)\s*\d+[a-z]?)*)",
        tail,
        flags=re.IGNORECASE,
    ):
        first = match.group(1)
        values = [first] + _extract_numeric_continuations(match.group(2), base=first)
        body.extend(values)

    if odsts:
        detail["odst"] = odsts
    if pismena:
        detail["pism"] = [p.lower() for p in pismena]
    if body:
        detail["body"] = body

    return detail


def _build_occurrence(
    citation_text: str,
    citation_type: str,
    normalized_start: int,
    normalized_end: int,
    raw_start: int,
    raw_end: int,
    parsed_detail: dict,
    resolved_law_id: str | None,
    predicted_classification: str,
    resolver_stage: str,
    context: str,
    candidate_law_ids: list[str],
) -> CitationOccurrence:
    confidence = _confidence_for_stage(resolver_stage)
    quality_reason: str | None = None
    if predicted_classification == "czech_unresolved":
        quality_reason = "unresolved"
    elif predicted_classification == "foreign_law":
        quality_reason = "foreign_law"
    elif confidence < LOW_CONFIDENCE_THRESHOLD:
        if "Ambiguous" in resolver_stage:
            quality_reason = "ambiguous_alias"
        elif "Short Structural" in resolver_stage:
            quality_reason = "short_structural_reference"
        elif "Carryover" in resolver_stage or "Implicit Generic" in resolver_stage:
            quality_reason = "weak_contextual_evidence"
        else:
            quality_reason = "low_confidence"
    quality_flag = (
        predicted_classification in {"czech_unresolved", "foreign_law"}
        or confidence < LOW_CONFIDENCE_THRESHOLD
    )
    return CitationOccurrence(
        citation_text=citation_text,
        citation_type=citation_type,
        normalized_start=normalized_start,
        normalized_end=normalized_end,
        raw_start=raw_start,
        raw_end=raw_end,
        parsed_detail=parsed_detail,
        resolved_law_id=resolved_law_id,
        predicted_classification=predicted_classification,
        resolver_stage=resolver_stage,
        confidence=confidence,
        quality_flag=quality_flag,
        quality_reason=quality_reason,
        context=context,
        candidate_law_ids=candidate_law_ids,
    )


def _candidate_law_ids_from_match(match: tuple[tuple[str, ...], str, int, str] | None) -> list[str]:
    if not match:
        return []
    return list(match[0])


def _is_abbreviation_alias(alias: str | None) -> bool:
    if not alias:
        return False
    compact = re.sub(r"[^\wá-ž]+", "", alias)
    return "." in alias or (compact.isalpha() and len(compact) <= 5 and compact.isupper())


def _choose_resolution_signal(
    local_match: tuple[tuple[str, ...], str, int, str] | None,
    global_match: tuple[tuple[str, ...], str, int, str] | None,
    direct_match: tuple[str, int] | None,
    preferred_law: str | None = None,
) -> tuple[str, str | None, str | None] | None:
    if direct_match and local_match:
        law_ids, alias, pos, _ = local_match
        direct_id, direct_pos = direct_match
        if len(law_ids) == 1 and law_ids[0] != direct_id and _is_abbreviation_alias(alias) and pos < direct_pos:
            return (law_ids[0], alias, "local")
    if direct_match and global_match:
        law_ids, alias, pos, _ = global_match
        direct_id, direct_pos = direct_match
        if len(law_ids) == 1 and law_ids[0] != direct_id and _is_abbreviation_alias(alias) and pos < direct_pos:
            return (law_ids[0], alias, "global")

    candidates: list[tuple[int, str, str, str | None, str | None]] = []
    if direct_match:
        law_id, pos = direct_match
        candidates.append((300 - pos, "direct", law_id, None, None))
    if local_match:
        law_ids, alias, pos, _ = local_match
        if len(law_ids) == 1:
            candidates.append((260 - pos, "local", law_ids[0], alias, None))
        elif preferred_law and preferred_law in law_ids:
            candidates.append((210 - pos, "local_ambiguous", preferred_law, alias, "ambiguous"))
    if global_match:
        law_ids, alias, pos, _ = global_match
        if len(law_ids) == 1:
            candidates.append((230 - pos, "global", law_ids[0], alias, None))
        elif preferred_law and preferred_law in law_ids:
            candidates.append((180 - pos, "global_ambiguous", preferred_law, alias, "ambiguous"))
    if not candidates:
        return None

    _, kind, law_id, alias, meta = max(candidates, key=lambda item: (item[0], item[1]))
    return (law_id, alias, meta or kind)


def _latest_effective_from_on_or_before(
    law_id: str,
    document_date_iso: str | None,
    law_timelines: dict[str, dict] | None,
) -> str | None:
    if not document_date_iso or not law_timelines:
        return None
    row = law_timelines.get(law_id)
    if not isinstance(row, dict):
        return None
    timeline = row.get("timeline")
    if not isinstance(timeline, list):
        return None

    eligible: list[str] = []
    for version in timeline:
        if not isinstance(version, dict):
            continue
        effective_from = version.get("effective_from")
        if isinstance(effective_from, str) and effective_from <= document_date_iso:
            eligible.append(effective_from)
    if not eligible:
        return None
    return max(eligible)


def _choose_temporal_law(
    law_ids: tuple[str, ...],
    document_date_iso: str | None,
    law_timelines: dict[str, dict] | None,
) -> str | None:
    """
    Conservative temporal disambiguation for historically ambiguous aliases.

    Among candidate laws that have at least one known version effective on or
    before the document date, prefer the one with the most recent such version
    start. If candidates tie, keep the alias ambiguous.
    """
    if len(law_ids) <= 1 or not document_date_iso or not law_timelines:
        return None

    candidates: list[tuple[str, str]] = []
    for law_id in law_ids:
        latest = _latest_effective_from_on_or_before(law_id, document_date_iso, law_timelines)
        if latest is not None:
            candidates.append((latest, law_id))
    if not candidates:
        return None

    best_latest, best_law = max(candidates, key=lambda item: (item[0], item[1]))
    if sum(1 for latest, _ in candidates if latest == best_latest) > 1:
        return None
    return best_law


def _choose_backward_signal(
    local_match: tuple[tuple[str, ...], str, int, str] | None,
    global_match: tuple[tuple[str, ...], str, int, str] | None,
    direct_match: tuple[str, int] | None,
    preferred_law: str | None = None,
) -> tuple[str, str | None, str | None] | None:
    candidates: list[tuple[int, str, str, str | None, str | None]] = []
    if direct_match:
        law_id, pos = direct_match
        candidates.append((100 + pos, "direct", law_id, None, None))
    if local_match:
        law_ids, alias, pos, _ = local_match
        if len(law_ids) == 1:
            candidates.append((80 + pos, "local", law_ids[0], alias, None))
        elif preferred_law and preferred_law in law_ids:
            candidates.append((70 + pos, "local_ambiguous", preferred_law, alias, "ambiguous"))
    if global_match:
        law_ids, alias, pos, _ = global_match
        if len(law_ids) == 1:
            candidates.append((60 + pos, "global", law_ids[0], alias, None))
        elif preferred_law and preferred_law in law_ids:
            candidates.append((50 + pos, "global_ambiguous", preferred_law, alias, "ambiguous"))
    if not candidates:
        return None

    _, kind, law_id, alias, meta = max(candidates, key=lambda item: (item[0], item[1]))
    return (law_id, alias, meta or kind)


def extract_citation_occurrences(
    text: str,
    local_aliases: dict[str, object],
    global_aliases: dict[str, object],
    document_metadata: dict | None = None,
    law_timelines: dict[str, dict] | None = None,
) -> list[CitationOccurrence]:
    """
    Citation-first extraction entrypoint.

    Returns all citation occurrences with resolution metadata. Callers can derive
    resolved outputs and anomaly/review queues from this list.
    """
    doc = normalize_for_matching(text)
    normalized_text = doc.normalized_text
    generic_topic_law = _extract_document_generic_law(normalized_text)
    document_date_iso = None
    if isinstance(document_metadata, dict):
        document_date_iso = document_metadata.get("decision_date_iso")
        if not isinstance(document_date_iso, str):
            document_date_iso = document_metadata.get("decision_date_lower_bound")
            if not isinstance(document_date_iso, str):
                document_date_iso = None

    local_matchers = _build_alias_matchers(local_aliases, "local")
    global_matchers = _build_alias_matchers(global_aliases, "global")

    occurrences: list[CitationOccurrence] = []
    active_law = None
    recent_strong_law: str | None = None
    recent_strong_law_end: int | None = None
    recent_context_law: str | None = None
    recent_context_law_end: int | None = None
    recent_section_refs: dict[str, tuple[str, int]] = {}
    candidate_laws: set[str] = set()
    last_event_end: int | None = None
    suppressed_anchor_starts: set[int] = set()

    events: list[tuple[int, int, str, re.Match]] = []
    section_matches = list(SECTION_PATTERN.finditer(normalized_text))
    for match in section_matches:
        events.append((match.start(), match.end(), "section", match))
    for match in LAW_ID_PATTERN.finditer(normalized_text):
        events.append((match.start(1), match.end(1), "law_id_mention", match))
    events.sort(key=lambda item: (item[0], 0 if item[2] == "section" else 1))

    section_start_positions = [match.start() for match in section_matches]
    occupied_section_starts = set(section_start_positions)

    for start_idx, end_idx, event_type, match in events:
        if event_type == "section" and start_idx in suppressed_anchor_starts:
            last_event_end = end_idx
            continue

        if last_event_end is not None and _has_hard_boundary(normalized_text[last_event_end:start_idx]):
            active_law = None

        if event_type == "law_id_mention":
            law_id = _normalize_law_id(match.group(1))
            surrounding = normalized_text[max(0, start_idx - 40) : min(len(normalized_text), end_idx + 40)]
            predicted_classification = "czech_resolved"
            stage = "Law ID Mention"
            if _looks_non_statutory_collection(surrounding):
                predicted_classification = "non_citation"
                stage = "Filtered: Non-Statutory Collection"
            elif NON_STATUTE_PUBLICATION_PATTERN.search(
                normalized_text[max(0, start_idx - 90) : min(len(normalized_text), end_idx + 90)]
            ) and "zákon č." not in surrounding.lower():
                predicted_classification = "non_citation"
                stage = "Filtered: Non-Statutory Collection"
            else:
                candidate_laws.add(law_id)
                if not _is_amendment_law_context(normalized_text, start_idx):
                    active_law = law_id
                    recent_strong_law = law_id
                    recent_strong_law_end = end_idx
                    recent_context_law = law_id
                    recent_context_law_end = end_idx

            raw_start, raw_end = doc.normalized_span_to_raw(start_idx, end_idx)
            occurrences.append(
                _build_occurrence(
                    citation_text=law_id,
                    citation_type="other_normative",
                    normalized_start=start_idx,
                    normalized_end=end_idx,
                    raw_start=raw_start,
                    raw_end=raw_end,
                    parsed_detail={},
                    resolved_law_id=law_id if predicted_classification == "czech_resolved" else None,
                    predicted_classification=predicted_classification,
                    resolver_stage=stage,
                    context=surrounding,
                    candidate_law_ids=sorted(candidate_laws),
                )
            )
            last_event_end = end_idx
            continue

        anchor = match.group(0).strip()
        forward_context = _extract_forward_context(normalized_text, start_idx)
        near_context = forward_context[:NEAR_CONTEXT_CHARS]
        backward_start = max(0, start_idx - BACKWARD_CONTEXT_CHARS)
        backward_context = normalized_text[backward_start:start_idx]
        backward_resolution_start = max(0, start_idx - BACKWARD_RESOLUTION_CHARS)
        backward_resolution_context = _trim_backward_resolution_context(
            normalized_text[backward_resolution_start:start_idx]
        )
        anchor_context = normalized_text[backward_start : min(len(normalized_text), start_idx + NEAR_CONTEXT_CHARS)]
        broad_context = normalized_text[max(0, start_idx - 240) : min(len(normalized_text), start_idx + FORWARD_CONTEXT_MAX_CHARS)]
        next_anchor_start = next((pos for pos in section_start_positions if pos > start_idx), None)
        detail_tail_end = min(len(normalized_text), end_idx + 180)
        detail_context = normalized_text[end_idx:detail_tail_end]
        if next_anchor_start is not None:
            prefix_to_next_anchor = normalized_text[end_idx:next_anchor_start]
            if not re.match(r"^\s*až\s*$", prefix_to_next_anchor, flags=re.IGNORECASE):
                detail_context = normalized_text[end_idx:next_anchor_start]
        parsed_detail = _parse_reference_detail(anchor, detail_context)

        resolved_id = None
        predicted_classification = "czech_unresolved"
        stage = "Level 7: Unresolved"
        should_promote_active_law = False

        local_near = _find_alias_in_context(near_context, local_matchers)
        global_near = _find_alias_in_context(near_context, global_matchers)
        direct_near = _find_non_amendment_law_id(near_context)
        boundary_match = HARD_BOUNDARY_PATTERN.search(near_context)
        if boundary_match and GENERIC_REFERENCE_PATTERN.search(near_context[: boundary_match.start()]):
            if local_near is not None and local_near[2] >= boundary_match.start():
                local_near = None
            if global_near is not None and global_near[2] >= boundary_match.start():
                global_near = None
            if direct_near is not None and direct_near[1] >= boundary_match.start():
                direct_near = None
        direct_broad = _find_non_amendment_law_id(forward_context)
        if boundary_match and GENERIC_REFERENCE_PATTERN.search(near_context[: boundary_match.start()]):
            if direct_broad is not None and direct_broad[1] >= boundary_match.start():
                direct_broad = None
        local_back = _find_last_alias_in_context(backward_resolution_context, local_matchers)
        global_back = _find_last_alias_in_context(backward_resolution_context, global_matchers)
        direct_back = _find_last_non_amendment_law_id(backward_resolution_context)
        fallback_law = None
        if recent_strong_law is not None and recent_strong_law_end is not None:
            if start_idx - recent_strong_law_end <= RECENT_STRONG_LAW_MAX_DISTANCE:
                fallback_law = recent_strong_law
        local_recent_law = None
        if recent_strong_law is not None and recent_strong_law_end is not None:
            if start_idx - recent_strong_law_end <= GENERIC_TOPIC_OVERRIDE_DISTANCE:
                local_recent_law = recent_strong_law
        context_fallback_law = None
        if recent_context_law is not None and recent_context_law_end is not None:
            if start_idx - recent_context_law_end <= RECENT_CONTEXT_LAW_MAX_DISTANCE:
                context_fallback_law = recent_context_law

        local_broad = _find_alias_in_context(forward_context, local_matchers)
        global_broad = _find_alias_in_context(forward_context, global_matchers)

        for law_id in _candidate_law_ids_from_match(local_near):
            candidate_laws.add(law_id)
        for law_id in _candidate_law_ids_from_match(global_near):
            candidate_laws.add(law_id)
        for law_id in _candidate_law_ids_from_match(local_broad):
            candidate_laws.add(law_id)
        for law_id in _candidate_law_ids_from_match(global_broad):
            candidate_laws.add(law_id)

        foreign_context = near_context
        foreign_cue_pos = _foreign_law_cue_position(foreign_context)
        nearest_named_signal_pos = None
        for signal in (local_near, global_near):
            if signal is None:
                continue
            pos = signal[2]
            if nearest_named_signal_pos is None or pos < nearest_named_signal_pos:
                nearest_named_signal_pos = pos

        generic_preferred_law = generic_topic_law
        if generic_topic_law is not None and recent_strong_law_end is not None:
            if start_idx - recent_strong_law_end <= 300:
                generic_preferred_law = None

        primary_active_law = active_law
        primary_fallback_law = fallback_law
        if generic_topic_law is not None:
            primary_active_law = local_recent_law or primary_active_law
            primary_fallback_law = local_recent_law or None

        preferred_law = primary_active_law or primary_fallback_law or generic_preferred_law or context_fallback_law
        temporal_preferred_law = None
        for signal in (local_near, global_near, local_broad, global_broad, local_back, global_back):
            if signal is None:
                continue
            temporal_law = _choose_temporal_law(signal[0], document_date_iso, law_timelines)
            if temporal_law is not None:
                temporal_preferred_law = temporal_law
                break
        if temporal_preferred_law is not None and any(
            signal is not None and len(signal[0]) > 1
            for signal in (local_near, global_near, local_broad, global_broad)
        ):
            preferred_law = temporal_preferred_law
        elif preferred_law is None:
            preferred_law = temporal_preferred_law
        generic_topic_signal = None
        if (
            generic_topic_law is not None
            and GENERIC_REFERENCE_PATTERN.search(near_context)
            and not local_near
            and not global_near
            and not direct_near
        ):
            generic_topic_signal = generic_topic_law

        near_signal = _choose_resolution_signal(
            local_near,
            global_near,
            direct_near,
            preferred_law=preferred_law,
        )
        backward_signal = _choose_backward_signal(
            local_back,
            global_back,
            direct_back,
            preferred_law=preferred_law,
        )
        broad_signal = _choose_resolution_signal(
            local_broad,
            global_broad,
            direct_broad,
            preferred_law=preferred_law,
        )
        has_unmatched_local_law_cue = (
            LOCAL_LAW_CUE_PATTERN.search(near_context[:LOCAL_LAW_CUE_WINDOW_CHARS]) is not None
            and not ANAPHORA_PATTERN.search(anchor_context)
            and not local_near
            and not global_near
            and not direct_near
            and not local_broad
            and not global_broad
            and not direct_broad
        )

        section_reference_law = None
        section_number = str(parsed_detail.get("number") or "").strip()
        if (
            occurrence_type := _citation_type(anchor)
        ) == "section" and section_number and SECTION_ANAPHORA_PATTERN.search(anchor_context):
            previous_ref = recent_section_refs.get(section_number)
            if previous_ref is not None:
                law_id, ref_end = previous_ref
                if start_idx - ref_end <= RECENT_SECTION_REFERENCE_MAX_DISTANCE:
                    section_reference_law = law_id

        if _looks_internal_regulation_context_broad(anchor_context, broad_context) and not local_near and not global_near and not direct_near:
            predicted_classification = "non_citation"
            stage = "Filtered: Internal Regulation"
        elif (
            foreign_cue_pos is not None
            and not direct_near
            and (nearest_named_signal_pos is None or foreign_cue_pos <= nearest_named_signal_pos)
        ):
            predicted_classification = "foreign_law"
            stage = "Typed: Foreign Law"
        elif section_reference_law is not None:
            resolved_id = section_reference_law
            predicted_classification = "czech_resolved"
            stage = "Level 2A: Section Anaphora"
        elif near_signal and near_signal[1] is None:
            resolved_id = near_signal[0]
            predicted_classification = "czech_resolved"
            stage = "Level 1: Direct Match"
            should_promote_active_law = True
        elif near_signal:
            resolved_id, alias, signal_kind = near_signal
            predicted_classification = "czech_resolved"
            if signal_kind == "ambiguous" and local_near and alias == local_near[1]:
                stage = f"Level 5A: Ambiguous Local Alias + Context ('{alias}')"
            elif signal_kind == "ambiguous":
                stage = f"Level 6A: Ambiguous Global Alias + Context ('{alias}')"
            elif local_near and alias == local_near[1]:
                stage = f"Level 5: Local Dictionary ('{alias}')"
            else:
                stage = f"Level 6: Global Dictionary ('{alias}')"
            should_promote_active_law = True
        elif ANAPHORA_PATTERN.search(anchor_context) and (
            active_law is not None
            or fallback_law is not None
            or context_fallback_law is not None
            or generic_topic_law is not None
        ):
            if backward_signal is not None:
                resolved_id = backward_signal[0]
            else:
                resolved_id = primary_active_law or primary_fallback_law or generic_preferred_law or context_fallback_law
            predicted_classification = "czech_resolved"
            stage = "Level 2: Explicit Anaphora"
        else:
            if broad_signal and broad_signal[1] is None:
                resolved_id = broad_signal[0]
                predicted_classification = "czech_resolved"
                stage = "Level 1: Direct Match"
                should_promote_active_law = True
            elif generic_topic_signal is not None:
                resolved_id = generic_topic_signal
                predicted_classification = "czech_resolved"
                stage = "Level 3: Implicit Generic Reference"
            elif GENERIC_REFERENCE_PATTERN.search(near_context) and (
                active_law is not None
                or fallback_law is not None
                or context_fallback_law is not None
                or generic_topic_law is not None
            ) and not has_unmatched_local_law_cue:
                resolved_id = primary_active_law or primary_fallback_law or generic_preferred_law or context_fallback_law
                predicted_classification = "czech_resolved"
                stage = "Level 3: Implicit Generic Reference"
            elif (
                backward_signal is not None
                and not local_broad
                and not global_broad
                and not direct_broad
                and not has_unmatched_local_law_cue
            ):
                resolved_id = backward_signal[0]
                predicted_classification = "czech_resolved"
                stage = "Level 3C: Backward Context Carryover"
            elif broad_signal:
                resolved_id, alias, signal_kind = broad_signal
                predicted_classification = "czech_resolved"
                if signal_kind == "ambiguous" and local_broad and alias == local_broad[1]:
                    stage = f"Level 5A: Ambiguous Local Alias + Context ('{alias}')"
                elif signal_kind == "ambiguous":
                    stage = f"Level 6A: Ambiguous Global Alias + Context ('{alias}')"
                elif local_broad and alias == local_broad[1]:
                    stage = f"Level 5: Local Dictionary ('{alias}')"
                else:
                    stage = f"Level 6: Global Dictionary ('{alias}')"
                should_promote_active_law = True
            elif (
                recent_context_law is not None
                and last_event_end is not None
                and start_idx - last_event_end <= CHAIN_CARRYOVER_MAX_DISTANCE
                and not _has_hard_boundary(normalized_text[last_event_end:start_idx])
                and not has_unmatched_local_law_cue
            ):
                resolved_id = recent_context_law
                predicted_classification = "czech_resolved"
                stage = "Level 3B: Citation Chain Carryover"
            elif (
                _is_short_structural_reference(near_context)
                and active_law is not None
                and not has_unmatched_local_law_cue
            ):
                resolved_id = active_law
                predicted_classification = "czech_resolved"
                stage = "Level 4: Short Structural Reference"

        if should_promote_active_law and resolved_id is not None:
            active_law = resolved_id
            recent_strong_law = resolved_id
            recent_strong_law_end = end_idx
            candidate_laws.add(resolved_id)
        if predicted_classification == "czech_resolved" and resolved_id is not None:
            recent_context_law = resolved_id
            recent_context_law_end = end_idx
            candidate_laws.add(resolved_id)
            if occurrence_type == "section" and section_number:
                recent_section_refs[section_number] = (resolved_id, end_idx)

        raw_start, raw_end = doc.normalized_span_to_raw(start_idx, end_idx)
        occurrence = _build_occurrence(
            citation_text=anchor,
            citation_type=_citation_type(anchor),
            normalized_start=start_idx,
            normalized_end=end_idx,
            raw_start=raw_start,
            raw_end=raw_end,
            parsed_detail=parsed_detail,
            resolved_law_id=resolved_id,
            predicted_classification=predicted_classification,
            resolver_stage=stage,
            context=forward_context,
            candidate_law_ids=sorted(candidate_laws),
        )
        range_ref = _extract_range_continuation(
            anchor=anchor,
            citation_type=occurrence.citation_type,
            segment_text=normalized_text[end_idx:detail_tail_end],
            segment_start=end_idx,
        )
        if range_ref:
            range_anchor_start, range_anchor_end, _, range_surface = range_ref
            occurrence.citation_text = range_surface
        occurrences.append(occurrence)

        if range_ref:
            range_anchor_start, range_anchor_end, range_number, range_surface = range_ref
            if range_anchor_start is not None:
                suppressed_anchor_starts.add(range_anchor_start)
            range_norm_start = occurrence.normalized_start
            range_norm_end = occurrence.normalized_end
            range_raw_start = occurrence.raw_start
            range_raw_end = occurrence.raw_end
            if range_anchor_start is not None and range_anchor_end is not None:
                range_norm_start = range_anchor_start
                range_norm_end = range_anchor_end
                range_raw_start, range_raw_end = doc.normalized_span_to_raw(range_anchor_start, range_anchor_end)
            range_occurrence = _build_occurrence(
                citation_text=range_surface,
                citation_type=occurrence.citation_type,
                normalized_start=range_norm_start,
                normalized_end=range_norm_end,
                raw_start=range_raw_start,
                raw_end=range_raw_end,
                parsed_detail={**occurrence.parsed_detail, "number": range_number},
                resolved_law_id=occurrence.resolved_law_id,
                predicted_classification=occurrence.predicted_classification,
                resolver_stage=occurrence.resolver_stage,
                context=forward_context,
                candidate_law_ids=sorted(candidate_laws),
            )
            occurrences.append(range_occurrence)

        group_segment_end = detail_tail_end
        if next_anchor_start is not None:
            prefix_to_next_anchor = normalized_text[end_idx:next_anchor_start]
            if not re.match(r"^\s*až\s*$", prefix_to_next_anchor, flags=re.IGNORECASE):
                group_segment_end = min(group_segment_end, next_anchor_start)
        group_segment = normalized_text[end_idx:group_segment_end]
        continuation_refs = _extract_group_continuations(
            citation_type=occurrence.citation_type,
            segment_text=group_segment,
            segment_start=end_idx,
            occupied_starts=occupied_section_starts,
        )
        for cont_start, cont_end, cont_number in continuation_refs:
            raw_cont_start, raw_cont_end = doc.normalized_span_to_raw(cont_start, cont_end)
            cont_occurrence = _build_occurrence(
                citation_text=normalized_text[cont_start:cont_end],
                citation_type=occurrence.citation_type,
                normalized_start=cont_start,
                normalized_end=cont_end,
                raw_start=raw_cont_start,
                raw_end=raw_cont_end,
                parsed_detail={"number": cont_number},
                resolved_law_id=occurrence.resolved_law_id,
                predicted_classification=occurrence.predicted_classification,
                resolver_stage=occurrence.resolver_stage,
                context=forward_context,
                candidate_law_ids=sorted(candidate_laws),
            )
            occurrences.append(cont_occurrence)
            occupied_section_starts.add(cont_start)

        last_event_end = end_idx

    return occurrences


def _build_anomaly_entry(
    occurrence: CitationOccurrence,
    full_text: str,
    route_reason: str | None = None,
    document_id: str | None = None,
    document_path: str | None = None,
    document_source: str | None = None,
) -> dict:
    ctx_start = max(0, occurrence.raw_start - ANOMALY_CONTEXT_RADIUS)
    ctx_end = min(len(full_text), occurrence.raw_end + ANOMALY_CONTEXT_RADIUS)
    context_block = full_text[ctx_start:ctx_end].replace("\n", " ")

    payload = {
        "target_reference": occurrence.citation_text,
        "context_block": context_block,
        "candidates": occurrence.candidate_law_ids,
        "resolver_stage": occurrence.resolver_stage,
        "confidence": occurrence.confidence,
        "citation_type": occurrence.citation_type,
        "raw_start": occurrence.raw_start,
        "raw_end": occurrence.raw_end,
    }
    entry_id = _anomaly_entry_id(
        document_source=document_source,
        document_id=document_id,
        raw_start=occurrence.raw_start,
        raw_end=occurrence.raw_end,
        citation_type=occurrence.citation_type,
    )
    if entry_id is not None:
        payload["entry_id"] = entry_id
    if document_id is not None:
        payload["document_id"] = document_id
    if document_path is not None:
        payload["document_path"] = document_path
    if document_source is not None:
        payload["source"] = document_source
    if route_reason is not None:
        payload["route_reason"] = route_reason
    return payload


def _is_llm_ambiguity_candidate(occurrence: CitationOccurrence) -> tuple[bool, str | None]:
    """
    Keep the human review signal broad but the LLM queue narrow.

    Resolved citations are queued only when they are both low-confidence and
    exhibit additional ambiguity signals that an LLM could realistically help
    with.
    """
    label = occurrence.predicted_classification
    stage = occurrence.resolver_stage
    candidate_count = len(occurrence.candidate_law_ids)

    if label == "czech_unresolved":
        return True, "unresolved"
    if label == "foreign_law":
        return True, "foreign_law"
    if label != "czech_resolved":
        return False, None
    if occurrence.confidence >= LOW_CONFIDENCE_THRESHOLD:
        return False, None

    # Generic carryover often produces broad candidate sets even when the
    # deterministic resolver is already behaving correctly. We only send
    # resolved citations to the LLM when the resolver itself marked the path
    # as genuinely ambiguous.
    if "Ambiguous" in stage:
        return True, "low_confidence_ambiguous_alias"

    return False, None


def append_occurrences_to_jsonl(
    occurrences: list[CitationOccurrence],
    output_jsonl: str,
    document_id: str | None = None,
) -> None:
    if not occurrences:
        return
    output_dir = os.path.dirname(output_jsonl)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_jsonl, "a", encoding="utf-8") as f:
        for occ in occurrences:
            row = occ.to_dict()
            if document_id is not None:
                row["document_id"] = document_id
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def occurrences_to_resolved_and_anomalies(
    occurrences: list[CitationOccurrence],
    text: str,
    include_low_confidence_anomalies: bool = False,
    document_id: str | None = None,
    document_path: str | None = None,
    document_source: str | None = None,
) -> tuple[list[dict], list[dict]]:
    resolved_refs: list[dict] = []
    anomalies: list[dict] = []

    for occ in occurrences:
        if occ.predicted_classification == "non_citation":
            continue
        if occ.predicted_classification == "foreign_law":
            if include_low_confidence_anomalies:
                should_queue, route_reason = _is_llm_ambiguity_candidate(occ)
                if should_queue:
                    anomalies.append(
                        _build_anomaly_entry(
                            occ,
                            text,
                            route_reason=route_reason,
                            document_id=document_id,
                            document_path=document_path,
                            document_source=document_source,
                        )
                    )
            continue
        if occ.resolved_law_id is not None:
            resolved_refs.append(
                {
                    "anchor": occ.citation_text,
                    "resolved_law_id": occ.resolved_law_id,
                    "level": occ.resolver_stage,
                    "context": occ.context,
                    "confidence": occ.confidence,
                    "quality_flag": occ.quality_flag,
                    "quality_reason": occ.quality_reason,
                    "parsed_detail": occ.parsed_detail,
                    "predicted_classification": occ.predicted_classification,
                }
            )
            if include_low_confidence_anomalies:
                should_queue, route_reason = _is_llm_ambiguity_candidate(occ)
                if should_queue:
                    anomalies.append(
                        _build_anomaly_entry(
                            occ,
                            text,
                            route_reason=route_reason,
                            document_id=document_id,
                            document_path=document_path,
                            document_source=document_source,
                        )
                    )
        else:
            should_queue, route_reason = _is_llm_ambiguity_candidate(occ)
            anomalies.append(
                _build_anomaly_entry(
                    occ,
                    text,
                    route_reason=route_reason,
                    document_id=document_id,
                    document_path=document_path,
                    document_source=document_source,
                )
            )

    return resolved_refs, anomalies


def resolve_references(
    text: str,
    local_aliases: dict[str, str],
    global_aliases: dict[str, str],
    output_anomaly_json: str = "data/processed/to_be_checked_by_llm.json",
    persist_anomalies: bool = True,
    include_low_confidence_anomalies: bool = False,
    document_id: str | None = None,
    document_path: str | None = None,
    document_source: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Backward-compatible wrapper over citation-first extraction.

    Returns:
      (resolved_references, anomalies)
    where anomalies are derived from occurrence-level outputs.
    """
    occurrences = extract_citation_occurrences(text, local_aliases, global_aliases)
    resolved_refs, anomalies = occurrences_to_resolved_and_anomalies(
        occurrences,
        text,
        include_low_confidence_anomalies=include_low_confidence_anomalies,
        document_id=document_id,
        document_path=document_path,
        document_source=document_source,
    )

    if persist_anomalies and anomalies:
        append_anomalies_to_file(anomalies, output_anomaly_json)

    return resolved_refs, anomalies
