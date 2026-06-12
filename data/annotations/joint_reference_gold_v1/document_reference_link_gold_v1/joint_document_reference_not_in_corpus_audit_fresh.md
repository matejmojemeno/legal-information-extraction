# Not-In-Corpus Audit

- reviewed: `data/annotations/joint_reference_gold_v1/document_reference_link_review_v1/joint_document_reference_link_review_v1.jsonl`
- self-identifiers: `data/final_runs/thesis_final_v1/document_references/document_self_identifiers.jsonl`
- audited `not_in_corpus` rows: `49`

## Priority Counts

- `high`: `18`
- `low`: `23`
- `medium`: `6`
- `skip`: `2`

## Likely Source Counts

- `ns`: `29`
- `nss`: `7`
- `uohs`: `11`

## Suspicion Counts

- `exact_candidate_multiple`: `1`
- `no_candidate_found`: `34`
- `outside_priority_scope`: `2`
- `strong_fuzzy_candidate`: `12`

## Top Re-review Rows

- `609828.txt::docref::418::58` | `sp. zn. S 19/05` | priority=`high` | likely=['uohs'] | suspicion=`exact_candidate_multiple`
  exact -> `uohs:pis24980.txt` `S019/05-4013/05-OOHS` via `body_uohs_root`
  exact -> `uohs:pis23302.txt` `VZ/S019/05` via `body_uohs_root`
