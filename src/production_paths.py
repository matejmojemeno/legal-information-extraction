"""Canonical production paths for the full-corpus thesis pipeline.

These constants define the stable production-facing locations for the canonical
corpus, metadata, dictionaries, and final-run outputs. Centralizing them helps
keep the active pipeline separate from legacy or experiment-era paths.
"""

from __future__ import annotations

from pathlib import Path


def _prefer_existing(primary: str, fallback: str) -> str:
    if Path(primary).exists():
        return primary
    return fallback


PRODUCTION_CORPUS_ROOT = "data/imports/parent_bucket_texts"
PRODUCTION_FINAL_RUNS_ROOT = "data/final_runs"
PRODUCTION_DATE_METADATA_PATH = "data/metadata/document_metadata.jsonl.gz"
PRODUCTION_EXTERNAL_SELF_ID_METADATA_PATH = "data/metadata/document_metadata.jsonl.gz"
PRODUCTION_METADATA_PATH = PRODUCTION_DATE_METADATA_PATH

PRODUCTION_DICT_ROOT = "data/dicts/production"
PRODUCTION_CANONICAL_LAWS_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/canonical_laws.json",
    "data/dicts/canonical_laws.json",
)
PRODUCTION_GLOBAL_ALIASES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/global_aliases.json",
    "data/dicts/global_aliases.json",
)
PRODUCTION_AUDITED_ALIASES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/audited_aliases.json",
    "data/dicts/audited_aliases.json",
)
PRODUCTION_SEED_ALIASES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/seed_aliases.json",
    "data/dicts/seed_aliases.json",
)
PRODUCTION_AMBIGUOUS_ALIASES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/ambiguous_aliases.json",
    "data/dicts/ambiguous_aliases.json",
)
PRODUCTION_ALIAS_MATCH_OVERRIDES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/alias_match_overrides.json",
    "data/dicts/alias_match_overrides.json",
)
PRODUCTION_LAW_TIMELINES_PATH = _prefer_existing(
    f"{PRODUCTION_DICT_ROOT}/law_timelines.json",
    "data/dicts/law_timelines.json",
)
PRODUCTION_RAW_LAWS_PATH = "data/dicts/raw/002PravniAkt.json"
