"""
Helpers for loading runtime alias data with optional ambiguity and match overrides.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


DEFAULT_GLOBAL_PATH = "data/dicts/global_aliases.json"
DEFAULT_AUDITED_PATH = "data/dicts/audited_aliases.json"
DEFAULT_SEEDED_PATH = "data/dicts/seed_aliases.json"
DEFAULT_AMBIGUOUS_PATH = "data/dicts/ambiguous_aliases.json"
DEFAULT_OVERRIDES_PATH = "data/dicts/alias_match_overrides.json"
DEFAULT_CANONICAL_LAWS_PATH = "data/dicts/canonical_laws.json"
_ABBREVIATED_HEADS = {
    "občanský": "obč.",
    "obchodní": "obch.",
    "trestní": "tr.",
    "správní": "spr.",
}
_HIGH_VALUE_NOISY_TITLE_VARIANTS = {
    "občanský soudní řád": ["občanského soudu řádu"],
}
_CANONICAL_TITLE_SHORTCUTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"^(?:zákoník práce|zákon zákoník práce)$", re.IGNORECASE),
        ("zák. práce",),
    ),
    (
        re.compile(r"^zákon o pobytu cizinců na území", re.IGNORECASE),
        ("cizinecký zákon", "cizineckého zákona"),
    ),
    (
        re.compile(
            r"^(?:zákon o|zákon české národní rady o) ochraně zemědělského půdního fondu$",
            re.IGNORECASE,
        ),
        ("zákon o ochraně ZPF",),
    ),
    (
        re.compile(r"posuzování pracovní schopnosti pro účely invalidity", re.IGNORECASE),
        ("vyhláška o posuzování invalidity",),
    ),
)
_SHORTCUT_ALIAS_NAMES = {alias for _, aliases in _CANONICAL_TITLE_SHORTCUTS for alias in aliases}


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_alias_entry(
    alias_map: dict[str, Any],
    alias: str,
    payload: Any,
) -> None:
    if isinstance(payload, str):
        alias_map[alias] = payload
        return
    if isinstance(payload, list):
        alias_map[alias] = {"law_ids": payload}
        return
    if isinstance(payload, dict):
        existing = alias_map.get(alias)
        if isinstance(existing, dict):
            merged = dict(existing)
            merged.update(payload)
            alias_map[alias] = merged
        elif existing is not None:
            merged = dict(payload)
            if "law_id" not in merged and "law_ids" not in merged:
                if isinstance(existing, str):
                    merged["law_id"] = existing
            alias_map[alias] = merged
        else:
            alias_map[alias] = dict(payload)


_PAREN_ALIAS_PATTERN = re.compile(r"\(([^()]{2,120})\)")
_LAW_ID_PATTERN = re.compile(r"\d{1,4}/(?:\d{2}|\d{4})\s*Sb\.", re.IGNORECASE)
_BAD_TITLE_PREFIXES = (
    "zákon, kterým se",
    "vyhláška, kterou se",
    "nařízení, kterým se",
    "sdělení ",
    "oznámení ",
    "redakční sdělení",
    "nález ",
    "usnesení ",
    "úplné znění",
    "vyhláška o počátku platnosti",
    "vyhláška o publikaci",
    "oprava ",
)
_BAD_ALIAS_FRAGMENTS = (
    "ve znění",
    "pozdějších předpisů",
    "úplné znění",
    "jak vyplývá",
    "jak vyplývá z",
    "přijatému parlamentem",
    "vrácenému prezidentem",
    "nález",
    "usnesení",
)
_LEGAL_ALIAS_KEYWORDS = (
    "zákon",
    "řád",
    "zákoník",
    "pravidla",
    "tarif",
    "listina",
    "ústava",
    "rozhodč",
)


def _contains_bad_alias_fragment(lower: str) -> bool:
    for fragment in _BAD_ALIAS_FRAGMENTS:
        if " " in fragment:
            if fragment in lower:
                return True
        else:
            boundary_pattern = rf"(?<![A-Za-zÁ-ž]){re.escape(fragment)}(?![A-Za-zÁ-ž])"
            if re.search(boundary_pattern, lower):
                return True
    return False


def _normalize_harvested_alias(alias: str) -> str:
    normalized = alias.strip().strip(".,;: ").strip(' "„“')
    for prefix in ("Zákon o ", "Vyhláška o ", "Nařízení o ", "Úmluva o "):
        if normalized.startswith(prefix):
            return prefix[:1].lower() + normalized[1:]
    return normalized


def _looks_like_base_law_title(title: str) -> bool:
    lower = title.lower().strip()
    if lower.startswith(_BAD_TITLE_PREFIXES):
        return False
    return lower.startswith(
        (
            "zákon o ",
            "vyhláška o ",
            "nařízení o ",
            "úmluva o ",
            "občanský soudní řád",
            "trestní zákon",
            "trestní řád",
            "občanský zákoník",
            "obchodní zákoník",
        )
    )


def _is_viable_harvested_alias(alias: str) -> bool:
    normalized = _normalize_harvested_alias(alias)
    lower = normalized.lower()
    if len(normalized) < 4 or len(normalized) > 120:
        return False
    if _contains_bad_alias_fragment(lower):
        return False
    if re.search(r"\bč\.\b|\bč\.\s*\d|sb\.", lower):
        return False
    if normalized.count(",") > 1:
        return False
    if any(keyword in lower for keyword in _LEGAL_ALIAS_KEYWORDS):
        return True
    if normalized.isupper() and 2 <= len(normalized) <= 8:
        return True
    return False


def _iter_canonical_names(canonical_laws: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if not isinstance(canonical_laws, dict):
        return items
    for law_id, names in canonical_laws.items():
        if not isinstance(law_id, str):
            continue
        if isinstance(names, list):
            for name in names:
                if isinstance(name, str):
                    items.append((law_id, name.strip()))
        elif isinstance(names, str):
            items.append((law_id, names.strip()))
    return items


def _harvest_canonical_aliases(canonical_laws: Any) -> dict[str, Any]:
    alias_candidates: dict[str, set[str]] = {}

    for current_law_id, title in _iter_canonical_names(canonical_laws):
        normalized_title = title.strip().strip(". ")

        if _looks_like_base_law_title(normalized_title):
            base_alias = _normalize_harvested_alias(normalized_title)
            if _is_viable_harvested_alias(base_alias):
                alias_candidates.setdefault(base_alias, set()).add(current_law_id)

        lower_title = normalized_title.lower()
        for pattern, shortcuts in _CANONICAL_TITLE_SHORTCUTS:
            if pattern.search(lower_title):
                for shortcut in shortcuts:
                    alias_candidates.setdefault(shortcut, set()).add(current_law_id)

        parenthetical_matches = list(_PAREN_ALIAS_PATTERN.finditer(normalized_title))
        law_id_matches = list(_LAW_ID_PATTERN.finditer(normalized_title))

        for match in parenthetical_matches:
            alias = _normalize_harvested_alias(match.group(1))
            if not _is_viable_harvested_alias(alias):
                continue
            preceding_law_ids = [m.group(0).strip() for m in law_id_matches if m.start() < match.start()]
            if preceding_law_ids:
                alias_candidates.setdefault(alias, set()).add(preceding_law_ids[-1])

        if _looks_like_base_law_title(normalized_title) and parenthetical_matches:
            alias = _normalize_harvested_alias(parenthetical_matches[-1].group(1))
            if _is_viable_harvested_alias(alias):
                alias_candidates.setdefault(alias, set()).add(current_law_id)

    harvested: dict[str, Any] = {}
    for alias, law_ids in alias_candidates.items():
        ordered = sorted(law_ids)
        shortcut_like = alias in _SHORTCUT_ALIAS_NAMES
        if len(ordered) == 1:
            harvested[alias] = {"law_id": ordered[0], "match_mode": "inflect"} if shortcut_like else ordered[0]
        elif len(ordered) <= 4:
            payload: dict[str, Any] = {"law_ids": ordered}
            if shortcut_like:
                payload["match_mode"] = "inflect"
            harvested[alias] = payload
    return harvested


def _payload_law_ids(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        if isinstance(payload.get("law_id"), str):
            return [payload["law_id"]]
        if isinstance(payload.get("law_ids"), list):
            return [str(item) for item in payload["law_ids"] if str(item).strip()]
    return []


def _make_payload_like(payload: Any, law_ids: list[str]) -> Any:
    if len(law_ids) == 1:
        if isinstance(payload, dict):
            clone = dict(payload)
            clone.pop("law_ids", None)
            clone["law_id"] = law_ids[0]
            clone["match_mode"] = "inflect"
            return clone
        return {"law_id": law_ids[0], "match_mode": "inflect"}
    return {"law_ids": law_ids, "match_mode": "inflect"}


def _derive_additional_aliases(alias_map: dict[str, Any]) -> dict[str, Any]:
    derived: dict[str, Any] = {}

    def add(alias: str, payload: Any) -> None:
        if alias in derived:
            _merge_alias_entry(derived, alias, payload)
        else:
            derived[alias] = payload

    for alias, payload in alias_map.items():
        law_ids = _payload_law_ids(payload)
        if not law_ids:
            continue
        lower = alias.lower().strip()
        tokens = lower.split()

        if len(tokens) >= 2 and tokens[0] in _ABBREVIATED_HEADS:
            abbreviated = " ".join([_ABBREVIATED_HEADS[tokens[0]], *tokens[1:]])
            add(abbreviated, _make_payload_like(payload, law_ids))

        if lower.startswith("zákon o ") and " a o " in lower:
            prefix = lower.split(" a o ", 1)[0].strip()
            if len(prefix) >= len("zákon o rozhodčím řízení"):
                add(prefix, _make_payload_like(payload, law_ids))

        if lower.startswith("zákon o rozhodčím řízení a o výkonu rozhodčích nálezů"):
            add("RozŘ", _make_payload_like(payload, law_ids))
            add("zákon o rozhodčím řízení", _make_payload_like(payload, law_ids))

        for noisy_variant in _HIGH_VALUE_NOISY_TITLE_VARIANTS.get(lower, []):
            add(noisy_variant, _make_payload_like(payload, law_ids))

    return derived


def load_runtime_aliases(
    audited_path: str = DEFAULT_AUDITED_PATH,
    global_path: str = DEFAULT_GLOBAL_PATH,
    seeded_path: str = DEFAULT_SEEDED_PATH,
    ambiguous_path: str = DEFAULT_AMBIGUOUS_PATH,
    overrides_path: str = DEFAULT_OVERRIDES_PATH,
    canonical_laws_path: str = DEFAULT_CANONICAL_LAWS_PATH,
) -> tuple[dict[str, Any], str]:
    """
    Load aliases used by the runtime extractor.

    Result values may be:
    - "99/1963 Sb."
    - {"law_ids": ["40/1964 Sb.", "89/2012 Sb."]}
    - {"law_id": "182/1993 Sb.", "match_mode": "inflect"}
    """
    alias_map: dict[str, Any] = {}
    source = "empty"

    if os.path.exists(audited_path):
        audited = _load_json(audited_path)
        if isinstance(audited, dict):
            for alias, details in audited.items():
                if isinstance(details, dict) and details.get("is_valid") is True:
                    law_id = details.get("law_id")
                    if isinstance(alias, str) and isinstance(law_id, str):
                        alias_map[alias] = law_id
        source = "audited"
    elif os.path.exists(global_path):
        raw = _load_json(global_path)
        if isinstance(raw, dict):
            for alias, law_id in raw.items():
                if isinstance(alias, str):
                    _merge_alias_entry(alias_map, alias, law_id)
        source = "global"

    if os.path.exists(seeded_path):
        seeded = _load_json(seeded_path)
        if isinstance(seeded, dict):
            for alias, payload in seeded.items():
                if isinstance(alias, str):
                    _merge_alias_entry(alias_map, alias, payload)
        source = f"{source}+seeded" if source != "empty" else "seeded"

    if os.path.exists(canonical_laws_path):
        canonical_laws = _load_json(canonical_laws_path)
        harvested = _harvest_canonical_aliases(canonical_laws)
        for alias, law_id in harvested.items():
            if alias not in alias_map:
                alias_map[alias] = law_id
        source = f"{source}+canonical" if source != "empty" else "canonical"

    derived_aliases = _derive_additional_aliases(alias_map)
    for alias, payload in derived_aliases.items():
        if alias not in alias_map:
            alias_map[alias] = payload
    if derived_aliases:
        source = f"{source}+derived" if source != "empty" else "derived"

    if os.path.exists(ambiguous_path):
        ambiguous = _load_json(ambiguous_path)
        if isinstance(ambiguous, dict):
            for alias, payload in ambiguous.items():
                if isinstance(alias, str):
                    _merge_alias_entry(alias_map, alias, payload)
        source = f"{source}+ambiguous" if source != "empty" else "ambiguous"

    if os.path.exists(overrides_path):
        overrides = _load_json(overrides_path)
        if isinstance(overrides, dict):
            for alias, payload in overrides.items():
                if isinstance(alias, str) and isinstance(payload, dict):
                    _merge_alias_entry(alias_map, alias, payload)
        source = f"{source}+overrides" if source != "empty" else "overrides"

    return alias_map, source
