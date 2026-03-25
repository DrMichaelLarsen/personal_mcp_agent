from __future__ import annotations

from typing import Literal

from app.schemas.common import ConfidenceInfo


def label_for_score(score: float) -> Literal["low", "medium", "high"]:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def build_confidence(score: float, rationale: str, review_required: bool | None = None) -> ConfidenceInfo:
    bounded = round(max(0.0, min(1.0, score)), 3)
    return ConfidenceInfo(
        confidence_score=bounded,
        confidence_label=label_for_score(bounded),
        rationale=rationale,
        review_required=review_required if review_required is not None else bounded < 0.85,
    )
