from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import ConfidenceInfo


class DayPlanInput(BaseModel):
    target_date: str
    workday_start_hour: int | None = None
    workday_end_hour: int | None = None
    max_tasks: int = 5
    include_calendar: bool = True
    include_backlog: bool = True
    priorities_hint: str | None = None
    preview_only: bool = True


class PlannedTask(BaseModel):
    task_id: str
    title: str
    category: str
    recommended_order: int
    estimated_minutes: int | None = None


class TimeBlock(BaseModel):
    title: str
    start: str
    end: str
    task_id: str | None = None
    block_type: str


class DayPlanResult(BaseModel):
    target_date: str
    prioritized_tasks: list[PlannedTask] = Field(default_factory=list)
    suggested_blocks: list[TimeBlock] = Field(default_factory=list)
    deferred_tasks: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    confidence: ConfidenceInfo
    committed_focus_blocks: bool = False
