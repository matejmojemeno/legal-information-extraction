# Evaluation Report

- Generated: `2026-03-25T16:18:21.359891+00:00`
- Gold input: `data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl`
- Alias source: `audited+seeded+ambiguous+overrides`
- Gold documents: `25`

## Gold Coverage

- Total findings: `658`
- Supported findings: `658`
- Supported positive findings: `646`
- Supported non-citation findings: `12`
- Unsupported `other_normative` findings: `0`

## Prediction Summary

- Predicted occurrences: `660`
- Duplicate same-anchor occurrences: `0`
- Resolved occurrences: `630`
- Unresolved occurrences: `30`

## Metrics

### Supported Anchor Detection

- Precision: `0.997`
- Recall: `1.0`
- F1: `0.9985`
- TP / FP / FN: `658 / 2 / 0`

### Supported Positive Citation Presence

- Precision: `0.9803`
- Recall: `1.0`
- F1: `0.99`
- TP / FP / FN: `646 / 13 / 0`

### Classification / Resolution / Detail

- Matched supported classification accuracy: `0.9362`
- Czech resolved law accuracy: `0.9322`
- Detail number exact: `0.4706`
- Detail odst. exact: `0.9211`
- Detail písm. exact: `0.9412`
- Detail full exact: `0.4644`

## Error Taxonomy

- `foreign_law_not_typed_explicitly`: `5`
- `gold_non_citation_predicted_as_citation`: `11`
- `spurious_predicted_anchor`: `2`
- `wrong_classification`: `42`
- `wrong_detail_number`: `342`
- `wrong_detail_odst`: `51`
- `wrong_detail_pism`: `38`
- `wrong_law_id`: `43`

## Per-document Summary

| Document | Gold supported | Gold positive | Predicted | Matched | Missing | Spurious | Duplicate same-anchor |
|---|---:|---:|---:|---:|---:|---:|---:|
| `2013_R184_2.txt` | 45 | 45 | 46 | 45 | 0 | 1 | 0 |
| `2016_S0551.txt` | 66 | 63 | 66 | 66 | 0 | 0 | 0 |
| `2021_S0666.txt` | 29 | 29 | 29 | 29 | 0 | 0 | 0 |
| `23 cdo 1135_2022_openElement.txt` | 50 | 50 | 50 | 50 | 0 | 0 | 0 |
| `230526.txt` | 8 | 8 | 8 | 8 | 0 | 0 | 0 |
| `231833.txt` | 15 | 14 | 15 | 15 | 0 | 0 | 0 |
| `237253.txt` | 23 | 22 | 23 | 23 | 0 | 0 | 0 |
| `29 icdo 2_2022_openElement.txt` | 27 | 24 | 27 | 27 | 0 | 0 | 0 |
| `29 odo 1019_2006_openElement.txt` | 50 | 50 | 50 | 50 | 0 | 0 | 0 |
| `30 cdo 1354_2006_openElement.txt` | 52 | 51 | 52 | 52 | 0 | 0 | 0 |
| `30 cdo 1363_2022_openElement.txt` | 9 | 9 | 9 | 9 | 0 | 0 | 0 |
| `609828.txt` | 37 | 35 | 37 | 37 | 0 | 0 | 0 |
| `621783.txt` | 6 | 6 | 6 | 6 | 0 | 0 | 0 |
| `7 tdo 1449_2003_openElement.txt` | 25 | 25 | 25 | 25 | 0 | 0 | 0 |
| `724902.txt` | 20 | 20 | 21 | 20 | 0 | 1 | 0 |
| `GetText.aspx_sz_1-256-2000.txt` | 3 | 3 | 3 | 3 | 0 | 0 | 0 |
| `GetText.aspx_sz_1-293-96.txt` | 55 | 55 | 55 | 55 | 0 | 0 | 0 |
| `GetText.aspx_sz_1-432-97.txt` | 3 | 3 | 3 | 3 | 0 | 0 | 0 |
| `GetText.aspx_sz_2-111-04.txt` | 12 | 12 | 12 | 12 | 0 | 0 | 0 |
| `GetText.aspx_sz_2-4524-12_1.txt` | 4 | 4 | 4 | 4 | 0 | 0 | 0 |
| `GetText.aspx_sz_3-444-97.txt` | 23 | 22 | 23 | 23 | 0 | 0 | 0 |
| `pis33402.txt` | 40 | 40 | 40 | 40 | 0 | 0 | 0 |
| `pis6543.txt` | 9 | 9 | 9 | 9 | 0 | 0 | 0 |
| `pis7143.txt` | 24 | 24 | 24 | 24 | 0 | 0 | 0 |
| `pis8046.txt` | 23 | 23 | 23 | 23 | 0 | 0 | 0 |
