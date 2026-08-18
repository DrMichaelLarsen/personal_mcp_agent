from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CalendarSyncMode = Literal["google_authoritative", "two_way"]


class CalendarSyncInput(BaseModel):
    start: str | None = None
    end: str | None = None
    days_ahead: int | None = Field(default=None, ge=1, le=366)
    calendar_ids: list[str] | None = None
    mode: CalendarSyncMode | None = None
    dry_run: bool = False


class CalendarSyncItem(BaseModel):
    action: str
    event_id: str | None = None
    calendar_id: str | None = None
    notion_page_id: str | None = None
    notion_url: str | None = None
    message: str | None = None


class CalendarSyncResult(BaseModel):
    start: str
    end: str
    mode: CalendarSyncMode
    dry_run: bool
    google_events_seen: int = 0
    notion_pages_seen: int = 0
    notion_pages_created: int = 0
    notion_pages_updated: int = 0
    google_events_created: int = 0
    google_events_updated: int = 0
    skipped: int = 0
    items: list[CalendarSyncItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
