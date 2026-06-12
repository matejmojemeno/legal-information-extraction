"""Lightweight view models for the demo app."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DemoReferenceItem:
    id: str
    kind: str
    subtype: str
    start: int
    end: int
    surface: str
    status: str
    badge: str
    color_class: str
    title: str
    detail: str
    resolved_label: str | None = None
    target_source: str | None = None
    target_document_id: str | None = None
    target_url: str | None = None
    external_url: str | None = None
    ai_assisted: bool = False
