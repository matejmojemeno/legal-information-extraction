"""Span segmentation for inline rendering of extracted references."""

from __future__ import annotations

from demo_app.models import DemoReferenceItem


def build_render_segments(text: str, items: list[DemoReferenceItem]) -> list[dict[str, object]]:
    accepted: list[DemoReferenceItem] = []
    last_end = 0
    for item in sorted(items, key=lambda current: (current.start, -(current.end - current.start), current.kind)):
        start = max(0, min(item.start, len(text)))
        end = max(start, min(item.end, len(text)))
        if end <= start:
            continue
        if start < last_end:
            continue
        accepted.append(item)
        last_end = end

    segments: list[dict[str, object]] = []
    cursor = 0
    for item in accepted:
        if item.start > cursor:
            segments.append({"kind": "text", "text": text[cursor:item.start]})
        segments.append(
            {
                "kind": "reference",
                "text": text[item.start:item.end],
                "item": item,
            }
        )
        cursor = item.end
    if cursor < len(text):
        segments.append({"kind": "text", "text": text[cursor:]})
    return segments
