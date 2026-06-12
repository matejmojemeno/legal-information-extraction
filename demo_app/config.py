"""Configuration for the local demo web application."""

from __future__ import annotations

import os
from pathlib import Path

from src.production_paths import PRODUCTION_CORPUS_ROOT, PRODUCTION_DATE_METADATA_PATH

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

DEMO_CORPUS_ROOT = Path(os.getenv("DEMO_CORPUS_ROOT", PRODUCTION_CORPUS_ROOT)).resolve()
DEMO_METADATA_PATH = Path(os.getenv("DEMO_METADATA_PATH", PRODUCTION_DATE_METADATA_PATH)).resolve()
DEMO_SELF_ID_SNAPSHOT = Path(
    os.getenv(
        "DEMO_SELF_ID_SNAPSHOT",
        "data/final_runs/thesis_final_v1/document_references/document_self_identifiers.jsonl",
    )
).resolve()
DEMO_MAX_TARGET_PREVIEW_CHARS = int(os.getenv("DEMO_MAX_TARGET_PREVIEW_CHARS", "16000"))
