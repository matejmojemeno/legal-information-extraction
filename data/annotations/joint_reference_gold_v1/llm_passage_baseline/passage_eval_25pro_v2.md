# LLM Passage Baseline Evaluation

- results: `data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_results_25pro_v2.jsonl`
- prediction rows: `data/annotations/joint_reference_gold_v1/llm_passage_baseline/passage_predictions_25pro_v2.jsonl`

## Exact-Span Metrics

| Task | Gold | Predicted | TP | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Combined | 758 | 848 | 158 | 0.1863 | 0.2084 | 0.1968 |
| Law references | 646 | 704 | 50 | 0.0710 | 0.0774 | 0.0741 |
| Document references | 112 | 144 | 108 | 0.7500 | 0.9643 | 0.8437 |

## Relaxed Overlap Metrics

| Task | TP | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Law references | 578 | 0.8210 | 0.8947 | 0.8563 |
| Document references | 110 | 0.7639 | 0.9821 | 0.8594 |

## Law-ID Diagnostic

- Czech-law ID accuracy on exact law-span matches: `0.5102`
- support: `49`
- share with any predicted law id: `0.5102`

## Prediction Quality Counts

- invalid_offsets: `5`
- offset_corrected_from_nearest_text: `312`
- offset_corrected_from_unique_text: `922`
- offset_not_corrected_text_not_found: `19`
- text_mismatch: `1253`
