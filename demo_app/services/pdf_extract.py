"""PDF text extraction helpers for the demo app."""

from __future__ import annotations

import re

import fitz
from src.utils import TextLoader


class PdfExtractionError(RuntimeError):
    """Raised when uploaded PDF content cannot be converted into usable text."""


_EMPTY_LINE_PATTERN = re.compile(r"^\s*$")
_SECTION_MARKER_PATTERN = re.compile(
    r"^(?:[IVXLCDM]+\.?|[A-Z]\.|[0-9]+\.)$",
    re.IGNORECASE,
)
_STRUCTURAL_LINE_PATTERN = re.compile(
    r"^(?:"
    r"[IVXLCDM]+\."
    r"|[0-9]+\."
    r"|[a-z]\)"
    r"|§+\s*\d+[a-z]?"
    r"|čl\.\s*\d+[a-z]?"
    r")$",
    re.IGNORECASE,
)
_STRONG_BREAK_ENDING_PATTERN = re.compile(r"[:.!?]\s*$")
_CASE_ID_HEADER_PATTERN = re.compile(
    r"^\s*(?:č\.\s*j\.|sp\.\s*zn\.|[IVXLCDM]+\.\s*ÚS\b|\d+\s+[A-Za-zÁ-Žá-ž]{1,6}\s+\d+/\d{4})",
    re.IGNORECASE,
)
_COURT_HEADER_PATTERN = re.compile(
    r"\b(?:Nejvyšší\s+soud|Nejvyšší\s+správní\s+soud|Ústavní\s+soud|Krajský\s+soud|Městský\s+soud|Vrchní\s+soud|ČESKÁ\s+REPUBLIKA|pokračování)\b",
    re.IGNORECASE,
)


def _normalize_whitespace(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()


def _should_join_lines(previous: str, current: str) -> bool:
    if not previous or not current:
        return False

    if _SECTION_MARKER_PATTERN.fullmatch(previous) or _STRUCTURAL_LINE_PATTERN.fullmatch(previous):
        return False
    if _SECTION_MARKER_PATTERN.fullmatch(current) or _STRUCTURAL_LINE_PATTERN.fullmatch(current):
        return False

    if previous.endswith("-"):
        return True

    if previous.endswith((",", ";")):
        return True

    if previous.endswith((".", "!", "?", ":")):
        return False

    return True


def _join_lines(previous: str, current: str) -> str:
    if previous.endswith("-"):
        if len(previous) >= 2 and previous[-2].islower() and current[:1].islower():
            return previous[:-1] + current
        return previous + current
    return f"{previous} {current}"


def _merge_block_lines(block_text: str) -> str:
    lines = [_normalize_whitespace(line) for line in block_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    merged: list[str] = []
    current = lines[0]
    for line in lines[1:]:
        if _should_join_lines(current, line):
            current = _join_lines(current, line)
        else:
            merged.append(current)
            current = line
    merged.append(current)
    return "\n".join(merged)


def _blocks_should_merge(
    previous_bounds: tuple[float, float, float, float],
    previous_text: str,
    current_bounds: tuple[float, float, float, float],
    current_text: str,
) -> bool:
    if not previous_text or not current_text:
        return False

    if _SECTION_MARKER_PATTERN.fullmatch(previous_text) or _STRUCTURAL_LINE_PATTERN.fullmatch(previous_text):
        return False
    if _SECTION_MARKER_PATTERN.fullmatch(current_text) or _STRUCTURAL_LINE_PATTERN.fullmatch(current_text):
        return False
    if _STRONG_BREAK_ENDING_PATTERN.search(previous_text):
        return False

    prev_x0, _, _, prev_y1 = previous_bounds
    curr_x0, curr_y0, _, _ = current_bounds
    same_column = abs(curr_x0 - prev_x0) <= 28
    small_vertical_gap = (curr_y0 - prev_y1) <= 22
    return same_column and small_vertical_gap


def _page_text_from_blocks(page: fitz.Page) -> str:
    blocks = page.get_text("blocks", sort=True)
    cleaned_blocks: list[str] = []
    current_cluster_lines: list[str] = []
    current_cluster_bounds: tuple[float, float, float, float] | None = None
    current_cluster_last_text = ""

    def flush_cluster() -> None:
        nonlocal current_cluster_lines, current_cluster_bounds, current_cluster_last_text
        if current_cluster_lines:
            normalized = _merge_block_lines("\n".join(current_cluster_lines))
            if normalized:
                cleaned_blocks.append(normalized)
        current_cluster_lines = []
        current_cluster_bounds = None
        current_cluster_last_text = ""

    for block in blocks:
        bounds = (float(block[0]), float(block[1]), float(block[2]), float(block[3]))
        block_text = _normalize_whitespace(str(block[4] or ""))
        if not block_text:
            continue

        if (
            current_cluster_bounds is not None
            and _blocks_should_merge(current_cluster_bounds, current_cluster_last_text, bounds, block_text)
        ):
            current_cluster_lines.append(block_text)
            current_cluster_bounds = bounds
            current_cluster_last_text = block_text
            continue

        flush_cluster()
        current_cluster_lines = [block_text]
        current_cluster_bounds = bounds
        current_cluster_last_text = block_text

    flush_cluster()
    return "\n\n".join(cleaned_blocks).strip()


def _looks_like_page_header(first_line: str) -> bool:
    line = _normalize_whitespace(first_line)
    if not line:
        return False
    if len(line) > 140:
        return False
    if _CASE_ID_HEADER_PATTERN.search(line):
        return True
    if _COURT_HEADER_PATTERN.search(line):
        return True

    letters = [char for char in line if char.isalpha()]
    uppercase_ratio = (
        sum(1 for char in letters if char.isupper()) / len(letters)
        if letters
        else 0.0
    )
    if len(line) <= 90 and uppercase_ratio >= 0.8:
        return True
    return False


def _remove_probable_page_header(text: str, page_index: int) -> str:
    if page_index <= 1:
        return text
    if "\n" not in text:
        return text
    first_line, remainder = text.split("\n", 1)
    if _looks_like_page_header(first_line):
        return remainder.strip()
    return text


def _apply_demo_document_cleaning(text: str, loader: TextLoader) -> str:
    cleaned = text.replace("\x00", "")
    if loader._needs_cp1250_fix(cleaned):
        cleaned = loader._fix_cp1250_encoding(cleaned)
    cleaned = loader.remove_pokracovani(cleaned)
    cleaned = loader.remove_roman_numerals(cleaned)
    return cleaned


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise PdfExtractionError("Uploaded file is empty.")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # pragma: no cover - library-specific failure path
        raise PdfExtractionError("The uploaded file could not be opened as a PDF.") from exc

    loader = TextLoader()
    page_texts: list[str] = []
    for page_index, page in enumerate(doc, start=1):
        text = _page_text_from_blocks(page)
        text = loader.remove_page_number(text, page_index)
        text = _remove_probable_page_header(text, page_index)
        cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
        if cleaned:
            page_texts.append(cleaned)

    full_text = _apply_demo_document_cleaning("\n\n".join(page_texts).strip(), loader)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text).strip()
    if not full_text:
        raise PdfExtractionError(
            "The PDF did not yield readable text. It may be image-only or require OCR."
        )
    return full_text