- `2016_S0551.txt::docref::20220::88` | `č. j. 62 Af 41/2010-72` | priority=`medium` | likely=['nss'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `nss:548494.txt` `22 Af 41/2010` score=`0.9876`
  fuzzy -> `nss:548495.txt` `22 Af 41/2010` score=`0.9876`
  fuzzy -> `nss:569308.txt` `62 Af 10/2010` score=`0.9876`
- `2016_S0551.txt::docref::24788::92` | `čj. 62 Ca 77/2008-45` | priority=`medium` | likely=['nss'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `nss:569335.txt` `62 Cad 77/2009 - 34` score=`0.93`
  fuzzy -> `nss:559151.txt` `62 Ca 86/2008` score=`0.9186`
  fuzzy -> `nss:559152.txt` `62 Ca 86/2008` score=`0.9186`
- `23 cdo 1135_2022_openElement.txt::docref::3468::28` | `č. j. 21 Co 123/2020-294` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:21 Cdo 123_2006.txt` `21 Cdo 123/2006` score=`0.9179`
  fuzzy -> `ns:21 Cdo 137_2020.txt` `21 Cdo 137/2020` score=`0.9179`
  fuzzy -> `ns:21 Cdo 138_2020.txt` `21 Cdo 138/2020` score=`0.9179`
- `29 odo 1019_2006_openElement.txt::docref::9158::40` | `sp. 
zn. 20 Cdo 91/99` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:20 Cdo 931_99.txt` `20 Cdo 931/99` score=`1.09`
  fuzzy -> `ns:20 Cdo 991_99.txt` `20 Cdo 991/99` score=`1.09`
  fuzzy -> `ns:20 Cdo 1391_99.txt` `20 Cdo 1391/99` score=`1.0531`
- `30 cdo 1354_2006_openElement.txt::docref::1624::42` | `č.j. 22 Co 412/2005-98` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:22 Cdo 1412_2005.txt` `22 Cdo 1412/2005` score=`0.9785`
  fuzzy -> `ns:20 Co 412_2005.txt` `20 Co 412/2005` score=`0.9687`
  fuzzy -> `ns:22 Cdo 410_2005.txt` `22 Cdo 410/2005` score=`0.9425`
- `621783.txt::docref::418::68` | `sp. zn. 6 Ca 172/2008` | priority=`medium` | likely=['nss'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `nss:561741.txt` `6 Ca 312/2008 - 39` score=`0.9042`
  fuzzy -> `nss:613363.txt` `6 As 12/2008 - 73` score=`0.8633`
  fuzzy -> `nss:563168.txt` `18 Cad 172/2008 - 61` score=`0.8573`
- `7 tdo 1449_2003_openElement.txt::docref::140::49` | `sp. zn. 8 To 357/2002` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:7 Tdo 357_2002.txt` `7 Tdo 357/2002` score=`1.0489`
  fuzzy -> `ns:8 To 59_2002.txt` `8 To 59/2002` score=`1.04`
  fuzzy -> `ns:33 Odo 357_2002.txt` `33 Odo 357/2002` score=`0.9457`
- `GetText.aspx_sz_2-111-04.txt::docref::117::8` | `sp. zn. 4 To 111/2003` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:4 Tz 111_2003.txt` `4 Tz 111/2003` score=`1.1231`
  fuzzy -> `ns:3 Tdo 111_2003.txt` `3 Tdo 111/2003` score=`1.0489`
  fuzzy -> `ns:4 To 61_2003.txt` `4 To 61/2003` score=`1.04`
- `GetText.aspx_sz_2-111-04.txt::docref::4221::13` | `č. j. 4 To 54/2003-1280` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:4 Tz 54_2003.txt` `4 Tz 54/2003` score=`0.9286`
  fuzzy -> `ns:4 To 54_2014.txt` `4 To 54/2014` score=`0.8886`
  fuzzy -> `ns:4 Tz 54_2002.txt` `4 Tz 54/2002` score=`0.8886`
- `GetText.aspx_sz_2-111-04.txt::docref::4982::15` | `č. j. 4 To 111/2003-1355` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:4 Tz 111_2003.txt` `4 Tz 111/2003` score=`0.9442`
  fuzzy -> `ns:3 Tdo 111_2003.txt` `3 Tdo 111/2003` score=`0.88`
  fuzzy -> `ns:4 To 61_2003.txt` `4 To 61/2003` score=`0.8633`
- `GetText.aspx_sz_3-444-97.txt::docref::172::20` | `č.j. 11 To 60/97-2030` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:11 Tdo 60_2003.txt` `11 Tdo 60/2003` score=`0.93`
  fuzzy -> `ns:11 Tvo 60_2002.txt` `11 Tvo 60/2002` score=`0.93`
  fuzzy -> `ns:11 Tvo 60_2004.txt` `11 Tvo 60/2004` score=`0.93`
- `GetText.aspx_sz_3-444-97.txt::docref::4126::23` | `č.j. 1 1 To G0/97-2030` | priority=`high` | likely=['ns'] | suspicion=`strong_fuzzy_candidate`
  fuzzy -> `ns:11 Tdo 60_2003.txt` `11 Tdo 60/2003` score=`0.93`
  fuzzy -> `ns:11 Tvo 60_2002.txt` `11 Tvo 60/2002` score=`0.93`
  fuzzy -> `ns:11 Tvo 60_2004.txt` `11 Tvo 60/2004` score=`0.93`
- `2016_S0551.txt::docref::2874::83` | `č. j.: MVS-2059/2016` | priority=`low` | likely=['uohs'] | suspicion=`no_candidate_found`
  note -> `manual_v1:zadavatel outgoing letter reference, not represented as a corpus target document`
- `29 odo 1019_2006_openElement.txt::docref::1533::38` | `sp. zn. 28 Cm 52/2003` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze commercial proceeding outside current corpus`
- `29 odo 1019_2006_openElement.txt::docref::3810::39` | `sp. zn. 28 
Cm 52/2003` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze commercial proceeding outside current corpus`
- `29 odo 1019_2006_openElement.txt::docref::964::37` | `č. j. 30 Cm 46/2004-29` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze commercial file outside current corpus`
- `30 cdo 1354_2006_openElement.txt::docref::67::41` | `č.j. 6 C 245/2004-70` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Okresní soud v Pardubicích civil judgment outside current corpus`
- `30 cdo 1363_2022_openElement.txt::docref::333::43` | `č. j. 
15 C 127/2017-158` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Obvodní soud pro Prahu 1 civil judgment outside current corpus`
- `7 tdo 1449_2003_openElement.txt::docref::280::50` | `sp. zn. 7 T 15/2002` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Obvodní soud pro Prahu 2 criminal file outside current corpus`
- `GetText.aspx_sz_1-293-96.txt::docref::105::1` | `sp. zn. 28 Ca 209/95` | priority=`medium` | likely=['nss'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze file 28 Ca 209/95 is outside current corpus`
- `GetText.aspx_sz_1-293-96.txt::docref::252::2` | `č.j. Vl-5500/93/Je` | priority=`high` | likely=['uohs'] | suspicion=`no_candidate_found`
  note -> `manual_v1:Katastrální úřad file reference outside current corpus`
- `GetText.aspx_sz_1-293-96.txt::docref::6809::3` | `sp. zn. 28 Ca 209/95` | priority=`medium` | likely=['nss'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze file 28 Ca 209/95 is outside current corpus`
- `GetText.aspx_sz_1-293-96.txt::docref::7511::4` | `sp. zn. 28 Ca 209/95` | priority=`medium` | likely=['nss'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze file 28 Ca 209/95 is outside current corpus`
- `GetText.aspx_sz_1-293-96.txt::docref::7669::5` | `č.j. V1-5500/93/Je` | priority=`high` | likely=['uohs'] | suspicion=`no_candidate_found`
  note -> `manual_v1:Katastrální úřad file reference outside current corpus`
- `GetText.aspx_sz_1-432-97.txt::docref::360::7` | `sp. zn. 24 C 76/95` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Obvodní soud pro Prahu 4 civil file outside current corpus`
- `GetText.aspx_sz_1-432-97.txt::docref::90::6` | `čj. 36 Co 13/97 - 24` | priority=`high` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Městský soud v Praze appellate file outside current corpus`
- `GetText.aspx_sz_2-111-04.txt::docref::1620::11` | `sp. zn. 43 T 3/2001` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Krajský soud v Brně criminal file 43 T 3/2001 is outside current corpus`
- `GetText.aspx_sz_2-111-04.txt::docref::194::9` | `sp. zn. 43 T 3/2001` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Krajský soud v Brně criminal file 43 T 3/2001 is outside current corpus`
- `GetText.aspx_sz_2-111-04.txt::docref::3218::12` | `č. j. 43 T 3/2001-1261` | priority=`low` | likely=['ns'] | suspicion=`no_candidate_found`
  note -> `manual_v1:reference to Krajský soud v Brně criminal judgment outside current corpus`
