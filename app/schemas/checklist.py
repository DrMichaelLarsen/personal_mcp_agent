from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChecklistItemRecord(BaseModel):
    id: str
    title: str
    status: str | None = None
    done: bool = False
    scheduled: str | None = None
    deadline: str | None = None
    estimated_minutes: int | None = None
    score: float | None = None
    schedule_filter: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
