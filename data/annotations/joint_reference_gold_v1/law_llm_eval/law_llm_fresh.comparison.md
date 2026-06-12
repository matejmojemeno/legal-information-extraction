# Law Reference LLM Comparison

- gold input: `data/annotations/joint_reference_gold_v1/law_gold/joint_law_reference_gold_v1.jsonl`
- deterministic predictions: `data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.deterministic_predictions.jsonl`
- hybrid predictions: `data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.hybrid_predictions.jsonl`
- routed anomaly queue: `data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.anomalies.json`
- Step 6 results: `data/annotations/joint_reference_gold_v1/law_llm_eval/law_llm_fresh.step6_results.jsonl`

## Whole Documents

- deterministic positive F1: `0.987`
- hybrid positive F1: `0.987`
- positive-F1 delta: `+0.0000`
- deterministic classification accuracy: `0.9195`
- hybrid classification accuracy: `0.9544`
- classification-accuracy delta: `+0.0349`
- deterministic Czech law accuracy: `0.9085`
- hybrid Czech law accuracy: `0.9432`
- Czech-law-accuracy delta: `+0.0347`

## Routed Subset

- routed rows with gold match: `42`
- route reasons: `{'unresolved': 42}`
- deterministic classification accuracy: `0.0000`
- hybrid classification accuracy: `0.5476`
- routed classification delta: `+0.5476`
- deterministic Czech law accuracy: `0.0000`
- hybrid Czech law accuracy: `0.5946`
- routed Czech-law delta: `+0.5946`
- deterministic full accuracy: `0.0000`
- hybrid full accuracy: `0.5476`
- routed full delta: `+0.5476`

