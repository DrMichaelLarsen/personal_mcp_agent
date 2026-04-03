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
    ai_cost: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class NoteResult(BaseModel):
    created: bool = False
    note: NoteRecord | None = None
    confidence: ConfidenceInfo
    review_items: list[ReviewItem] = Field(default_factory=list)
    message: str = ""


class NoteUpdateInput(BaseModel):
    note_id: str
    project_id: str | None = None
    area_id: str | None = None
    tags: list[str] | None = None
    ai_cost: float | None = None
    additional_properties: dict[str, Any] | None = None


class ProcessNotesInboxInput(BaseModel):
    max_count: int = 50
    preview_only: bool = True
    processed_tag: str = "Inbox Processed"


class NotesInboxItemResult(BaseModel):
    note_id: str
    updated: bool = False
    changed_fields: dict = Field(default_factory=dict)
    review_items: list[ReviewItem] = Field(default_factory=list)


class ProcessNotesInboxResult(BaseModel):
    preview_only: bool
    processed_count: int
    updated_count: int
    results: list[NotesInboxItemResult] = Field(default_factory=list)