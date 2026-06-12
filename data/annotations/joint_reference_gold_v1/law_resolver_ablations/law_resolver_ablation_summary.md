# Law Resolver Ablation Results

| Variant | Czech-law accuracy | Unresolved | Wrong law | Classification accuracy | Anchor F1 | Citation F1 |
|---|---:|---:|---:|---:|---:|---:|
| `full_spp` | 0.9101 | 37 | 20 | 0.9195 | 0.9955 | 0.9870 |
| `no_global_aliases` | 0.7003 | 170 | 20 | 0.7173 | 0.9955 | 0.9870 |
| `no_seed_aliases` | 0.9101 | 37 | 20 | 0.9195 | 0.9955 | 0.9870 |
| `no_canonical_title_harvesting` | 0.9101 | 37 | 20 | 0.9195 | 0.9955 | 0.9870 |
| `no_inflection_aware_alias_matching` | 0.8123 | 96 | 23 | 0.8298 | 0.9955 | 0.9870 |

All variants are scored on the same 25-document joint law-reference gold set.
The unresolved and wrong-law columns are counted only over gold Czech-law references that should resolve to a concrete act.
