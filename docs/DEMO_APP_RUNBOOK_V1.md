# Demo App Runbook v1

This file captures the current local demo workflow for the thesis presentation app.

## Purpose

The demo app provides a lightweight interface for:

- uploading one legal PDF
- extracting its text
- running the thesis pipeline on that text
- optionally sending difficult law-reference and document-reference cases through
  AI-assisted BRL review
- highlighting law references and decision references inline
- opening official court/UOHS sources for linked decisions when available
- opening linked in-corpus target decisions as a local preview fallback

The app is intentionally local and minimal. It is a demonstration layer, not a production deployment.

## Stack

- FastAPI
- Jinja2 templates
- vanilla JavaScript
- custom CSS
- PyMuPDF for PDF text extraction
- Gemini via `google-genai` for optional BRL review

## Main Files

- `demo_app/app.py`
- `demo_app/config.py`
- `demo_app/services/pipeline_runner.py`
- `demo_app/services/linker_cache.py`
- `demo_app/templates/upload.html`
- `demo_app/templates/result.html`
- `demo_app/static/styles.css`

## Required Inputs

The app expects the current canonical project resources. In the compact
submission package, the included sample corpus can be used for local inspection:

- corpus root:
  - `data/sample_corpus/parent_bucket_texts`
- canonical metadata:
  - `data/metadata/document_metadata.jsonl.gz`
- document self-identifier snapshot:
  - the compact package includes a small snapshot for the bundled demo PDFs
  - configure `DEMO_SELF_ID_SNAPSHOT` to use a full snapshot instead

The defaults come from:

- `demo_app/config.py`

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
DEMO_CORPUS_ROOT=data/sample_corpus/parent_bucket_texts \
PYTHONPATH=. uvicorn demo_app.app:app --reload
```

Then open:

- `http://127.0.0.1:8000`

## Environment Overrides

Optional overrides:

- `DEMO_CORPUS_ROOT`
- `DEMO_METADATA_PATH`
- `DEMO_SELF_ID_SNAPSHOT`
- `DEMO_MAX_TARGET_PREVIEW_CHARS`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`

Example:

```bash
DEMO_MAX_TARGET_PREVIEW_CHARS=24000 PYTHONPATH=. uvicorn demo_app.app:app --reload
```

Example with AI-assisted BRL review available:

```bash
GEMINI_API_KEY=... \
DEMO_CORPUS_ROOT=data/sample_corpus/parent_bucket_texts \
PYTHONPATH=. uvicorn demo_app.app:app --reload
```

The compact package includes two real public upload PDFs in `data/sample_pdfs/`.
The `31 nd 200_2012.pdf` file is useful for deterministic extraction and
linking. The `2021_S0666.pdf` file is useful for showing AI-assisted BRL
document-link disambiguation.

## Current Behavior

- law references are highlighted inline
- decision references are highlighted inline
- law references can open the resolved law text on `zakonyprolidi.cz`
- linked decision references prefer the official court/UOHS source page
- linked decision references still expose a local target preview fallback
- the side panel lists detected references and scrolls to each occurrence
- filters allow hiding either law or decision references
- optional BRL mode shows the SPP result immediately, then reviews routed hard
  cases in the background
- BRL-reviewed findings are marked in both the text and the side panel

## Known Limits

- no OCR is performed for image-only PDFs
- BRL mode sends selected text and candidate information to the configured Gemini
  service
- linking for uploaded files is optimized for exact in-corpus matching and does not try to infer uploaded-source-specific weak link scopes
- the first request may be noticeably slower because the linker scans the self-identifier snapshot for matching keys
- BRL mode can review both law-reference hard cases and routed
  document-reference hard cases when candidate targets are available
- if `GEMINI_API_KEY` is not set, BRL mode will fail gracefully and the SPP
  result remains usable

## Suggested Thesis Framing

Describe the app as:

- a local demonstration interface
- built on top of the final extraction and linking pipeline
- intended to visualize extracted references in context
- not intended as a production legal information system
