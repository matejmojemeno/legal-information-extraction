# Law Confidence Analysis

- gold input: `data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl`
- predictions input: `data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.deterministic_predictions.jsonl`
- matched gold findings: `658`
- unmatched gold findings: `0`

| Confidence | Stage family | Matched | Class acc. | Czech-law acc. | Czech unresolved | Czech wrong |
|---:|---|---:|---:|---:|---:|---:|
| 0.99 | Filtered: Non-Statutory Collection | 1 | 1.0000 | -- | 0 | 0 |
| 0.99 | Law ID Mention | 125 | 0.9760 | 1.0000 | 0 | 0 |
| 0.99 | Level 1: Direct Match | 54 | 1.0000 | 0.9630 | 0 | 2 |
| 0.93 | Level 5: Local Dictionary | 111 | 1.0000 | 0.9369 | 0 | 7 |
| 0.92 | Level 2A: Section Anaphora | 1 | 1.0000 | 1.0000 | 0 | 0 |
| 0.90 | Level 2: Explicit Anaphora | 12 | 1.0000 | 1.0000 | 0 | 0 |
| 0.86 | Level 6: Global Dictionary | 146 | 0.9795 | 0.9720 | 0 | 4 |
| 0.82 | Typed: Foreign Law | 7 | 1.0000 | -- | 0 | 0 |
| 0.72 | Level 3B: Citation Chain Carryover | 21 | 0.8571 | 0.7222 | 0 | 5 |
| 0.70 | Level 3C: Backward Context Carryover | 8 | 0.8750 | 0.5714 | 0 | 3 |
| 0.62 | Level 3: Implicit Generic Reference | 130 | 0.9923 | 1.0000 | 0 | 0 |
| 0.00 | Level 7: Unresolved | 42 | 0.0000 | 0.0000 | 37 | 0 |
