# Czech Legal Reference Extraction Thesis Package

This package contains the submitted implementation and compact data artifacts for
the thesis project on extracting and linking Czech legal references.

The package is intended for code inspection, small-scale execution, evaluation
review, and local demonstration. It does not include the full normalized text
corpus or raw source exports, because those artifacts are large generated or
source-derived files.

## Contents

- `src/`: core extraction, normalization, law-resolution, document-linking,
  metadata, and prompt-support modules.
- `scripts/`: production, evaluation, baseline, ablation, BRL, audit, and graph
  analysis scripts used in the thesis.
- `demo_app/`: local FastAPI interface for uploading a PDF and inspecting
  extracted references in context.
- `tests/`: lightweight unit and regression checks.
- `docs/`: annotation and demo documentation.
- `data/dicts/`: runtime law dictionaries and alias resources.
- `data/metadata/`: compressed corpus metadata and text-quality audit outputs.
- `data/annotations/`: compact benchmark, review, baseline, ablation, and BRL
  evaluation artifacts used in the thesis.
- `data/final_runs/thesis_final_v1/`: compact final-run summaries, tables,
  plots, manifests, evaluation reports, and a small self-identifier snapshot for
  the bundled demo PDFs.
- `data/sample_corpus/`: normalized sample input texts and target texts used by
  the local demo app.
- `data/sample_pdfs/`: two real public decision PDFs that can be uploaded into
  the local demo app.

## Install

Use Python 3.11 or newer. From the package root:

```bash
pip install -r requirements.txt
```

Run the lightweight tests with:

```bash
PYTHONPATH=. pytest tests
```

## Local Demo App

The demo app provides a local inspection interface for one uploaded PDF. It
extracts text from the PDF, runs the thesis extraction pipeline, highlights law
references and document references, and shows the structured output behind each
detected occurrence.

Start the deterministic version from the package root:

```bash
DEMO_CORPUS_ROOT=data/sample_corpus/parent_bucket_texts \
PYTHONPATH=. uvicorn demo_app.app:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

The deterministic demo does not require an API key. It can be tested with any
text-based legal PDF. For convenience, this package includes two real public
decision PDFs in `data/sample_pdfs/`.

Suggested demo inputs:

- `31 nd 200_2012.pdf` shows ordinary deterministic law-reference extraction
  and document-reference linking.
- `2021_S0666.pdf` shows the AI-assisted BRL document-linking path. The document
  cites `sp. zn. 1 Afs 106/2012`; the compact candidate snapshot contains two
  possible NSS targets, and BRL review can use the cited date in the
  surrounding text to select the matching target.

To enable AI-assisted BRL review, provide a Gemini API key before starting the
app:

```bash
GEMINI_API_KEY=<your-gemini-api-key> \
DEMO_CORPUS_ROOT=data/sample_corpus/parent_bucket_texts \
PYTHONPATH=. uvicorn demo_app.app:app --reload
```

The model can be changed with `GEMINI_MODEL` if needed. When BRL review is
enabled in the web interface, difficult law-reference and document-reference
cases are sent to Gemini for bounded review. Use this mode only for documents
whose text and candidate metadata may be sent to the configured Gemini service.

Known demo limits:

- The app expects text-based PDFs. It does not perform OCR on scanned image-only
  PDFs.
- The compact submission package includes only a small document
  self-identifier snapshot for the bundled demo PDFs. To run the demo against the
  full corpus, supply the full snapshot through `DEMO_SELF_ID_SNAPSHOT`.
- The demo is a local inspection tool, not a production legal information
  system.

## Main Execution Entry Points

The main thesis pipeline scripts are:

- `scripts/40_run_final_pipeline.py`: SPP production run.
- `scripts/18_link_document_references.py`: document-reference linking.
- `scripts/41_export_analysis_ready_graph.py`: graph export.
- `scripts/42_build_analysis_tables.py`: analysis table generation.
- `scripts/43_analyze_law_references.py`: law-reference analysis.
- `scripts/44_analyze_citation_graph.py`: citation-graph analysis.

The full production run expects the full normalized text corpus under the corpus
root. The compact package includes `data/sample_corpus/` for small-scale
inspection and code-level checks.

## Data Boundary

The compressed metadata file in `data/metadata/document_metadata.jsonl.gz`
contains one record for each document in the corpus. It keeps the fields used by
the submitted code and thesis analyses: source, stable document identifier,
source artifact name, normalized-text path, public source identifier, public
source URL when available, and normalized date fields.

The full normalized text corpus, raw source exports, earlier experimental text
corpus, and full corpus-scale JSONL outputs are omitted from the compact submission
package. The final-run summaries, benchmark artifacts, plots, manifests, and
analysis tables included here document the reported aggregate results.
