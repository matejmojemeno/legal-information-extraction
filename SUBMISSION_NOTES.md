# Thesis Submission Package Notes

This package contains the code and compact data artifacts needed to inspect the
implementation and reproduce the reported thesis-level evaluations and summary
outputs.

## Included

- `src/`: core extraction, normalization, resolution, linking, metadata, and
  prompt-support modules.
- `scripts/`: production, evaluation, baseline, ablation, BRL, audit, and
  analysis scripts used in the thesis. Source-acquisition scripts are excluded.
- `demo_app/`: local inspection interface.
- `tests/`: lightweight regression and unit checks.
- `docs/`: annotation and demo documentation.
- `data/dicts/`: runtime law dictionaries and alias resources.
- `data/metadata/document_metadata.jsonl.gz`: compressed metadata for all
  444,361 corpus records. Each row contains the source, stable document
  identifier, source artifact name, date fields, normalized-text path, public
  source identifier, and public source URL where available. Duplicate raw-date
  fields and unused date-bound fields are omitted from the compact submission
  metadata.
- `data/metadata/corpus_text_quality_audit.*`: text-quality audit outputs.
- `data/annotations/`: compact benchmark, review, baseline, ablation, and BRL
  evaluation artifacts used in the thesis.
- `data/final_runs/thesis_final_v1/`: compact final-run summaries, analysis
  tables, plots, manifests, evaluation summaries, and a small self-identifier
  snapshot for the bundled demo PDFs.
- `data/sample_corpus/`: normalized sample input texts and target texts for the
  local demo app, while preserving the expected source directory layout.
- `data/sample_pdfs/`: two real public decision PDFs for trying the local demo
  app without supplying an external PDF.

## Excluded

The full imported text corpus, raw source exports, earlier experimental text corpus, and
full corpus-scale JSONL outputs are not included. The normalized text corpus is
represented by the compact sample text set, while corpus coverage is documented by
the complete metadata table, compact final-run summaries, analysis tables,
plots, manifests, benchmark artifacts, and evaluation reports included in this
package. The omitted files are large generated or source-derived artifacts and
are not necessary for inspecting the implementation or checking the reported
benchmark and aggregate results.

The included self-identifier snapshot is intentionally small. It supports the
bundled demo PDFs and is not a replacement for the full corpus-scale
self-identifier snapshot used in the thesis pipeline.

The source-acquisition scraper layer is also excluded from this submission
package. The submitted implementation starts from normalized source exports and
contains the corpus normalization interface, extraction, resolution, linking,
evaluation, BRL experiments, and graph-analysis pipeline used in the thesis.

## Important Paths

- Canonical production entry point: `scripts/40_run_final_pipeline.py`
- Document linking: `scripts/18_link_document_references.py`
- Analysis table generation: `scripts/42_build_analysis_tables.py`
- Law-reference analysis: `scripts/43_analyze_law_references.py`
- Citation-graph analysis: `scripts/44_analyze_citation_graph.py`
- Final compact analysis snapshot:
  `data/final_runs/thesis_final_v1/analysis_law_refresh_apr1/`

## Reproducibility Boundary

The package is intended to support code inspection, small-scale execution, and
verification of thesis-reported benchmark and aggregate outputs. Re-running the
full corpus pipeline requires the full normalized input corpus, which is omitted
from this compact submission package.
