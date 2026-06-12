"""Debug helper for inspecting demo-app extraction output outside the web UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo_app.services.pipeline_runner import run_demo_pipeline
from dataclasses import asdict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the demo pipeline on a text snippet or PDF and print the raw "
            "law/document extraction output for debugging."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="Inline text to inspect.")
    group.add_argument("--pdf", help="Path to a PDF file to inspect.")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output for easier manual inspection.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.text:
        text = args.text
        document_name = "debug.txt"
    else:
        from demo_app.services.pdf_extract import extract_text_from_pdf_bytes

        pdf_path = Path(args.pdf).expanduser().resolve()
        text = extract_text_from_pdf_bytes(pdf_path.read_bytes())
        document_name = pdf_path.name

    result = run_demo_pipeline(text=text, document_name=document_name)
    payload = {
        "document_name": result["document_name"],
        "stats": result["stats"],
        "law_items": [asdict(item) for item in result["law_items"]],
        "document_items": [asdict(item) for item in result["document_items"]],
        "debug": result["debug"],
    }
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
