# Manual Annotation Task Format V1

This format is designed to minimize manual tidy work.

## Files
- Input tasks (recommended): per-document JSON files under `data/annotations/tasks/by_document/`
- Task index: `data/annotations/tasks/tasks_index.jsonl`
- Finalized per-doc gold: `data/annotations/gold/by_document/*.json`
- Finalized aggregate index: `data/annotations/gold/gold_annotations_v1.jsonl`
- All produced by:
  - `scripts/09_prepare_manual_annotation_tasks.py`
  - `scripts/10_finalize_manual_annotations.py`

## Task Object
Each task contains:
- `snippet_text`: context block where you annotate findings
- `snippet_doc_start`: document offset of snippet start (auto-generated)
- `annotations[]`: list you fill in

You can split one task into multiple findings by adding entries in `annotations[]`.

## Annotation Fields To Fill
Per `annotations[]` item:
- `finding_text`: exact substring from `snippet_text` for one citation occurrence
- `occurrence_index_in_snippet`: 1-based index if `finding_text` repeats in snippet
- `citation_type`: `section` / `article` / `other_normative`
- `classification`: `czech_resolved` / `czech_unresolved` / `foreign_law` / `non_citation`
- `law_id`: e.g. `99/1963 Sb.` or `null`
- `law_name_text`: local law name/alias if useful
- `declared_alias_text`: alias declared in the document (e.g. `dále jen "..."`), optional
- `detail_number`: e.g. `237`, `238a`
- `detail_odst`: list of odstavce, e.g. `["1", "2"]`
- `detail_pism`: list of letters, e.g. `["c"]`
- `confidence`: optional float `0.0-1.0`
- `note`: optional note

## Important Rules
- Do not edit document text on disk.
- Keep `finding_text` as exact substring from `snippet_text`.
- If a finding appears multiple times in snippet, set `occurrence_index_in_snippet`.

## Hard Example
Snippet contains:
`... podle § 238a odst. 1 písm. c), odst. 2 ve spojení s § 237 odst. 1 písm. c), odst. 3 o.s.ř. ...`

Create two annotation items:
1. `finding_text = "§ 238a odst. 1 písm. c), odst. 2"` with details for `238a`
2. `finding_text = "§ 237 odst. 1 písm. c), odst. 3"` with details for `237`

Both can map to `law_id = "99/1963 Sb."` if context supports it.
