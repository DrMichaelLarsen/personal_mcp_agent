from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.calendar import EventResult
from app.schemas.common import ConfidenceInfo, ReviewItem
from app.schemas.notes import NoteResult
from app.schemas.projects import ProjectRecord
from app.schemas.tasks import TaskResult


class EmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str
    sender: str
    body: str
    received_at: str | None = None
    labels: list[str] = Field(default_factory=list)
    attachments: list["EmailAttachment"] = Field(default_factory=list)


class EmailAttachment(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int = 0
    content_b64: str | None = None


class AttachmentLink(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int
    drive_file_id: str | None = None
    drive_url: str | None = None
    notion_file_url: str | None = None


class EmailTaskItem(BaseModel):
    text: str
    completed: bool = False


class EmailAnalysis(BaseModel):
    email_id: str
    summary: str
    action_items: list[EmailTaskItem] = Field(default_factory=list)
    suggested_title: str
    suggested_status: str | None = None
    suggested_importance: int | None = None
    suggested_contexts: list[str] = Field(default_factory=list)
    suggested_scheduled: str | None = None
    suggested_deadline: str | None = None
    suggested_time_required: int | None = None
    suggested_project_name: str | None = None
    event_start: str | None = None
    event_end: str | None = None
    event_location: str | None = None
    event_description: str | None = None
    outline: list[str] = Field(default_factory=list)
    event_hints: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    confidence: ConfidenceInfo


class EmailStructuredContent(BaseModel):
    summary_markdown: str
    action_items_markdown: str
    original_email_markdown: str
    full_markdown: str


class EmailClassification(BaseModel):
    email_id: str
    category: Literal["task", "note", "event", "task+note", "event+task", "ignore"]
    confidence: ConfidenceInfo


class ExtractedTaskCandidate(BaseModel):
    title: str
    notes: str | None = None
    structured_content: EmailStructuredContent | None = None
    scheduled: str | None = None
    deadline: str | None = None
    contexts: list[str] = Field(default_factory=list)
    importance: int | None = None
    estimated_minutes: int | None = None
    project_name: str | None = None


class ExtractedEventCandidate(BaseModel):
    title: str
    start: str | None = None
    end: str | None = None
    description: str | None = None
    location: str | None = None
    project_name: str | None = None


class ExtractedNoteCandidate(BaseModel):
    title: str
    content: str | None = None
    project_name: str | None = None


class ProcessEmailsInput(BaseModel):
    max_count: int = 10
    preview_only: bool = True
    confidence_threshold: float = 0.8
    mark_processed: bool = False
    query: str | None = None
    include_unread_only: bool = True
    input_emails: list[EmailMessage] = Field(default_factory=list)
    create_project_if_missing: bool = False


class PerEmailProcessResult(BaseModel):
    email_id: str
    classification: EmailClassification
    analysis: EmailAnalysis | None = None
    created_project: ProjectRecord | None = None
    created_task: TaskResult | None = None
    created_note: NoteResult | None = None
    created_event: EventResult | None = None
    review_items: list[ReviewItem] = Field(default_factory=list)


class ProcessEmailsResult(BaseModel):
    preview_only: bool
    processed_count: int
    results: list[PerEmailProcessResult] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
