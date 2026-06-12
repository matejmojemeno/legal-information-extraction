# Joint Reference Gold Set Annotation Plan

This note lists the shared 25-document evaluation set in a practical annotation order.

Recommended workflow:
- start with `light` documents to calibrate the annotation routine
- continue with `medium` documents
- leave `dense` documents for the end

The same documents are intended to be annotated for both:
- law references
- document references

## Light

1. `nalus / GetText.aspx_sz_1-256-2000.txt`
2. `nalus / GetText.aspx_sz_2-4524-12_1.txt`
3. `nalus / GetText.aspx_sz_1-432-97.txt`
4. `nss / 621783.txt`
5. `nss / 230526.txt`

## Medium

1. `ns / 30 cdo 1363_2022_openElement.txt`
2. `uohs / pis6543.txt`
3. `nss / 231833.txt`
4. `nss / 237253.txt`
5. `nalus / GetText.aspx_sz_2-111-04.txt`
6. `uohs / pis7143.txt`
7. `uohs / pis8046.txt`

## Dense

1. `ns / 7 tdo 1449_2003_openElement.txt`
2. `nss / 724902.txt`
3. `uohs / 2021_S0666.txt`
4. `nalus / GetText.aspx_sz_3-444-97.txt`
5. `ns / 29 icdo 2_2022_openElement.txt`
6. `uohs / pis33402.txt`
7. `nss / 609828.txt`
8. `ns / 23 cdo 1135_2022_openElement.txt`
9. `uohs / 2013_R184_2.txt`
10. `ns / 29 odo 1019_2006_openElement.txt`
11. `ns / 30 cdo 1354_2006_openElement.txt`
12. `nalus / GetText.aspx_sz_1-293-96.txt`
13. `uohs / 2016_S0551.txt`

## Notes

- The underlying sample is source-balanced and randomly drawn at the document level.
- The order here is only for annotation convenience; it does not change the sample itself.
- If the current systems are evaluated on this set and no extractor/linker changes are made afterward, this set can serve as the final thesis test set.
- If system changes are made after reviewing results on this set, this set should be treated as a development set and a fresh final test set should be sampled later.
