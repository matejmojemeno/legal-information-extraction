"""
Non-destructive text normalization for citation matching.

This module builds a normalized matching view while preserving a char-level
mapping back to the raw text so extracted offsets remain auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

_ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\ufeff"}


@dataclass(slots=True)
class NormalizedDocument:
    """Raw text + normalized matching text + span mapping."""

    raw_text: str
    normalized_text: str
    char_starts: list[int]
    char_ends: list[int]

    def normalized_span_to_raw(self, start: int, end: int) -> tuple[int, int]:
        """
        Map a [start, end) span from normalized text back to raw text offsets.
        """
        n = len(self.normalized_text)
        if not self.char_starts or n == 0:
            return (0, 0)

        start = max(0, min(start, n))
        end = max(start, min(end, n))

        if start >= n:
            last = self.char_ends[-1]
            return (last, last)

        raw_start = self.char_starts[start]
        raw_end = self.char_ends[end - 1] if end > start else raw_start
        return (raw_start, raw_end)


def _is_letter(ch: str) -> bool:
    return ch.isalpha()


def _next_non_space_index(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _consume_linebreak_whitespace(text: str, index: int) -> tuple[int, bool]:
    """
    Consume whitespace from index and report whether at least one line break was seen.
    """
    seen_linebreak = False
    i = index
    while i < len(text):
        ch = text[i]
        if ch == "\r":
            seen_linebreak = True
            if i + 1 < len(text) and text[i + 1] == "\n":
                i += 2
            else:
                i += 1
            continue
        if ch == "\n":
            seen_linebreak = True
            i += 1
            continue
        if ch in " \t":
            i += 1
            continue
        break
    return i, seen_linebreak


def normalize_for_matching(raw_text: str) -> NormalizedDocument:
    """
    Build normalized matching view with auditable mapping to raw offsets.

    Applied transformations:
    - remove zero-width characters
    - convert NBSP to regular space
    - normalize CR/LF variants
    - safe de-hyphenation of line-wrapped words
    - collapse whitespace runs to single spaces
    """
    out_chars: list[str] = []
    char_starts: list[int] = []
    char_ends: list[int] = []

    i = 0
    last_was_space = False
    raw_len = len(raw_text)

    while i < raw_len:
        ch = raw_text[i]

        if ch in _ZERO_WIDTH_CHARS:
            i += 1
            continue

        # Safe de-hyphenation: "obec-\nného" -> "obecného"
        if ch == "-" and out_chars and _is_letter(out_chars[-1]):
            j, seen_linebreak = _consume_linebreak_whitespace(raw_text, i + 1)
            if seen_linebreak:
                j = _next_non_space_index(raw_text, j)
                if j < raw_len and _is_letter(raw_text[j]):
                    i = j
                    continue

        # Normalize newline forms.
        raw_span_end = i + 1
        if ch == "\r":
            if i + 1 < raw_len and raw_text[i + 1] == "\n":
                raw_span_end = i + 2
            ch = "\n"

        if ch == "\u00a0":
            ch = " "

        # Normalize all whitespace into single spaces.
        if ch.isspace():
            j = raw_span_end
            while j < raw_len:
                nxt = raw_text[j]
                if nxt in _ZERO_WIDTH_CHARS:
                    j += 1
                    continue
                if nxt == "\u00a0" or nxt.isspace():
                    j += 1
                    continue
                break
            if not last_was_space and out_chars:
                out_chars.append(" ")
                char_starts.append(i)
                char_ends.append(j)
                last_was_space = True
            i = j
            continue

        out_chars.append(ch)
        char_starts.append(i)
        char_ends.append(raw_span_end)
        last_was_space = False
        i = raw_span_end

    normalized_text = "".join(out_chars).strip()

    # If strip removed leading/trailing spaces, adjust mappings accordingly.
    if normalized_text != "".join(out_chars):
        first_non_space = 0
        while first_non_space < len(out_chars) and out_chars[first_non_space] == " ":
            first_non_space += 1

        last_non_space = len(out_chars) - 1
        while last_non_space >= 0 and out_chars[last_non_space] == " ":
            last_non_space -= 1

        if last_non_space >= first_non_space:
            char_starts = char_starts[first_non_space : last_non_space + 1]
            char_ends = char_ends[first_non_space : last_non_space + 1]
        else:
            char_starts = []
            char_ends = []

    return NormalizedDocument(
        raw_text=raw_text,
        normalized_text=normalized_text,
        char_starts=char_starts,
        char_ends=char_ends,
    )
