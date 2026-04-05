from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EventRecord(BaseModel):
    id: str
    title: str
    done: bool = False
    start: str
    end: str
    location: str | None = None
    notes: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
