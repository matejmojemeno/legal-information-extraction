"""
Local Alias Extraction from Czech legal documents.

Implements Rules 1–5 from the NLP Extraction Rulebook:
  - Rule 1: Anchor phrases (dále jen, dále také jen, dále též, etc.)
  - Rule 2: Enclosures & formatting constraints
  - Rule 3: Self-contained ID (alias contains "Sb.")
  - Rule 4: Lookback window with barrier
  - Rule 5: Distance threshold (120 chars max)

Post-processing rules (Rules 10–13):
  - Rule 10: Strip stray quotes/punctuation from alias names
  - Rule 11: Reject generic, too-short, or meaningless aliases
  - Rule 12: Normalize whitespace, split comma-separated aliases
  - Rule 13: Discard aliases that are just law citations
"""

import re


# Rule 1: Anchor phrases with word boundary on "dále"
STANDARD_ANCHOR_PATTERN = re.compile(
    r"""
    \bdále\s+jen\b\s*
    (?:
      \(\s*(?P<paren>[^\)\n]{1,80}?)\s*\) |
      \[\s*(?P<bracket>[^\]\n]{1,80}?)\s*\] |
      [„\"']\s*(?P<quote>[^„“\"'\n]{1,80}?)\s*[“\"']
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Rule 2: For non-standard variants we require parentheses.
VARIANT_ANCHOR_PATTERN = re.compile(
    r"""
    \bdále\s+
    (?:také\s+jen|také\s+jako|také|též\s+jen|též\s+jako|též|jako)
    \b\s*
    \(\s*(?P<alias>[^\)\n]{1,80}?)\s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)

LAW_ID_PATTERN = re.compile(r"(\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.)")
AMENDMENT_TAIL_PATTERN = re.compile(
    r"(?:ve\s+znění|ve\s+znění\s+účinném|viz\s+čl\.|kterým\s+se\s+mění|mění\s+zákon|doplňuje)",
    re.IGNORECASE,
)

# Rule 4 constants
LOOKBACK_CHARS = 150
# Rule 5 constants
MAX_DISTANCE = 120

MIN_ALIAS_LENGTH = 3
GENERIC_ALIASES = {
    "zákon",
    "zákona",
    "zákonu",
    "zákonem",
    "zákoně",
    "zákony",
    "vyhláška",
    "vyhlášky",
    "vyhlášce",
    "vyhláškou",
    "nařízení",
    "směrnice",
    "předpis",
    "předpisu",
    "novela",
    "novely",
    "novelou",
    "s.r.o.",
    "s. r. o.",
}


def _normalize_law_id(law_id: str) -> str:
    """Collapse whitespace/newlines in a law ID ('326/1999 \\nSb.' → '326/1999 Sb.')."""
    return re.sub(r"\s+", " ", law_id).strip()


def _sanitize_alias(alias: str) -> str | None:
    """
    Rules 10–13: Clean and validate an alias name.

    Returns the cleaned alias, or None if it should be rejected.
    """
    # Rule 10 & Boundaries:
    # Strip leading non-letters (removes stray quotes, dashes, numbers)
    alias = re.sub(r"^[\W\d_]+", "", alias)
    # Strip trailing characters that are neither letters nor dots
    alias = re.sub(r"(?!\.)[\W\d_]+$", "", alias)

    # Rule 12a: Normalize acronym dot spacing (e.g. 's.r.o.' -> 's. r. o.')
    # Add a space after a dot if it's followed by a non-whitespace character
    alias = re.sub(r"\.(?=[^\s])", ". ", alias)

    # Remove trailing standalone dots (e.g. "s. ř. s. .")
    alias = re.sub(r"(?:\s+\.)+$", "", alias)

    # Rule 12b: Normalize whitespace (collapse double spaces, etc.)
    alias = re.sub(r"\s+", " ", alias).strip()

    # Reject empty after cleanup
    if not alias:
        return None

    # Allow very short all-uppercase abbreviations such as "AT".
    compact = re.sub(r"[^\w]+", "", alias)
    is_short_upper_abbrev = len(compact) == 2 and compact.isalpha() and compact.isupper()

    # Rule 11: Reject too-short aliases
    if len(alias) < MIN_ALIAS_LENGTH and not is_short_upper_abbrev:
        return None

    # Rule 11: Reject generic legal terms.
    if alias.lower() in GENERIC_ALIASES:
        return None

    # Rule 13: Discard aliases that are just a law citation (e.g. "zákon č. 40/1993 Sb.")
    if LAW_ID_PATTERN.search(alias):
        return None

    return alias


