import tempfile
import unittest
from pathlib import Path

from demo_app.services.linker_cache import build_uploaded_reference_llm_tasks
from src.citation_extractor import extract_citation_occurrences
from src.alias_loader import load_runtime_aliases
from src.document_reference_extractor import extract_document_references
from src.document_reference_linker import (
    DocumentSelfIdentifier,
    build_reference_candidate_targets,
    extract_document_self_identifiers,
)
from src.document_reference_llm_prompts import build_link_disambiguation_prompt


class DocumentReferenceExtractorTests(unittest.TestCase):
    def test_trims_trailing_court_prose_after_docket(self) -> None:
        text = "Rozsudek odvolacího soudu z 27. 1. 2011 (sp. zn. 69Co 371/2010 Krajského soudu v Ostravě – pobočka v Olomouci) byl dne 6. 4. 2011 doručen."

        refs = extract_document_references(text)

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].reference_text, "sp. zn. 69Co 371/2010")
        self.assertEqual(refs[0].reference_body, "69Co 371/2010")

    def test_trims_trailing_publication_prose_after_us_reference(self) -> None:
        text = (
            "nálezem ÚS sp. zn. Pl. ÚS 4/20 vyhlášeným ve Sbírce zákonů dne 22. 7. 2020 "
            "pod č. 325/2020 Sb."
        )

        refs = extract_document_references(text)

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].reference_text, "sp. zn. Pl. ÚS 4/20")
        self.assertEqual(refs[0].reference_body, "Pl. ÚS 4/20")

    def test_keeps_split_prefixed_chain_behavior(self) -> None:
        text = (
            "sp. zn. I. ÚS 2959/20 a usnesení Obvodního soudu pro Prahu 9 ze dne 30. června 2025 "
            "sp. zn. T 162/2019"
        )

        refs = extract_document_references(text)

        self.assertEqual(
            [ref.reference_text for ref in refs],
            ["sp. zn. I. ÚS 2959/20", "sp. zn. T 162/2019"],
        )

    def test_filters_uohs_self_reference_before_candidate_building(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "uohs" / "2018_S0181.txt"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("sp. zn. S0181/2018/VZ\n", encoding="utf-8")

            reference = extract_document_references("sp. zn. ÚOHS-S0181/2018/VS")[0]
            candidate_info = build_reference_candidate_targets(
                reference=reference,
                source_document_path=str(source_path),
                source_document_source="uohs",
                self_id_index={},
            )

            self.assertEqual(candidate_info["llm_route"], "filtered_out_of_scope")
            self.assertEqual(candidate_info["filtered_reason"], "uohs_self_reference")

    def test_filters_reporter_and_foreign_scope_cases(self) -> None:
        reporter_ref = extract_document_references("č.j. R 53/2002")[0]
        foreign_ref = extract_document_references("sp. zn. C-360/96")[0]

        reporter_info = build_reference_candidate_targets(
            reference=reporter_ref,
            source_document_path="/tmp/source.txt",
            source_document_source="uohs",
            self_id_index={},
        )
        foreign_info = build_reference_candidate_targets(
            reference=foreign_ref,
            source_document_path="/tmp/source.txt",
            source_document_source="uohs",
            self_id_index={},
        )

        self.assertEqual(reporter_info["llm_route"], "filtered_out_of_scope")
        self.assertEqual(reporter_info["filtered_reason"], "uohs_reporter_citation")
        self.assertEqual(foreign_info["llm_route"], "filtered_out_of_scope")
        self.assertEqual(foreign_info["filtered_reason"], "foreign_case_out_of_scope")

    def test_filters_metadata_backed_uohs_self_reference(self) -> None:
        reference = extract_document_references("č. j. S250/2006/SZ-15118/2006/520-KV")[0]
        source_path = str(Path("/tmp/pis32510.txt").resolve())
        self_identifier = DocumentSelfIdentifier(
            document_id="pis32510.txt",
            document_path=source_path,
            source="uohs",
            identifier_text="VZ/S250/06",
            identifier_kind="spisova_znacka",
            origin="external_name",
            keys=("S2502006", "VZS25006"),
            source_iri=None,
        )

        candidate_info = build_reference_candidate_targets(
            reference=reference,
            source_document_path=source_path,
            source_document_source="uohs",
            self_id_index={
                "S2502006": [self_identifier],
                "VZS25006": [self_identifier],
            },
        )

        self.assertEqual(candidate_info["llm_route"], "filtered_out_of_scope")
        self.assertEqual(candidate_info["filtered_reason"], "uohs_self_reference")

    def test_filters_obvious_administrative_internal_identifiers(self) -> None:
        admin_refs = [
            extract_document_references("č. j. Fj 88410/2017/KSBR")[0],
            extract_document_references("sp. zn. UMCP14/14/20148/OV/BARK")[0],
            extract_document_references("č. j. 5920/97")[0],
        ]

        for ref in admin_refs:
            info = build_reference_candidate_targets(
                reference=ref,
                source_document_path="/tmp/source.txt",
                source_document_source="ns",
                self_id_index={},
            )
            self.assertEqual(info["llm_route"], "filtered_out_of_scope")
            self.assertEqual(info["filtered_reason"], "administrative_internal_identifier")

    def test_does_not_offer_apex_fuzzy_candidates_for_vrchni_soud_reference(self) -> None:
        text = (
            "o dovolání žalovaného proti rozsudku Vrchního soudu v Olomouci ze dne 28. "
            "února 2013, č. j. 5 Cmo 407/2012-191, takto"
        )
        reference = extract_document_references(text)[0]
        ns_identifier = DocumentSelfIdentifier(
            document_id="25 Cdo 407_2012.txt",
            document_path="/tmp/25 Cdo 407_2012.txt",
            source="ns",
            identifier_text="25 Cdo 407/2012",
            identifier_kind="spisova_znacka",
            origin="parent_metadata_name",
            keys=("25CDO4072012",),
            source_iri="https://rozhodnuti.nsoud.cz/example",
        )

        info = build_reference_candidate_targets(
            reference=reference,
            source_document_path="/tmp/source.txt",
            source_document_source="uploaded",
            self_id_index={},
            all_identifiers=[ns_identifier],
        )

        self.assertEqual(info["llm_route"], "extraction_presence_check")
        self.assertEqual(info["candidate_targets"], [])

    def test_dedupes_nalus_copy_variants_in_candidate_targets(self) -> None:
        reference = extract_document_references("sp. zn. Pl. ÚS 20/15")[0]
        identifiers = [
            DocumentSelfIdentifier(
                document_id="Pl-20-15_1.txt",
                document_path="/tmp/Pl-20-15_1.txt",
                source="nalus",
                identifier_text="Pl. ÚS 20/15",
                identifier_kind="spisova_znacka",
                origin="parent_metadata_name_normalized_nalus",
                keys=("PLUS2015",),
                source_iri="https://nalus.usoud.cz/Search/GetText.aspx?sz=Pl-20-15_1",
            ),
            DocumentSelfIdentifier(
                document_id="Pl-20-15_2.txt",
                document_path="/tmp/Pl-20-15_2.txt",
                source="nalus",
                identifier_text="Pl. ÚS 20/15",
                identifier_kind="spisova_znacka",
                origin="parent_metadata_name_normalized_nalus",
                keys=("PLUS2015",),
                source_iri="https://nalus.usoud.cz/Search/GetText.aspx?sz=Pl-20-15_2",
            ),
        ]

        info = build_reference_candidate_targets(
            reference=reference,
            source_document_path="/tmp/source.txt",
            source_document_source="nalus",
            self_id_index={"PLUS2015": identifiers},
        )

        self.assertEqual(info["llm_route"], "link_normalization_or_target_recovery")
        self.assertEqual(len(info["candidate_targets"]), 1)
        self.assertEqual(info["candidate_targets"][0]["target_identifier"], "Pl. ÚS 20/15")

    def test_candidate_targets_include_metadata_and_duplicate_hints(self) -> None:
        reference = extract_document_references("č. j. 62 Af 57/2011-96")[0]
        identifiers = [
            DocumentSelfIdentifier(
                document_id="555342.txt",
                document_path="/tmp/nss/555342.txt",
                source="nss",
                identifier_text="62 Af 57/2011 - 96",
                identifier_kind="unknown",
                origin="parent_metadata_name",
                keys=("62AF57201196",),
                source_iri="https://example.test/555342",
                decision_date="01.11.2012",
                decision_date_iso="2012-11-01",
                decision_year=2012,
                decision_date_precision="day",
                judicate_name="62 Af 57/2011 - 96",
                blob_name="NSS/555342.pdf",
            ),
            DocumentSelfIdentifier(
                document_id="555343.txt",
                document_path="/tmp/nss/555343.txt",
                source="nss",
                identifier_text="62 Af 57/2011 - 96",
                identifier_kind="unknown",
                origin="parent_metadata_name",
                keys=("62AF57201196",),
                source_iri="https://example.test/555343",
                decision_date="01.11.2012",
                decision_date_iso="2012-11-01",
                decision_year=2012,
                decision_date_precision="day",
                judicate_name="62 Af 57/2011 - 96",
                blob_name="NSS/555343.pdf",
            ),
        ]

        info = build_reference_candidate_targets(
            reference=reference,
            source_document_path="/tmp/source.txt",
            source_document_source="uohs",
            self_id_index={"62AF57201196": identifiers},
        )

        self.assertEqual(info["llm_route"], "link_disambiguation")
        self.assertEqual(len(info["candidate_targets"]), 2)
        first = info["candidate_targets"][0]
        self.assertEqual(first["target_decision_date_iso"], "2012-11-01")
        self.assertEqual(first["target_judicate_name"], "62 Af 57/2011 - 96")
        self.assertEqual(first["target_duplicate_group_size"], 2)
        self.assertEqual(first["target_duplicate_canonical_document_id"], "555342.txt")
        self.assertTrue(first["target_is_duplicate_canonical"])

    def test_disambiguation_prompt_allows_provided_metadata(self) -> None:
        prompt = build_link_disambiguation_prompt(
            {
                "target_reference": "č. j. 62 Af 57/2011-96",
                "context_block": "rozsudku ze dne 1. 11. 2012 č. j. 62 Af 57/2011-96",
                "candidate_targets": [
                    {
                        "target_document_id": "555342.txt",
                        "target_decision_date_iso": "2012-11-01",
                        "target_duplicate_canonical_document_id": "555342.txt",
                    }
                ],
            },
            "v2",
        )

        self.assertIn("target_decision_date_iso", prompt)
        self.assertIn("target_duplicate_canonical_document_id", prompt)
        self.assertIn("date agreement", prompt)

    def test_uohs_merged_filename_exposes_component_case_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = Path(tmpdir) / "uohs" / "2014_S423_424.txt"
            merged_path.parent.mkdir(parents=True, exist_ok=True)
            merged_path.write_text("placeholder", encoding="utf-8")

            identifiers = extract_document_self_identifiers(
                str(merged_path.resolve()),
                merged_path.read_text(encoding="utf-8"),
            )

            identifier_texts = {identifier.identifier_text for identifier in identifiers}
            self.assertIn("S423/2014", identifier_texts)
            self.assertIn("S424/2014", identifier_texts)

    def test_uohs_component_case_can_surface_merged_document_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = Path(tmpdir) / "uohs" / "2014_S423_424.txt"
            merged_path.parent.mkdir(parents=True, exist_ok=True)
            merged_path.write_text("placeholder", encoding="utf-8")

            identifiers = extract_document_self_identifiers(
                str(merged_path.resolve()),
                merged_path.read_text(encoding="utf-8"),
            )
            self.assertTrue(identifiers)

            index: dict[str, list[DocumentSelfIdentifier]] = {}
            for identifier in identifiers:
                for key in identifier.keys:
                    index.setdefault(key, []).append(identifier)

            reference = extract_document_references("sp. zn. S424/2014/VZ")[0]
            info = build_reference_candidate_targets(
                reference=reference,
                source_document_path="/tmp/legacy_sample/uohs/2014_S423_424.txt",
                source_document_source="uohs",
                self_id_index=index,
            )

            self.assertEqual(info["llm_route"], "link_normalization_or_target_recovery")
            self.assertEqual(len(info["candidate_targets"]), 1)
            self.assertEqual(info["candidate_targets"][0]["target_document_id"], "2014_S423_424.txt")
            self.assertEqual(info["candidate_targets"][0]["target_identifier"], "S424/2014")

    def test_extracts_article_continuation_with_shared_law_alias(self) -> None:
        occurrences = extract_citation_occurrences(
            text="čl. 81 a 90 Ústavy",
            local_aliases={},
            global_aliases={"Ústavy": "1/1993 Sb."},
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 2)
        self.assertEqual([occ.citation_text for occ in occurrences], ["čl. 81", "90"])
        self.assertTrue(all(occ.resolved_law_id == "1/1993 Sb." for occ in occurrences))

    def test_does_not_let_backward_constitution_context_override_obc_zak_alias(self) -> None:
        text = (
            "Stěžovatel v podaném dovolání namítá mj. porušení § 2 odst. 5 a 6 a § 215 odst. 2 tr. ř., "
            "v čemž spatřuje porušení čl. 36, čl. 37 odst. 3 a čl. 38 odst. 2 Listiny a čl. 95 a 96 Ústavy, "
            "které označuje jako hmotněprávní ustanovení; rovněž uvádí, že pořízením obrazových záznamů jeho osoby "
            "bez jeho vědomí a jejich následným použitím v trestním řízení došlo k porušení jeho práv podle "
            "§ 11 a 12 obč. zák. Nejvyššímu soudu lze sice vytknout, že v napadeném usnesení tyto námitky konstatoval "
            "jen částečně, nicméně jeho závěr, že dovolací důvod podle § 265b odst. 1 písm. g) tr. ř. nebyl "
            "materiálně naplněn, pokládá Ústavní soudu za správný. S poukazem na tento dovolací důvod totiž nelze "
            "přezkoumávat a hodnotit správnost a úplnost zjištění skutkového stavu či prověřovat úplnost povedeného "
            "dokazování a správnost hodnocení důkazů ve smyslu § 2 odst. 5 a 6 tr. ř. či namítat jiné porušení "
            "trestního řádu. Pokud stěžovatel primárně namítal porušení § 2 odst. 5 a 6 a § 215 odst. 2 tr. ř., "
            "nezakládá pouhé konstatování, že tím došlo rovněž k porušení shora citovaných článků Listiny a Ústavy, "
            "které jsou dle názoru stěžovatele hmotněprávního charakteru, naplnění posuzovaného dovolacího důvodu. "
            "Stěžovatelem tvrzené porušení ustanovení § 11 a 12 obč. zák. nebylo přitom vůbec možné v rámci trestního "
            "řízení posuzovat, jak bylo v tomto usnesení shora konstatováno."
        )
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            text=text,
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        trailing = [
            occ for occ in occurrences if occ.raw_start >= 1200 and occ.citation_text in {"§ 11", "12"}
        ]

        self.assertEqual(len(trailing), 2)
        self.assertTrue(all(occ.resolved_law_id is None for occ in trailing))
        self.assertTrue(all(occ.predicted_classification == "czech_unresolved" for occ in trailing))

    def test_resolves_police_law_long_form_title(self) -> None:
        occurrences = extract_citation_occurrences(
            text="§ 42f zákona o Policii České republiky",
            local_aliases={},
            global_aliases={"zákona o Policii České republiky": "273/2008 Sb."},
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].citation_text, "§ 42f")
        self.assertEqual(occurrences[0].resolved_law_id, "273/2008 Sb.")
        self.assertEqual(occurrences[0].predicted_classification, "czech_resolved")

    def test_unmatched_local_law_cue_blocks_wrong_context_carryover(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()
        text = "Podle § 1 odst. 1 tr. ř. dále namítal porušení § 42f zákona o Policii města."

        occurrences = extract_citation_occurrences(
            text=text,
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        target = next(occ for occ in occurrences if occ.citation_text == "§ 42f")
        self.assertIsNone(target.resolved_law_id)
        self.assertEqual(target.predicted_classification, "czech_unresolved")

    def test_resolves_zohs_abbreviation_to_competition_law(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            text="§ 3 ZOHS",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "143/2001 Sb.")
        self.assertEqual(occurrences[0].predicted_classification, "czech_resolved")

    def test_classifies_tfeu_article_as_foreign_law(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            text="čl. 101 Smlouvy o fungování Evropské unie",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertIsNone(occurrences[0].resolved_law_id)
        self.assertEqual(occurrences[0].predicted_classification, "foreign_law")

    def test_resolves_competition_law_long_form_and_linked_section(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()
        text = (
            "v souladu s § 21f ve spojení s § 20 odst. 1 písm. a) a odst. 3 "
            "zákona o ochraně hospodářské soutěže"
        )

        occurrences = extract_citation_occurrences(
            text=text,
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 2)
        self.assertTrue(all(occ.resolved_law_id == "143/2001 Sb." for occ in occurrences))
        self.assertEqual([occ.citation_text for occ in occurrences], ["§ 21f", "§ 20"])

    def test_uploaded_lower_court_docrefs_without_candidates_skip_llm_tasks(self) -> None:
        text = (
            "Městského soudu sp. zn. 12 T 79/2002. "
            "Krajského soudu sp. zn. 3 To 530/2002."
        )

        refs = extract_document_references(text)
        tasks = build_uploaded_reference_llm_tasks(refs, "x.txt", {})

        self.assertEqual(len(tasks), 0)

    def test_uploaded_strong_uohs_docrefs_without_candidates_skip_llm_tasks(self) -> None:
        text = (
            "č. j. ÚOHS-15369/2024/852. "
            "č. j. ÚOHS-15369/2024/852 předsedy Úřadu. "
            "sp. zn. ÚOHS-S0493/2024/KD."
        )

        refs = extract_document_references(text)
        tasks = build_uploaded_reference_llm_tasks(refs, "x.txt", {})

        self.assertEqual(len(tasks), 0)


if __name__ == "__main__":
    unittest.main()
