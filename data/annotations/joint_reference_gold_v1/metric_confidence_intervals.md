# Metric Confidence Intervals

Wilson score 95% intervals over reviewed rows; F1 intervals use the equivalent ratio 2TP/(2TP+FP+FN).

| Metric | Source | Estimate | 95% CI | Count |
|---|---|---:|---:|---:|
| Law anchor detection F1 | SPP | 0.9955 | [0.9901, 0.9979] | 1316/1322 |
| Law citation-presence F1 | SPP | 0.9870 | [0.9793, 0.9919] | 1292/1309 |
| Czech-law resolution accuracy | SPP | 0.9101 | [0.8853, 0.9300] | 577/634 |
| Czech-law resolution accuracy | SPP + BRL | 0.9448 | [0.9242, 0.9600] | 599/634 |
| Law classification accuracy | SPP | 0.9195 | [0.8961, 0.9379] | 605/658 |
| Law classification accuracy | SPP + BRL | 0.9544 | [0.9357, 0.9679] | 628/658 |
| Routed hard-case law classification accuracy | SPP + BRL | 0.5476 | [0.3995, 0.6878] | 23/42 |
| Routed hard-case Czech-law accuracy | SPP + BRL | 0.5946 | [0.4349, 0.7365] | 22/37 |
| Weak-evidence law full accuracy | SPP weak-evidence slice | 0.9182 | [0.8651, 0.9516] | 146/159 |
| Weak-evidence Czech-law accuracy | SPP weak-evidence slice | 0.9481 | [0.9008, 0.9734] | 146/154 |
| Weak-evidence law full accuracy | BRL verification | 0.9560 | [0.9119, 0.9785] | 152/159 |
| Weak-evidence Czech-law accuracy | BRL verification | 0.9805 | [0.9443, 0.9934] | 151/154 |
| Document-reference exact-span F1 | SPP | 0.9182 | [0.8744, 0.9476] | 202/220 |
| Document-reference exact-span F1 | BRL gap promotion | 0.8619 | [0.8124, 0.9000] | 206/239 |
| Exact-linkable recovery | SPP | 0.7826 | [0.6443, 0.8774] | 36/46 |
| Unavailable exact target correctly unresolved | SPP | 1.0000 | [0.9312, 1.0000] | 52/52 |
| Exact-linkable recovery | BRL link-only with enriched candidates | 0.9565 | [0.8547, 0.9880] | 44/46 |
| Unavailable exact target correctly unresolved | BRL link-only with enriched candidates | 1.0000 | [0.9312, 1.0000] | 52/52 |
| Document-reference route accuracy | SPP | 0.3667 | [0.2187, 0.5449] | 11/30 |
| Document-reference route accuracy | BRL | 0.9667 | [0.8333, 0.9941] | 29/30 |
