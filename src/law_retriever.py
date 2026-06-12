"""
Local candidate retrieval for Czech law resolution.

This avoids sending the full canonical laws database to the LLM by producing
small candidate shortlists from local data.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict

_TOKEN_PATTERN = re.compile(r"[a-z0-9á-ž]{2,}", re.IGNORECASE)

_STOPWORDS = {
    "a",
    "ale",
    "anebo",
    "ani",
    "bez",
    "by",
    "byl",
    "byla",
    "byly",
    "co",
    "do",
    "i",
    "jako",
    "je",
    "jej",
    "jejich",
    "jen",
    "jsou",
    "k",
    "která",
    "které",
    "který",
    "mezi",
    "na",
    "nad",
    "nebo",
    "o",
    "od",
    "po",
    "pod",
    "podle",
    "pro",
    "při",
    "s",
    "se",
    "si",
    "tak",
    "také",
    "to",
    "u",
    "v",
    "ve",
    "z",
    "za",
    "ze",
    "zákon",
    "zákona",
    "zákonem",
    "zákoník",
    "zákoníku",
    "vyhláška",
    "vyhlášky",
    "nařízení",
    "předpis",
    "předpisu",
    "část",
    "hlava",
    "oddíl",
    "odst",
    "odstavec",
    "písm",
    "pism",
    "čl",
    "cl",
    "paragraf",
    "sb",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    text = _strip_accents(text.lower())
    text = text.replace("čl.", "cl ")
    text = text.replace("článek", "clanek")
    text = text.replace("§", " paragraf ")
    return re.sub(r"\s+", " ", text)


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    out = []
    for tok in _TOKEN_PATTERN.findall(normalized):
        if tok.isdigit():
            continue
        if len(tok) < 3:
            continue
        if tok in _STOPWORDS:
            continue
        out.append(tok)
    return out


def normalize_law_id(law_id: str) -> str:
    return re.sub(r"\s+", " ", law_id).strip()


class CanonicalLawRetriever:
    def __init__(self, canonical_laws: dict[str, list[str] | str]):
        self.primary_name_by_id: dict[str, str] = {}
        self._token_postings: dict[str, set[str]] = defaultdict(set)
        self._tokens_by_id: dict[str, set[str]] = {}

        for raw_id, names in canonical_laws.items():
            law_id = normalize_law_id(raw_id)
            name_list = names if isinstance(names, list) else [names]
            clean_names = [n for n in name_list if isinstance(n, str) and n.strip()]
            if not clean_names:
                continue

            self.primary_name_by_id[law_id] = clean_names[0]

            token_set: set[str] = set()
            for name in clean_names:
                for tok in tokenize(name):
                    token_set.add(tok)
            if not token_set:
                continue

            self._tokens_by_id[law_id] = token_set
            for tok in token_set:
                self._token_postings[tok].add(law_id)

    def get_name(self, law_id: str) -> str:
        return self.primary_name_by_id.get(law_id, "Unknown law")

    def shortlist(self, context: str, top_k: int = 12) -> list[tuple[str, float]]:
        ctx_tokens = tokenize(context)
        if not ctx_tokens:
            return []

        overlap_counts: dict[str, int] = defaultdict(int)
        for tok in set(ctx_tokens):
            for law_id in self._token_postings.get(tok, ()):
                overlap_counts[law_id] += 1

        if not overlap_counts:
            return []

        scored: list[tuple[str, float]] = []
        for law_id, overlap in overlap_counts.items():
            denom = math.sqrt(max(len(self._tokens_by_id.get(law_id, ())), 1))
            score = overlap / denom
            scored.append((law_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def shortlist_by_law_name(
    context: str,
    canonical_laws: dict[str, list[str] | str],
    top_k: int = 12,
) -> list[tuple[str, float]]:
    retriever = CanonicalLawRetriever(canonical_laws)
    return retriever.shortlist(context, top_k=top_k)
