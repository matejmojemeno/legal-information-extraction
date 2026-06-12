#!/usr/bin/env python3
"""
LLM Conflict Resolution Script.
Reads conflict_aliases.json (where an alias mapped to multiple Sb. IDs)
and asks the LLM to pick the single most appropriate Law ID, or reject it.
"""

import json
import os
import sys

try:
    from google import genai
    from pydantic import BaseModel, Field
    from typing import Literal
except ImportError:
    print("Error: Required packages are not installed.")
    print("Please install them: pip install google-genai pydantic")
    sys.exit(1)


# 1. Define the expected JSON output structure using Pydantic
class ConflictResolution(BaseModel):
    classification: Literal["single", "ambiguous_valid", "reject"] = Field(
        description=(
            "single = exactly one candidate is the correct canonical alias target; "
            "ambiguous_valid = the alias is valid but legitimately maps to multiple "
            "candidate law versions/acts; reject = generic/noisy/not safe."
        )
    )
    reason: str = Field(
        description=(
            "Logical explanation of why you chose one candidate, preserved several "
            "candidates, or rejected the alias."
        )
    )
    resolved_law_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Candidate Law IDs selected by the model. Must be a subset of the "
            "provided candidates. Use one ID for classification=single, multiple "
            "IDs for classification=ambiguous_valid, and an empty list for reject."
        ),
    )


def resolve_conflicts(
    conflict_aliases_path: str,
    canonical_laws_path: str,
    output_path: str,
) -> dict:
    print("--- STARTING LLM CONFLICT RESOLUTION ---")

    # 2. Initialize the Client
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)

    try:
        client = genai.Client()
    except Exception as e:
        print(f"Failed to initialize the Gemini client: {e}")
        sys.exit(1)

    # Load dictionaries
    if not os.path.exists(conflict_aliases_path):
        print(f"No conflict file found at {conflict_aliases_path}. Nothing to do.")
        return {}

    with open(conflict_aliases_path, "r", encoding="utf-8") as f:
        conflict_aliases = json.load(f)

    with open(canonical_laws_path, "r", encoding="utf-8") as f:
        canonical_laws = json.load(f)

    # Load previously resolved to avoid re-running everything
    resolved_results = {}
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            try:
                resolved_results = json.load(f)
            except json.JSONDecodeError:
                pass

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    print(f"Using model: {model_name}")

    resolved_count = 0
    rejected_count = 0

    print(f"Evaluating {len(conflict_aliases)} conflicting aliases...\n")

    for alias, id_counts in conflict_aliases.items():
        if alias in resolved_results:
            print(f"⏭️  [SKIPPED] '{alias}' (already processed)")
            continue

        # Prepare candidates with their official names
        candidates_info = []
        for law_id, count in id_counts.items():
            law_names = canonical_laws.get(law_id, ["Unknown Law"])
            official_name = law_names[0] if isinstance(law_names, list) else law_names
            candidates_info.append(f"- ID: {law_id} (Frequency in text: {count})\n  Official Name: {official_name}")

        candidates_text = "\n".join(candidates_info)

        # 4. LLM Prompt
        prompt = f"""
        You are an expert legal data engineer and a scholar of Czech law. 
        You are looking at an alias/abbreviation extracted from Czech legal texts.
        Because of older laws, historical references, amendment contexts, or OCR noise,
        this alias mapped to multiple Official Law IDs.

        Your task is to decide which of these outcomes is correct:
        1. `single`: the alias safely and universally identifies exactly one candidate law.
        2. `ambiguous_valid`: the alias is still valid, but it legitimately refers to multiple candidate laws depending on time or context. This is especially important for historical law versions.
        3. `reject`: the alias is generic, noisy, or not safe to keep.

        CRITICAL RULE:
        If an alias correctly refers to multiple versions of the same law tradition
        (for example old vs new civil code), DO NOT force a single modern winner.
        Preserve all correct candidates with `classification = ambiguous_valid`.

        Example:
        - Alias: "občanský zákoník"
        - Candidates: 40/1964 Sb., 89/2012 Sb.
        - Correct outcome: `ambiguous_valid` with both IDs, because both are real,
          context-dependent references used in Czech legal texts.

        Reject only when the alias is not a safe law alias at all.

        Input Data:
        Alias/Abbreviation: "{alias}"

        Candidates (with how many times they appeared with this alias):
        {candidates_text}
        """

        try:
            # 5. Call API
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ConflictResolution,
                    "temperature": 0.1,
                },
            )

            classification = response.parsed.classification
            resolved_law_ids = [law_id for law_id in response.parsed.resolved_law_ids if law_id in id_counts]
            reason = response.parsed.reason

            if classification == "single":
                resolved_law_ids = resolved_law_ids[:1]
            elif classification == "reject":
                resolved_law_ids = []

            # Save result
            resolved_results[alias] = {
                "classification": classification,
                "resolved_law_ids": resolved_law_ids,
                "resolved_law_id": resolved_law_ids[0] if len(resolved_law_ids) == 1 else None,
                "reason": reason,
                "candidates": id_counts
            }

            if classification == "single" and resolved_law_ids:
                print(f"✅ [RESOLVED] '{alias}' -> {resolved_law_ids[0]}")
                resolved_count += 1
            elif classification == "ambiguous_valid" and resolved_law_ids:
                print(f"🟡 [AMBIGUOUS-VALID] '{alias}' -> {resolved_law_ids}")
            else:
                print(f"❌ [REJECTED] '{alias}' | {reason}")
                rejected_count += 1

        except Exception as e:
            print(f"⚠️ [ERROR] Failed to process '{alias}': {e}")
            resolved_results[alias] = {
                "classification": "reject",
                "resolved_law_ids": [],
                "resolved_law_id": None,
                "reason": f"ERROR: {str(e)}",
                "candidates": id_counts,
            }

        # Checkpoint after every request
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(resolved_results, f, ensure_ascii=False, indent=4)

    print("\n--- RESOLUTION COMPLETE ---")
    print(f"Successfully Resolved: {resolved_count}")
    print(f"Rejected as Ambiguous: {rejected_count}")
    print(f"Results saved to: {output_path}")

    return resolved_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Resolve conflicting aliases using LLM.")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Use test mode (swap GEMINI_API_KEY <- GEMINI_TEST_API_KEY).",
    )
    args = parser.parse_args()

    # Apply test config if requested (mirroring 02_audit_aliases logic)
    if args.test:
        os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_TEST_API_KEY", "")

    resolve_conflicts(
        conflict_aliases_path="data/dicts/conflict_aliases.json",
        canonical_laws_path="data/dicts/canonical_laws.json",
        output_path="data/dicts/resolved_conflicts.json",
    )
