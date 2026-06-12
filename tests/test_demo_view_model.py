import unittest

from demo_app.services.pipeline_runner import _apply_law_ai_results
from demo_app.services.view_model import build_law_items


class DemoViewModelTests(unittest.TestCase):
    def test_highlight_expands_to_inflected_law_alias(self) -> None:
        text = "Podle ustanovení § 45 odst.\n2 exekučního řádu"
        law_occurrences = [
            {
                "citation_text": "§ 45",
                "citation_type": "section",
                "raw_span": {"start": 17, "end": 21},
                "parsed_detail": {"number": "45", "odst": ["2"]},
                "resolved_law_id": "120/2001 Sb.",
                "predicted_classification": "czech_resolved",
            }
        ]
        canonical_law_names = {"120/2001 Sb.": "exekuční řád"}
        law_alias_index = {"120/2001 Sb.": ["exekuční řád"]}

        items = build_law_items(text, law_occurrences, canonical_law_names, law_alias_index)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].surface, "§ 45 odst.\n2 exekučního řádu")

    def test_highlight_expands_over_structural_gap_before_alias(self) -> None:
        text = "§ 252 odst. 2 věty první o. s. ř."
        law_occurrences = [
            {
                "citation_text": "§ 252",
                "citation_type": "section",
                "raw_span": {"start": 0, "end": 5},
                "parsed_detail": {"number": "252", "odst": ["2"]},
                "resolved_law_id": "99/1963 Sb.",
                "predicted_classification": "czech_resolved",
            }
        ]
        canonical_law_names = {"99/1963 Sb.": "Občanský soudní řád"}
        law_alias_index = {"99/1963 Sb.": ["o. s. ř."]}

        items = build_law_items(text, law_occurrences, canonical_law_names, law_alias_index)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].surface, "§ 252 odst. 2 věty první o. s. ř.")

    def test_merges_unresolved_coordinated_sections_with_shared_candidates(self) -> None:
        text = "§ 11 a 12 obč. zák."
        law_occurrences = [
            {
                "citation_text": "§ 11",
                "citation_type": "section",
                "raw_span": {"start": 0, "end": 4},
                "parsed_detail": {"number": "11"},
                "resolved_law_id": None,
                "predicted_classification": "czech_unresolved",
                "candidate_law_ids": ["40/1964 Sb.", "89/2012 Sb."],
            },
            {
                "citation_text": "12",
                "citation_type": "section",
                "raw_span": {"start": 7, "end": 9},
                "parsed_detail": {"number": "12"},
                "resolved_law_id": None,
                "predicted_classification": "czech_unresolved",
                "candidate_law_ids": ["40/1964 Sb.", "89/2012 Sb."],
            },
        ]

        items = build_law_items(text, law_occurrences, {}, {"40/1964 Sb.": ["obč. zák."], "89/2012 Sb.": ["obč. zák."]})

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].surface, "§ 11 a 12 obč. zák.")

    def test_rejects_ai_law_resolution_outside_occurrence_shortlist(self) -> None:
        law_occurrences = [
            {
                "citation_text": "§ 11",
                "citation_type": "section",
                "raw_span": {"start": 0, "end": 4},
                "parsed_detail": {"number": "11"},
                "resolved_law_id": None,
                "predicted_classification": "czech_unresolved",
                "candidate_law_ids": ["40/1964 Sb.", "89/2012 Sb."],
            },
            {
                "citation_text": "12",
                "citation_type": "section",
                "raw_span": {"start": 7, "end": 9},
                "parsed_detail": {"number": "12"},
                "resolved_law_id": None,
                "predicted_classification": "czech_unresolved",
                "candidate_law_ids": ["40/1964 Sb.", "89/2012 Sb."],
            },
        ]
        ai_rows = [
            {
                "entry_id": "uploaded::debug_obc.txt::0:4:section",
                "result": {
                    "classification": "czech_resolved",
                    "resolved_law_id": "1/1993 Sb.",
                    "confidence": 0.9,
                    "rationale": "wrong",
                },
                "model": "test",
            },
            {
                "entry_id": "uploaded::debug_obc.txt::7:9:section",
                "result": {
                    "classification": "czech_resolved",
                    "resolved_law_id": "1/1993 Sb.",
                    "confidence": 0.9,
                    "rationale": "wrong",
                },
                "model": "test",
            },
        ]

        enriched = _apply_law_ai_results(law_occurrences, "debug_obc.txt", ai_rows)

        self.assertTrue(all(row["resolved_law_id"] is None for row in enriched))
        self.assertTrue(all(row["predicted_classification"] == "czech_unresolved" for row in enriched))
        self.assertTrue(all(row["resolver_stage"] == "BRL review rejected" for row in enriched))

    def test_highlight_expands_to_police_law_long_form_title(self) -> None:
        text = "§ 42f zákona o Policii České republiky"
        law_occurrences = [
            {
                "citation_text": "§ 42f",
                "citation_type": "section",
                "raw_span": {"start": 0, "end": 5},
                "parsed_detail": {"number": "42f"},
                "resolved_law_id": "273/2008 Sb.",
                "predicted_classification": "czech_resolved",
            }
        ]
        canonical_law_names = {"273/2008 Sb.": "Zákon o Policii České republiky"}
        law_alias_index = {"273/2008 Sb.": ["zákon o Policii České republiky"]}

        items = build_law_items(text, law_occurrences, canonical_law_names, law_alias_index)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].surface, "§ 42f zákona o Policii České republiky")

    def test_highlight_expands_foreign_law_phrase(self) -> None:
        text = "čl. 101 Smlouvy o fungování Evropské unie"
        law_occurrences = [
            {
                "citation_text": "čl. 101",
                "citation_type": "article",
                "raw_span": {"start": 0, "end": 7},
                "parsed_detail": {"number": "101"},
                "resolved_law_id": None,
                "predicted_classification": "foreign_law",
            }
        ]

        items = build_law_items(text, law_occurrences, {}, {})

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].surface, "čl. 101 Smlouvy o fungování Evropské unie")

    def test_merges_sections_connected_by_ve_spojeni_s(self) -> None:
        text = "§ 21f ve spojení s § 20 odst. 1 písm. a) a odst. 3 zákona o ochraně hospodářské soutěže"
        law_occurrences = [
            {
                "citation_text": "§ 21f",
                "citation_type": "section",
                "raw_span": {"start": 0, "end": 5},
                "parsed_detail": {"number": "21f"},
                "resolved_law_id": "143/2001 Sb.",
                "predicted_classification": "czech_resolved",
            },
            {
                "citation_text": "§ 20",
                "citation_type": "section",
                "raw_span": {"start": 19, "end": 23},
                "parsed_detail": {"number": "20", "odst": ["1", "3"], "pism": ["a"]},
                "resolved_law_id": "143/2001 Sb.",
                "predicted_classification": "czech_resolved",
            },
        ]
        canonical_law_names = {
            "143/2001 Sb.": "Zákon o ochraně hospodářské soutěže a o změně některých zákonů (zákon o ochraně hospodářské soutěže)"
        }
        law_alias_index = {"143/2001 Sb.": ["zákona o ochraně hospodářské soutěže"]}

        items = build_law_items(text, law_occurrences, canonical_law_names, law_alias_index)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].surface,
            "§ 21f ve spojení s § 20 odst. 1 písm. a) a odst. 3 zákona o ochraně hospodářské soutěže",
        )
        self.assertIn("section 21f", items[0].detail)
        self.assertIn("section 20 | paragraph 1 letter a; paragraph 3", items[0].detail)


if __name__ == "__main__":
    unittest.main()
