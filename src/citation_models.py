"""
Citation output contracts used by extraction pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CitationOccurrence:
    """
    Occurrence-level citation extraction result.

    This is the citation-first output object. Anomaly queues are derived from
    these occurrences instead of being the primary output.
    """

    citation_text: str
    citation_type: str
    normalized_start: int
    normalized_end: int
    raw_start: int
    raw_end: int
    parsed_detail: dict[str, Any] = field(default_factory=dict)
    resolved_law_id: str | None = None
    predicted_classification: str = "czech_unresolved"
    resolver_stage: str = "unresolved"
    confidence: float = 0.0
    quality_flag: bool = True
    quality_reason: str | None = "unresolved"
    context: str = ""
    candidate_law_ids: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """Backward-compatible alias for older internal callers."""
        return self.quality_flag

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_text": self.citation_text,
            "citation_type": self.citation_type,
            "normalized_span": {
                "start": self.normalized_start,
                "end": self.normalized_end,
            },
            "raw_span": {
                "start": self.raw_start,
                "end": self.raw_end,
            },
            "parsed_detail": self.parsed_detail,
            "resolved_law_id": self.resolved_law_id,
            "predicted_classification": self.predicted_classification,
            "resolver_stage": self.resolver_stage,
            "confidence": self.confidence,
            "quality_flag": self.quality_flag,
            "quality_reason": self.quality_reason,
            "context": self.context,
            "candidate_law_ids": self.candidate_law_ids,
        }
