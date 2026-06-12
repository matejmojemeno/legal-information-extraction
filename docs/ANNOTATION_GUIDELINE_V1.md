# Annotation Guideline V1 (Solo-Friendly)

## Scope
Annotate legal citation occurrences in Czech legal texts.

Primary unit: one citation occurrence.

## Goal
- maximize precision of what is a citation
- keep law resolution conservative (`law_id = null` if uncertain)
- capture structured detail for deterministic parser evaluation

## What Counts As Citation
Annotate:
- `§ ...`
- `čl.` / `článek ...`
- direct law-id mentions (`NN/NNNN Sb.`) when they are legal references

Do not annotate:
- case identifiers (`sp. zn.`)
- dates
- docket/file numbers
- non-normative numbering

## Task File Fields You Fill (`scripts/09`)
Per `annotations[]` item:
- `finding_text`: exact substring from `snippet_text`
- `occurrence_index_in_snippet`: 1-based index if repeated
- `citation_type`: `section` / `article` / `other_normative`
- `classification`: `czech_resolved` / `czech_unresolved` / `foreign_law` / `non_citation`
- `law_id`: canonical ID like `99/1963 Sb.` or `null`
- `law_name_text`: surface law mention tied to this finding (e.g., `citovaného zákona`)
- `declared_alias_text`: alias declared in doc (for example from `dále jen "..."`), optional
- `detail_number`: section/article number (`237`, `238a`, ...)
- `detail_odst`: list (`["1", "2"]`)
- `detail_pism`: list (`["a", "c"]`)
- `confidence`: optional float `0.0-1.0`
- `note`: optional short note

## Span Boundary Policy
- `finding_text` must be exact text from snippet.
- Preferred boundary is the citation core + attached structural detail.
- Including local law phrase is allowed when it is directly attached and helps disambiguation.
- Keep boundary decisions consistent within a batch.

## Resolution Policy
- Resolve only with concrete nearby evidence.
- If multiple Czech laws are plausible, use `czech_unresolved`.
- For foreign references, use `foreign_law` and `law_id = null`.

## Finalization Output (`scripts/10`)
Finalizer computes `start_char`/`end_char` from `finding_text` and keeps structured fields (`law_id`, `law_name_text`, `declared_alias_text`, details, classification, confidence).

## Quality Loop
- Review sampled errors by category:
  - missed citation
  - wrong span boundary
  - wrong law resolution
  - non-citation mislabeled as citation
  - foreign/czech misclassification
  - detail parse mismatch
- Use these categories to prioritize deterministic rule updates before adding more LLM routing.
