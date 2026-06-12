"""Shared route-aware prompts for document-reference LLM experiments.

This module holds the reusable prompt logic for the document-reference LLM
routes so that:
- benchmark scripts can reuse the same route definitions
- the demo app does not need to import prompt helpers from `scripts/`
"""

from __future__ import annotations

import json


def build_link_disambiguation_prompt(entry: dict, prompt_version: str) -> str:
    payload = {
        "route": "link_disambiguation",
        "target_reference": entry.get("target_reference"),
        "context_block": entry.get("context_block"),
        "candidate_targets": entry.get("candidate_targets", []),
    }
    if prompt_version == "v2":
        payload["disambiguation_policy"] = [
            "Use only explicit information in target_reference, context_block, and candidate_targets.",
            "Do not use outside legal knowledge, remembered case topics, publication data, or internet knowledge.",
            "Candidate metadata fields such as target_decision_date_iso, target_decision_date, target_judicate_name, and target_duplicate_canonical_document_id are provided evidence.",
            "If context_block gives an explicit cited decision date and candidate_targets include target_decision_date_iso or target_decision_date, use date agreement as a strong disambiguation signal.",
            "Do not infer a target from dates or subject matter unless that information is explicitly present in context_block and candidate_targets.",
            "Treat the court identity implied by context_block and target_reference as a hard constraint when it is explicit.",
            "If context_block explicitly frames the citation as a decision of a lower court such as Vrchní, Krajský, Městský, or Okresní soud, do not choose an apex-court candidate from NALUS, NS, or NSS unless the text itself explicitly supports that court jump.",
            "If candidate_targets come from a different court family than the one explicitly named in context_block, prefer unresolved or ambiguous rather than normalizing across court levels.",
            "If several candidate document_ids are marked as duplicate records for the same identifier and date, choose the target_duplicate_canonical_document_id when the citation supports that duplicate group.",
            "If the cited form omits a chamber numeral and no explicit propagation clue in the local text safely supplies it, return ambiguous.",
            "Do not propagate a chamber numeral from a neighboring citation unless one chamber-marked citation clearly governs a compact same-style list, e.g. 'IV. ÚS 10/98, ÚS 130/98, ÚS 30/02'.",
            "Do not treat a simple conjunction like 'I. ÚS 389/05 a ÚS 457/05' or a looser narrative sequence like 'I. ÚS 215/95 ... ÚS 116/96' as safe propagation.",
            "If neighboring-list propagation would imply a chamber numeral that is absent from all provided candidates, do not convert that into unresolved by itself; prefer ambiguous unless the text explicitly excludes the provided candidates.",
            "If multiple candidate document_ids remain possible after applying the provided date and duplicate metadata, return ambiguous rather than choosing one.",
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_link_recovery_prompt(entry: dict, prompt_version: str) -> str:
    payload = {
        "route": "link_normalization_or_target_recovery",
        "target_reference": entry.get("target_reference"),
        "context_block": entry.get("context_block"),
        "candidate_targets": entry.get("candidate_targets", []),
    }
    if prompt_version == "v2":
        payload["resolution_policy"] = [
            "Use only the provided reference text, local context, and candidate_targets.",
            "Do not invent or assume a target outside candidate_targets.",
            "Candidate metadata fields such as target_decision_date_iso, target_decision_date, target_judicate_name, and target_duplicate_canonical_document_id are provided evidence.",
            "If local context gives an explicit cited decision date and candidate_targets include target_decision_date_iso or target_decision_date, use date agreement as a strong disambiguation signal.",
            "A trailing folio or sheet suffix such as '-144' can still be exact_target when the rest of the cited identifier exactly matches one candidate target.",
            "Treat an explicit court identity in context_block as a hard constraint.",
            "If context_block explicitly says the cited act is a decision of Vrchní, Krajský, Městský, or Okresní soud, do not map it to an apex-court candidate from NALUS, NS, or NSS unless the text itself explicitly supports that normalization.",
            "A source mismatch between the cited court context and candidate target is strong evidence for unresolved, not same_proceeding.",
            "If several candidate document_ids are marked as duplicate records for the same identifier and date, choose the target_duplicate_canonical_document_id when the citation supports that duplicate group.",
            "Use same_proceeding only when the cited identifier and candidate clearly share the same proceeding root but not the same exact decision identifier.",
            "If a UOHS candidate target is a merged document that explicitly combines multiple component case bodies, and the cited identifier names only one of those component bodies, prefer same_proceeding over exact_target.",
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_extraction_prompt(entry: dict, prompt_version: str) -> str:
    payload = {
        "route": "extraction_presence_check",
        "candidate_text": entry.get("candidate_text") or entry.get("target_reference"),
        "context_block": entry.get("context_block"),
        "reference_type_hint": entry.get("reference_type_hint"),
    }
    if prompt_version == "v2":
        payload["task_scope"] = (
            "Count only in-scope references to other decisions or case files for this project."
        )
        payload["treat_as_out_of_scope"] = [
            "reporter citations like R 58/2001",
            "collection citations like č. 906/2006 Sb. NSS",
            "the current document's own header or self identifier",
            "page headers, page footers, or repeated page markers",
        ]
        payload["self_identifier_guardrails"] = [
            "Treat a citation as self-identifier only when it functions as the current document's own header, top metadata block, repeated page marker, or other document-identifying label.",
            "Do not treat an identifier as self-identifier just because it appears in a sentence with a court name, a date, or a decision verb such as 'usnesením', 'rozsudkem', 'rozhodl', or 'odmítl'.",
            "If the candidate appears inside running narrative prose describing what another court decided earlier, treat it as an in-scope reference.",
            "Multi-part court citations that combine file numbers from different court levels inside one sentence are still references, not self-identifiers.",
        ]
        payload["reference_type_policy"] = [
            "Court-style docket patterns like '29 Cdo 2303/2013' or '29 ICdo 114/2021-144' are spisova_znacka.",
            "Administrative identifiers introduced by č. j. such as '29-2126/CD/95' are cislo_jednaci.",
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def system_prompt(route: str, prompt_version: str) -> str:
    if prompt_version == "v1":
        if route == "link_disambiguation":
            return (
                "You are a careful resolver of references to Czech legal decisions. "
                "Choose one provided target only if the context clearly supports it. "
                "If multiple provided targets remain plausible, return ambiguous. "
                "Return strict JSON only."
            )
        if route == "link_normalization_or_target_recovery":
            return (
                "You are a careful resolver of references to Czech legal decisions. "
                "Decide whether the best outcome is exact_target, same_proceeding, or unresolved. "
                "Use same_proceeding only when the cited act is clearly part of the same proceeding as a provided target. "
                "Return strict JSON only."
            )
        return (
            "You are a careful reviewer of possible references to Czech legal decisions. "
            "Decide whether the bounded candidate is a real reference occurrence in context. "
            "Return strict JSON only."
        )

    if route == "link_disambiguation":
        return (
            "You are a conservative resolver of references to Czech legal decisions. "
            "Use only explicit information visible in the provided JSON. "
            "Do not use outside legal knowledge, remembered case contents, internet knowledge, or unstated publication metadata. "
            "Treat candidate date and duplicate-canonical metadata as provided evidence when those fields are present. "
            "Treat explicit court identity in the local context as a hard constraint. "
            "Do not normalize a lower-court citation to an apex-court candidate unless the text itself explicitly supports that court jump. "
            "When the local context gives a cited decision date and candidate metadata includes decision dates, use date agreement to disambiguate. "
            "When several candidates are marked as duplicate records for the same identifier and date, choose the provided canonical duplicate target if the duplicate group is supported. "
            "Choose exact_target only when the local text itself safely distinguishes one provided candidate. "
            "If the cited form omits a chamber numeral or other key component and the local text does not explicitly propagate it, prefer ambiguous. "
            "Do not propagate a chamber numeral from one citation to another unless a single chamber-marked citation clearly governs a compact same-style list. "
            "Do not treat a simple conjunction or a looser narrative sequence as safe chamber propagation. "
            "If propagation would imply a chamber numeral that is not present in any provided candidate, prefer ambiguous unless the text expressly rules the candidates out. "
            "If multiple provided document_ids still fit, return ambiguous rather than guessing. "
            "Return strict JSON only."
        )
    if route == "link_normalization_or_target_recovery":
        return (
            "You are a careful resolver of references to Czech legal decisions. "
            "Decide whether the best outcome is exact_target, same_proceeding, or unresolved. "
            "Treat candidate date and duplicate-canonical metadata as provided evidence when those fields are present. "
            "When the local context gives a cited decision date and candidate metadata includes decision dates, use date agreement to disambiguate. "
            "When several candidates are marked as duplicate records for the same identifier and date, choose the provided canonical duplicate target if the duplicate group is supported. "
            "A trailing folio or sheet suffix such as '-144' can still be exact_target when the base identifier otherwise matches one candidate. "
            "Treat explicit court identity in the local context as a hard constraint. "
            "If the citation is explicitly framed as a decision of Vrchní, Krajský, Městský, or Okresní soud, do not choose an apex-court candidate from NALUS, NS, or NSS unless the text itself explicitly supports that mapping. "
            "When the cited court family and candidate source do not match, prefer unresolved rather than same_proceeding. "
            "Use same_proceeding only when the cited act is clearly part of the same proceeding as a provided target. "
            "When a UOHS candidate target is marked as a merged multi-case document and the citation names only one component case body, prefer same_proceeding rather than exact_target. "
            "Use only the provided JSON and do not invent targets outside candidate_targets. "
            "Return strict JSON only."
        )
    return (
        "You are a conservative reviewer of possible references to Czech legal decisions. "
        "Treat reporter citations like R 58/2001, collection citations like Sb. NSS, and the current document's own header/self identifier as not_reference. "
        "Prefer not_reference over is_reference when the candidate looks like a page header, page marker, or self-document identifier. "
        "However, treat the candidate as is_reference when it appears inside running narrative prose that describes what another court decided, even if the sentence also contains a court name, date, or decision verb. "
        "Only use not_reference for self-identifiers when the candidate is functioning as the current document's own identifier rather than as a citation to another decision. "
        "Use is_reference when the candidate is a real reference to another decision or case file in context, even if the target is not linkable in the current corpus. "
        "Return strict JSON only."
    )


def prompt_for_entry(entry: dict, prompt_version: str) -> tuple[str, str]:
    route = str(entry.get("llm_route") or "")
    if route == "link_disambiguation":
        return route, build_link_disambiguation_prompt(entry, prompt_version)
    if route == "link_normalization_or_target_recovery":
        return route, build_link_recovery_prompt(entry, prompt_version)
    if route == "extraction_presence_check":
        return route, build_extraction_prompt(entry, prompt_version)
    raise ValueError(f"Unsupported route: {route}")
