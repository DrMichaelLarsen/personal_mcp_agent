from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ConfidenceInfo, ReviewItem


class NoteCreateInput(BaseModel):
    title: str
    content: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    source_url: str | None = None
    source_email_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class NoteRecord(BaseModel):
    id: str
    title: str
    content: str | None = None
    project_id: str | None = None
    area_id: str | None = None
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class NoteResult(BaseModel):
    created: bool = False
    note: NoteRecord | None = None
    confidence: ConfidenceInfo
    review_items: list[ReviewItem] = Field(default_factory=list)
    message: str = ""