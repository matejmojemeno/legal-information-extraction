# Law Reference LLM Comparison

- gold input: `data/annotations/llm_challenge_gold/llm_challenge_gold_v1.jsonl`
- deterministic predictions: `data/annotations/llm_challenge_gold/eval/law_llm_challenge_fresh.deterministic_predictions.jsonl`
- hybrid predictions: `data/annotations/llm_challenge_gold/eval/law_llm_challenge_fresh.hybrid_predictions.jsonl`
- routed anomaly queue: `data/annotations/llm_challenge_gold/eval/law_llm_challenge_fresh.anomalies.json`
- Step 6 results: `data/annotations/llm_challenge_gold/eval/law_llm_challenge_fresh.step6_results.jsonl`

## Whole Documents

- deterministic positive F1: `0.026`
- hybrid positive F1: `0.026`
- positive-F1 delta: `+0.0000`
- deterministic classification accuracy: `0.2778`
- hybrid classification accuracy: `0.6667`
- classification-accuracy delta: `+0.3889`
- deterministic Czech law accuracy: `0.0`
- hybrid Czech law accuracy: `0.4615`
- Czech-law-accuracy delta: `+0.4615`

## Routed Subset

- routed rows with gold match: `13`
- route reasons: `{'unresolved': 13}`
- deterministic classification accuracy: `0.0000`
- hybrid classification accuracy: `0.5385`
- routed classification delta: `+0.5385`
- deterministic Czech law accuracy: `0.0000`
- hybrid Czech law accuracy: `0.4615`
- routed Czech-law delta: `+0.4615`
- deterministic full accuracy: `0.0000`
- hybrid full accuracy: `0.4615`
- routed full delta: `+0.4615`

