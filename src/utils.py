"""
Shared utilities for text extraction and cleaning.

- TextLoader: Full PDF text extraction with page-level and document-level cleaning
- extract_text_from_pdf_url: Fetch PDF from URL, clean via TextLoader, return text
- extract_text_from_nalus_url: Fetch NALUS HTML, clean text, return text
- build_canonical_law_dict: Parses the e-Sbírka JSON into a clean lookup
"""

import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# Characters that signal Windows-1250 bytes misinterpreted as Latin-1.
# Common in older Czech court PDFs.
_CP1250_MARKERS = frozenset("øìèùò")


class TextLoader:
    """
    Extracts and cleans text from Czech court decision PDFs.

    Handles:
    - Page number removal
    - Header removal (first line per page)
    - Sequential footer detection and removal
    - Section extraction (Odůvodnění → Poučení)
    - "pokračování" and Roman numeral removal
    - CP1250 encoding fix for corrupted older PDFs
    - Null byte removal
    """

    MAX_FOOTER_GAP = 600

    def load_text(self, doc) -> str:
        """
        Extract and clean text from a fitz.Document object.

        Args:
            doc: An open fitz.Document (from fitz.open()).

        Returns:
            Cleaned text string.
        """
        text = ""
        self.last_footer_num = 0

        for i, page in enumerate(doc, start=1):
            page_text = page.get_text("text")
            page_text = self.remove_page_number(page_text, i)
            page_text = self.remove_page_header(page_text)
            page_text = self.remove_footer_and_crop(page_text)
            text += "\n" + page_text

        text = self.clean_text(text)
        return text

    def clean_text(self, text: str) -> str:
        """
        Apply document-level cleaning to extracted text.

        Can be used standalone for non-PDF sources (e.g. NALUS HTML).
        """
        text = self.extract_relevant_section(text)
        text = self.remove_pokracovani(text)
        text = self.remove_roman_numerals(text)

        # Clean up encoding issues
        text = text.replace("\x00", "")
        if self._needs_cp1250_fix(text):
            text = self._fix_cp1250_encoding(text)
            logger.info("Fixed CP1250 encoding")

        return text

    @staticmethod
    def _needs_cp1250_fix(text: str) -> bool:
        """Check if text shows signs of Windows-1250 → Latin-1 corruption."""
        sample = text[:2000]
        return sum(1 for c in sample if c in _CP1250_MARKERS) >= 3

    @staticmethod
    def _fix_cp1250_encoding(text: str) -> str:
        """Re-encode text from Latin-1 back to Windows-1250, char by char."""
        result = []
        for char in text:
            try:
                result.append(char.encode("latin-1").decode("windows-1250"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                result.append(char)
        return "".join(result)

    def remove_footer_and_crop(self, text):
        """
        Scan for a sequence of footers (e.g., 1, 2, 3) at the bottom of the page.
        Only crops if the sequence matches the global counter AND ends at the bottom.
        """
        pattern = r"^\s*(\d+)(?!\.)\s+.*$"
        all_matches = list(re.finditer(pattern, text, flags=re.MULTILINE))

        if not all_matches:
            return text

        target_start_num = self.last_footer_num + 1
        candidates = [m for m in all_matches if int(m.group(1)) == target_start_num]

        for start_match in candidates:
            chain = [start_match]
            next_expected = target_start_num + 1

            start_index = all_matches.index(start_match)
            for potential_next in all_matches[start_index + 1:]:
                found_num = int(potential_next.group(1))
                if found_num == next_expected:
                    prev_match = chain[-1]
                    gap = potential_next.start() - prev_match.end()
                    if gap < self.MAX_FOOTER_GAP:
                        chain.append(potential_next)
                        next_expected += 1
                    else:
                        break

            last_match = chain[-1]
            chars_remaining = len(text) - last_match.end()

            if chars_remaining < self.MAX_FOOTER_GAP:
                self.last_footer_num = int(last_match.group(1))
                cut_index = chain[0].start()
                return text[:cut_index].rstrip()

        return text

    def remove_page_number(self, text, page_number):
        """Remove a line containing only the page number."""
        pattern = r"^\s*" + re.escape(str(page_number)) + r"\s*$"
        clean_text = re.sub(pattern, "", text, flags=re.MULTILINE)
        return re.sub(r"\n+", "\n", clean_text).strip()

    def remove_page_header(self, text):
        """Remove the first line (header) after page number removal."""
        return text[text.find("\n"):].strip() if "\n" in text else text

    def extract_relevant_section(self, text):
        """Extract text between Odůvodnění and Poučení."""
        start_pattern = r"O\s*d\s*ů\s*v\s*o\s*d\s*n\s*ě\s*n\s*í"
        end_pattern = r"P\s*o\s*u\s*č\s*e\s*n\s*í"

        start_match = re.search(start_pattern, text, flags=re.IGNORECASE)
        end_matches = list(re.finditer(end_pattern, text, flags=re.IGNORECASE))

        start_idx = 0
        end_idx = len(text)

        if start_match:
            start_idx = start_match.end()
        if end_matches:
            end_idx = end_matches[-1].start()

        if start_idx >= end_idx:
            return text
        return text[start_idx:end_idx]

    def remove_pokracovani(self, text):
        pattern = r"^\s*pokračování\s*$"
        return re.sub(pattern, "", text, flags=re.MULTILINE)

    def remove_roman_numerals(self, text):
        pattern = r"(?:^|\n)\s*[IXVLCDM]+\.(?=\s|$)"
        return re.sub(pattern, "", text, flags=re.MULTILINE)


# Shared instance — TextLoader is stateless between documents
_loader = TextLoader()


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract and clean text from a local PDF file.
    """
    import fitz

    with fitz.open(pdf_path) as doc:
        return _loader.load_text(doc)


def extract_text_from_pdf_url(url: str) -> str:
    """
    Fetch a PDF from a URL, extract and clean text in memory (no file saved).
    """
    import fitz
    import requests

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    with fitz.open(stream=resp.content, filetype="pdf") as doc:
        return _loader.load_text(doc)


def extract_text_from_nalus_url(url: str) -> str:
    """
    Fetch NALUS HTML page and extract clean decision text.

    Applies document-level cleaning (section extraction, encoding fixes)
    but skips page-level cleaning since there are no PDF pages.
    """
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")

    # Primary: main content table cell
    content_td = soup.find("td", class_="DocContent")
    if content_td:
        raw_text = content_td.get_text(separator="\n", strip=True)
    else:
        # Fallback: hidden input field
        hidden_input = soup.find("input", id="docContentHidden")
        if hidden_input and hidden_input.has_attr("value"):
            raw_text = hidden_input["value"].replace(r"\par", "\n").strip()
        else:
            return ""

    # Apply document-level cleaning (section extraction, encoding fixes)
    return _loader.clean_text(raw_text)


def build_canonical_law_dict(
    input_json: str = "data/dicts/002PravniAkt.json",
    output_json: str = "data/dicts/canonical_laws.json",
) -> dict[str, list[str]]:
    """
    Parse the e-Sbírka open-data JSON and build a canonical law dictionary.

    Each entry maps a citation (e.g. "89/2012 Sb.") to a list whose first
    element is the official name. Additional aliases can be appended later.
    """
    print(f"Processing: {input_json}")
    start_time = time.time()

    law_dict = {}
    count = 0
    skipped = 0

    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("položky", [])

    for item in items:
        if item.get("typ") == "právní-akt":
            citation = item.get("akt-citace")
            name = item.get("akt-název-vyhlášený")

            if citation and name:
                law_dict[citation] = [name]
                count += 1
            else:
                skipped += 1

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as out_f:
        json.dump(law_dict, out_f, ensure_ascii=False, indent=2)

    elapsed = round(time.time() - start_time, 2)

    print(f"\n--- DONE ---")
    print(f"Time: {elapsed}s")
    print(f"Laws saved: {count}")
    print(f"Skipped (incomplete): {skipped}")
    print(f"Output: {output_json}")

    return law_dict
