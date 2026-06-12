#!/usr/bin/env python3
"""Search PDFs for a phrase with whitespace-normalized matching.

Useful for locating a quoted snippet when the source PDF is unknown and line
breaks vary between extraction runs.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _extract_text(path: Path) -> str:
    try:
        import fitz  # type: ignore

        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser(description="Search PDFs for a phrase.")
    parser.add_argument("phrase", help="Phrase to search for.")
    parser.add_argument(
        "--root",
        default="data/sample_pdfs",
        help="Directory to scan recursively. Defaults to ~/Downloads.",
    )
    parser.add_argument(
        "--glob",
        default="**/*.pdf",
        help="Glob pattern under --root. Defaults to recursive PDF search.",
    )
    parser.add_argument(
        "--snippet",
        type=int,
        default=140,
        help="Characters of surrounding normalized context to print.",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    phrase_norm = _normalize(args.phrase)
    pdf_paths = sorted(root.glob(args.glob))

    print(f"root={root}")
    print(f"pdf_count={len(pdf_paths)}")

    found = 0
    for path in pdf_paths:
        text = _extract_text(path)
        if not text:
            continue
        normalized = _normalize(text)
        idx = normalized.find(phrase_norm)
        if idx == -1:
            continue
        found += 1
        start = max(0, idx - args.snippet)
        end = min(len(normalized), idx + len(phrase_norm) + args.snippet)
        print(f"\nMATCH {path}")
        print(normalized[start:end])

    if found == 0:
        print("\nNo matches found.")


if __name__ == "__main__":
    main()
