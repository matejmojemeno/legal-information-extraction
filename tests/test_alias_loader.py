import unittest
from src.alias_loader import load_runtime_aliases
from src.citation_extractor import extract_citation_occurrences


class AliasLoaderTests(unittest.TestCase):
    def test_harvests_base_canonical_law_title_as_runtime_alias(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        self.assertEqual(runtime_aliases.get("zákon o mimosoudních rehabilitacích"), "87/1991 Sb.")

    def test_harvests_parenthetical_short_title_for_current_law(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        self.assertEqual(
            runtime_aliases.get("zákon o soudech a soudcích"),
            {"law_ids": ["335/1991 Sb.", "6/2002 Sb."]},
        )

    def test_resolves_reference_with_full_canonical_law_title(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            "Podle § 6 odst. 1 písm. f) zákona o mimosoudních rehabilitacích rozhodl soud.",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "87/1991 Sb.")

    def test_resolves_reference_with_harvested_parenthetical_short_title(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()
        law_timelines = {
            "335/1991 Sb.": {"timeline": [{"effective_from": "1991-09-01"}]},
            "6/2002 Sb.": {"timeline": [{"effective_from": "2002-04-01"}]},
        }

        occurrences = extract_citation_occurrences(
            "Podle § 174a zákona o soudech a soudcích se účastník může bránit průtahům.",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=law_timelines,
            document_metadata={"decision_date_iso": "2010-01-01"},
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "6/2002 Sb.")

    def test_resolves_abbreviated_trade_and_criminal_law_variants(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        trade = extract_citation_occurrences(
            "§ 242 odst. 1 obch. zákoníku",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )
        criminal = extract_citation_occurrences(
            "§ 254 odst. 1 tr. zákona",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(trade[0].resolved_law_id, "513/1991 Sb.")
        self.assertEqual(criminal[0].resolved_law_id, "140/1961 Sb.")

    def test_resolves_abbreviated_arbitration_law_variants(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            "§ 12 odst. 2 RozŘ",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "216/1994 Sb.")

    def test_resolves_partial_arbitration_law_title(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            "§ 12 odst. 2 zákona o rozhodčím řízení",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "216/1994 Sb.")

    def test_resolves_high_value_noisy_full_title_variant(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        occurrences = extract_citation_occurrences(
            "§ 150 občanského soudu řádu",
            local_aliases={},
            global_aliases=runtime_aliases,
            law_timelines=None,
        )

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].resolved_law_id, "99/1963 Sb.")

    def test_derives_structured_shortcuts_from_canonical_titles(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        self.assertEqual(
            runtime_aliases.get("zák. práce"),
            {"law_ids": ["262/2006 Sb.", "42/1970 Sb.", "65/1965 Sb."], "match_mode": "inflect"},
        )
        self.assertEqual(
            runtime_aliases.get("cizinecký zákon"),
            {"law_ids": ["123/1992 Sb.", "326/1999 Sb.", "68/1965 Sb."], "match_mode": "inflect"},
        )
        self.assertEqual(
            runtime_aliases.get("zákon o ochraně ZPF"),
            {"law_ids": ["334/1992 Sb.", "48/1959 Sb.", "53/1966 Sb."], "match_mode": "inflect"},
        )
        self.assertEqual(
            runtime_aliases.get("vyhláška o posuzování invalidity"),
            {"law_id": "359/2009 Sb.", "match_mode": "inflect"},
        )

    def test_resolves_unambiguous_structured_shortcut_variants(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        examples = {"§ 4 odst. 1 vyhlášky o posuzování invalidity": "359/2009 Sb."}

        for text, expected in examples.items():
            occurrences = extract_citation_occurrences(
                text,
                local_aliases={},
                global_aliases=runtime_aliases,
                law_timelines=None,
            )
            self.assertEqual(len(occurrences), 1, text)
            self.assertEqual(occurrences[0].resolved_law_id, expected, text)

    def test_keeps_historical_shortcuts_ambiguous_without_date(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()

        examples = [
            "§ 42 odst. 1 cizineckého zákona",
            "§ 11 zákona o ochraně ZPF",
        ]

        for text in examples:
            occurrences = extract_citation_occurrences(
                text,
                local_aliases={},
                global_aliases=runtime_aliases,
                law_timelines=None,
            )
            self.assertEqual(len(occurrences), 1, text)
            self.assertIsNone(occurrences[0].resolved_law_id, text)
            self.assertTrue(occurrences[0].candidate_law_ids, text)

    def test_resolves_historical_shortcuts_with_date(self) -> None:
        runtime_aliases, _ = load_runtime_aliases()
        law_timelines = {
            "42/1970 Sb.": {"timeline": [{"effective_from": "1970-07-01"}]},
            "65/1965 Sb.": {"timeline": [{"effective_from": "1965-09-01"}]},
            "262/2006 Sb.": {"timeline": [{"effective_from": "2007-01-01"}]},
            "68/1965 Sb.": {"timeline": [{"effective_from": "1965-07-01"}]},
            "123/1992 Sb.": {"timeline": [{"effective_from": "1992-03-20"}]},
            "326/1999 Sb.": {"timeline": [{"effective_from": "2000-01-01"}]},
            "48/1959 Sb.": {"timeline": [{"effective_from": "1959-07-01"}]},
            "53/1966 Sb.": {"timeline": [{"effective_from": "1966-07-01"}]},
            "334/1992 Sb.": {"timeline": [{"effective_from": "1993-01-01"}]},
        }

        examples = [
            ("§ 111 odst. 1 větu druhou zák. práce", "2013-01-01", "262/2006 Sb."),
            ("§ 42 odst. 1 cizineckého zákona", "2020-01-01", "326/1999 Sb."),
            ("§ 11 zákona o ochraně ZPF", "2005-01-01", "334/1992 Sb."),
        ]

        for text, decision_date_iso, expected in examples:
            occurrences = extract_citation_occurrences(
                text,
                local_aliases={},
                global_aliases=runtime_aliases,
                law_timelines=law_timelines,
                document_metadata={"decision_date_iso": decision_date_iso},
            )
            self.assertEqual(len(occurrences), 1, text)
            self.assertEqual(occurrences[0].resolved_law_id, expected, text)


if __name__ == "__main__":
    unittest.main()
