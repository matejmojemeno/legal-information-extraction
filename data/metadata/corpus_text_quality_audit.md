# Corpus Text Quality Audit

Deterministic heuristic audit of normalized text quality. Categories are based on surface text features, not manual legal-quality annotation.

- sample size per source: `400`
- randomization seed: `thesis_text_quality_v1`
- sampled documents: `1600`

## Category Counts by Source

| Source | Available | Sampled | Readable | Artifacts | Empty/short |
|---|---:|---:|---:|---:|---:|
| NALUS | 102126 | 400 | 392 | 0 | 8 |
| NS | 160609 | 400 | 390 | 3 | 7 |
| NSS | 161151 | 400 | 396 | 4 | 0 |
| UOHS | 21346 | 400 | 387 | 13 | 0 |

## Selected Feature Summary

| Source | Median chars | Median tokens | Mean short-line ratio | Mean duplicate-line ratio | Mean hyphen breaks / 10k chars |
|---|---:|---:|---:|---:|---:|
| NALUS | 6547.5 | 996.5 | 0.0519 | 0.0002 | 0.1086 |
| NS | 8666.0 | 1344.5 | 0.0723 | 0.0025 | 0.0000 |
| NSS | 17940.5 | 2778.0 | 0.1131 | 0.0112 | 0.2737 |
| UOHS | 16618.5 | 2451.0 | 0.1634 | 0.0197 | 0.9562 |

## Example Flagged Documents

### empty_or_short

- `nalus` `3-246-03.txt`: very short or nearly empty text
- `nalus` `4-195-07_2.txt`: very short or nearly empty text
- `nalus` `3-2910-18_1.txt`: very short or nearly empty text
- `nalus` `4-3636-16_1.txt`: very short or nearly empty text
- `nalus` `2-1662-08_3.txt`: very short or nearly empty text

### artifacts

- `ns` `29 NSCR 59_2025.txt`: many short lines
- `ns` `33 Cdo 5473_2016.txt`: many short lines
- `ns` `21 Nd 44_2015.txt`: many short lines
- `nss` `700193.txt`: many short lines
- `nss` `717973.txt`: many short lines
