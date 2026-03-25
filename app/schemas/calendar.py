from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ConfidenceInfo


class EventCreateInput(BaseModel):
    title: str
    start: str
    end: str
    description: str | None = None
    location: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    email_id: str | None = None
    dry_run: bool = True


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: str
    end: str
    description: str | None = None
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventResult(BaseModel):
    created: bool
    dry_run: bool
    event: CalendarEvent
    confidence: ConfidenceInfo


class CalendarDayResult(BaseModel):
    date: str
    events: list[CalendarEvent] = Field(default_factory=list)