def _select_preceding_law_id(preceding_text: str) -> str | None:
    """
    Choose the law ID that a trailing alias definition most likely names.

    The nearest ID is usually correct, but phrases like:
      "zákona č. 99/1963 Sb. ... ve znění ... zákona č. 286/2021 Sb., dále jen 'o. s. ř.'"
    should bind the alias to the base act (`99/1963 Sb.`), not the latest amendment.
    """
    matches = list(LAW_ID_PATTERN.finditer(preceding_text))
    if not matches:
        return None
    if len(matches) == 1:
        return _normalize_law_id(matches[0].group(1))

    first_match = matches[0]
    tail_after_first = preceding_text[first_match.end() :]
    if AMENDMENT_TAIL_PATTERN.search(tail_after_first):
        return _normalize_law_id(first_match.group(1))

    return _normalize_law_id(matches[-1].group(1))


def _split_comma_aliases(alias: str) -> list[str]:
    """
    Rule 12: If an alias contains a comma, split into separate aliases.
    E.g. 'daňový řád, d.ř.' → ['daňový řád', 'd.ř.']
    """
    if "," in alias:
        return [part.strip() for part in alias.split(",") if part.strip()]
    return [alias]


def extract_local_aliases(text: str) -> dict[str, str]:
    """
    Extract local aliases from a single document's text.

    Uses a sliding-window approach with a barrier to prevent overlapping
    context windows from stealing IDs.

    Returns:
        dict mapping alias name → law ID (e.g. "o.z." → "89/2012 Sb.")
    """
    local_aliases = {}

    # Rule 4: Barrier — end of previous match prevents lookback overlap
    last_match_end = 0

    all_matches = []
    for match in STANDARD_ANCHOR_PATTERN.finditer(text):
        raw_alias = (
            match.group("paren") or match.group("bracket") or match.group("quote") or ""
        ).strip()
        all_matches.append((match.start(), match.end(), raw_alias))

    for match in VARIANT_ANCHOR_PATTERN.finditer(text):
        raw_alias = (match.group("alias") or "").strip()
        all_matches.append((match.start(), match.end(), raw_alias))

    all_matches.sort(key=lambda item: item[0])

    for match_start, match_end, raw_alias in all_matches:
        if not raw_alias:
            last_match_end = match_end
            continue

        # Rule 3 / 13: Self-contained ID — alias itself contains "Sb."
        if "Sb." in raw_alias:
            law_id_match = LAW_ID_PATTERN.search(raw_alias)
            if law_id_match:
                # Don't store the citation as an alias name — just record
                # that this definition exists (for barrier tracking)
                # We do not use this as an alias anymore (merged Rule 3 and 13)
                pass
            last_match_end = match_end
            continue

        # Rule 4: Lookback window stops at previous match end (barrier)
        start_lookback = max(last_match_end, match_start - LOOKBACK_CHARS)
        preceding_text = text[start_lookback:match_start]

        found_id = _select_preceding_law_id(preceding_text)
        if found_id:

            # Rule 5: Distance threshold — reject if ID is too far away
            id_end_pos = preceding_text.rfind(found_id) + len(found_id)
            distance = len(preceding_text) - id_end_pos
            if distance <= MAX_DISTANCE:
                law_id = _normalize_law_id(found_id)

                # Rules 10–13: Sanitize and split alias names
                for sub_alias in _split_comma_aliases(raw_alias):
                    clean_alias = _sanitize_alias(sub_alias)
                    if clean_alias:
                        local_aliases[clean_alias] = law_id

        # Move barrier forward
        last_match_end = match_end

    return local_aliases
