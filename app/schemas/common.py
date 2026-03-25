from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfidenceInfo(BaseModel):
    confidence_score: float
    confidence_label: Literal["low", "medium", "high"]
    rationale: str
    review_required: bool = False


class ReviewItem(BaseModel):
    item_type: str
    reason: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    confidence: ConfidenceInfo


class OperationSummary(BaseModel):
    created: bool = False
    updated: bool = False
    preview_only: bool = False
    message: str = ""
