#!/usr/bin/env python3
"""
LLM Alias Validation Script.
Reads global aliases and validates them against the canonical laws Database
using Google's Gemini LLM via the modern google-genai SDK.
"""

import json
import os
import sys

try:
    from google import genai
    from pydantic import BaseModel, Field
    from typing import Optional
except ImportError:
    print("Error: Required packages are not installed.")
    print("Please install them: pip install google-genai pydantic")
    sys.exit(1)


# 1. Define the expected JSON output structure using Pydantic
class AliasValidation(BaseModel):
    reason: Optional[str] = Field(
        default=None,
        description="Step-by-step logical explanation of why this alias fits or fails the criteria. Only required if is_valid is false."
    )
    is_valid: bool = Field(
        description="True if the alias is a valid, specific shorthand for the exact law. False otherwise."
    )


def run_audit(
    global_aliases_path: str,
    canonical_laws_path: str,
    output_path: str,
) -> dict:
    print("--- STARTING LLM ALIAS AUDIT ---")

    # 2. Initialize the Client
    # The genai.Client() automatically looks for the GEMINI_API_KEY environment variable.
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        print("Please set it: export GEMINI_API_KEY='your_key'")
        sys.exit(1)

    try:
        client = genai.Client()
    except Exception as e:
        print(f"Failed to initialize the Gemini client: {e}")
        sys.exit(1)

    # Load dictionaries
    with open(global_aliases_path, "r", encoding="utf-8") as f:
        global_aliases = json.load(f)

    with open(canonical_laws_path, "r", encoding="utf-8") as f:
        canonical_laws = json.load(f)

    # 3. Choose model (override via GEMINI_MODEL if needed)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    print(f"Using model: {model_name}")

    results = {}
    valid_count = 0
    invalid_count = 0

    print(f"Evaluating {len(global_aliases)} global aliases...\n")

    for alias, law_id in list(global_aliases.items()):
        law_names = canonical_laws.get(law_id)

        if not law_names:
            print(
                f"[SKIPPED] '{alias}': Law ID {law_id} not found in canonical_laws.json."
            )
            results[alias] = {
                "law_id": law_id,
                "is_valid": "UNKNOWN",
                "reason": "ID not found in canonical DB",
            }
            continue

        law_name = law_names[0] if isinstance(law_names, list) else law_names

        # 4. LLM Prompt (Removed formatting instructions since Pydantic handles it)
        prompt = f"""
        You are an expert legal data engineer and a scholar of Czech law. 
        Your task is to sanitize a global dictionary of legal aliases. You must evaluate whether a specific abbreviation, acronym, or alias is a valid, static identifier for the given official Czech law name.

        You must be STRICT in rejecting bad data (noise), but PRAGMATIC about how lawyers actually write (colloquialisms).

        REJECT the alias ONLY if it falls into one of these categories:
        1. Entity/Subject Hijacking: The alias refers to a person, company, institution, or general legal topic rather than the law itself (e.g., 'Sdružení', 'Úřad', 'uchazeč', 'soutěž', 'smlouva', 'veřejná zakázka').
        2. Temporal/State Modifiers: The alias describes the temporal state or version of a law rather than its name (e.g., 'zákon po novele', 'v platném znění', 'nový zákon', 'zákon v úplném znění', 'novela ZZVZ').
        3. Generic Terms: The alias is too broad and could apply to any law (e.g., 'zákon', 'vyhláška', 'předpis', 'nařízení').

        APPROVE the alias if it is a specific, established, or logical shorthand for that exact law.
        CRITICAL RULE: You MUST APPROVE shortened or colloquial versions of long law names. For example, approve 'zákon o Vězeňské službě' for 'Zákon o Vězeňské službě a justiční stráži', or 'silniční zákon' for 'Zákon o provozu na pozemních komunikacích'. Do not reject an alias simply because it omits a portion of the full official title.

        Input Data:
        Alias: "{alias}"
        Official Law Name: "{law_name}"
        """

        try:
            # 5. Call API with Structured Output config
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": AliasValidation,
                },
            )

            # Since we used response_schema, google-genai automatically parses it into response.parsed
            is_valid = response.parsed.is_valid
            reason = response.parsed.reason or "No reason provided"

            # Save result
            results[alias] = {
                "law_id": law_id,
                "law_name": law_name,
                "is_valid": is_valid,
                "reason": reason,
            }

            if is_valid:
                print(f"✅ [OK] '{alias}' -> {law_id}")
                valid_count += 1
            else:
                reason_str = f" | {reason}" if reason else ""
                print(f"❌ [BAD] '{alias}' -> {law_id}{reason_str}")
                invalid_count += 1

        except Exception as e:
            print(f"⚠️ [ERROR] Failed to process '{alias}': {e}")
            results[alias] = {"law_id": law_id, "is_valid": "ERROR", "reason": str(e)}

    # 6. Save audited dictionary
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print("\n--- AUDIT COMPLETE ---")
    print(f"Valid Aliases: {valid_count}")
    print(f"Invalid Aliases: {invalid_count}")
    print(f"Results saved to: {output_path}")

    return results


if __name__ == "__main__":
    run_audit(
        global_aliases_path="data/dicts/global_aliases.json",
        canonical_laws_path="data/dicts/canonical_laws.json",
        output_path="data/dicts/audited_aliases.json",
    )
