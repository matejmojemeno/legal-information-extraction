# Evaluation Report

- Generated: `2026-03-31T22:22:47.650148+00:00`
- Gold input: `data/annotations/llm_challenge_gold/llm_challenge_gold_v1.jsonl`
- Alias source: `external_predictions`
- Gold documents: `17`

## Gold Coverage

- Total findings: `18`
- Supported findings: `18`
- Supported positive findings: `18`
- Supported non-citation findings: `0`
- Unsupported `other_normative` findings: `0`

## Prediction Summary

- Predicted occurrences: `1375`
- Duplicate same-anchor occurrences: `0`
- Resolved occurrences: `1370`
- Unresolved occurrences: `5`

## Metrics

### Supported Anchor Detection

- Precision: `0.0131`
- Recall: `1.0`
- F1: `0.0258`
- TP / FP / FN: `18 / 1357 / 0`

### Supported Positive Citation Presence

- Precision: `0.0132`
- Recall: `1.0`
- F1: `0.026`
- TP / FP / FN: `18 / 1350 / 0`

### Classification / Resolution / Detail

- Matched supported classification accuracy: `0.6667`
- Czech resolved law accuracy: `0.4615`
- Detail number exact: `1.0`
- Detail odst. exact: `1.0`
- Detail písm. exact: `1.0`
- Detail full exact: `1.0`

## Error Taxonomy

- `spurious_predicted_anchor`: `1357`
- `wrong_classification`: `6`
- `wrong_law_id`: `7`

## Per-document Summary

| Document | Gold supported | Gold positive | Predicted | Matched | Missing | Spurious | Duplicate same-anchor |
|---|---:|---:|---:|---:|---:|---:|---:|
| `0 Ts 42_2003.txt` | 2 | 2 | 505 | 2 | 0 | 503 | 0 |
| `0 Ts 43_2012.txt` | 1 | 1 | 521 | 1 | 0 | 520 | 0 |
| `1 Co 46_2009.txt` | 1 | 1 | 49 | 1 | 0 | 48 | 0 |
| `1 Ko 125_2001.txt` | 1 | 1 | 7 | 1 | 0 | 6 | 0 |
| `1 Ko 259_2001.txt` | 1 | 1 | 13 | 1 | 0 | 12 | 0 |
| `1 Ko 397_2002.txt` | 1 | 1 | 11 | 1 | 0 | 10 | 0 |
| `1 Ko 433_2001.txt` | 1 | 1 | 11 | 1 | 0 | 10 | 0 |
| `1 Ko 556_2001.txt` | 1 | 1 | 16 | 1 | 0 | 15 | 0 |
| `1 Ko 559_2001.txt` | 1 | 1 | 14 | 1 | 0 | 13 | 0 |
| `1 Ko 68_2001.txt` | 1 | 1 | 11 | 1 | 0 | 10 | 0 |
| `1 Ko 77_2003.txt` | 1 | 1 | 9 | 1 | 0 | 8 | 0 |
| `1 Nt 206_2007.txt` | 1 | 1 | 47 | 1 | 0 | 46 | 0 |
| `1 Ntd 1_2024.txt` | 1 | 1 | 33 | 1 | 0 | 32 | 0 |
| `1 To 39_2012.txt` | 1 | 1 | 47 | 1 | 0 | 46 | 0 |
| `10 To 34_2007.txt` | 1 | 1 | 27 | 1 | 0 | 26 | 0 |
| `11 Tcu 106_2015.txt` | 1 | 1 | 16 | 1 | 0 | 15 | 0 |
| `11 Tcu 116_2019.txt` | 1 | 1 | 38 | 1 | 0 | 37 | 0 |
