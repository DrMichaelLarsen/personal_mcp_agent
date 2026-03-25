from __future__ import annotations

from typing import TypedDict

from app.schemas.common import ReviewItem
from app.schemas.email import (
    EmailAnalysis,
    EmailClassification,
    EmailMessage,
    ExtractedEventCandidate,
    ExtractedNoteCandidate,
    ExtractedTaskCandidate,
    PerEmailProcessResult,
)
from app.schemas.projects import ProjectMatchResult


class ProcessEmailsState(TypedDict, total=False):
    preview_only: bool
    confidence_threshold: float
    emails: list[EmailMessage]
    analyses: dict[str, EmailAnalysis]
    classifications: list[EmailClassification]
    task_candidates: dict[str, ExtractedTaskCandidate]
    note_candidates: dict[str, ExtractedNoteCandidate]
    event_candidates: dict[str, ExtractedEventCandidate]
    project_matches: dict[str, ProjectMatchResult]
    resolved_contexts: dict[str, list[str]]
    context_review_items: dict[str, list[ReviewItem]]
    results: list[PerEmailProcessResult]
