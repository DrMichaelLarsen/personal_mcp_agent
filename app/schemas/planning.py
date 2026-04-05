from __future__ import annotations

from typing import Literal

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


class DayScheduleBuildInput(BaseModel):
    target_date: str
    day_start: str | None = None
    day_end: str | None = None
    day_start_hour: int | None = None
    day_end_hour: int | None = None
    buffer_minutes: int | None = None
    preserve_existing_scheduled: bool = True
    include_due_tomorrow: bool = True
    max_candidates: int = 25
    preview_only: bool = True


class ScheduledItem(BaseModel):
    item_id: str
    item_type: Literal["task", "checklist_item"]
    title: str
    start: str
    end: str
    estimated_minutes: int
    source: Literal["new", "existing"]
    deadline: str | None = None
    score: float | None = None


class DayScheduleBuildResult(BaseModel):
    target_date: str
    scheduled_items: list[ScheduledItem] = Field(default_factory=list)
    unscheduled_items: list[dict] = Field(default_factory=list)
    busy_blocks: list[TimeBlock] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    confidence: ConfidenceInfo
    preview_only: bool = True
    cleared_existing_count: int = 0


class ScheduleTaskAtTimeInput(BaseModel):
    task_id: str | None = None
    task_title: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    start: str
    duration_minutes: int
    deadline: str | None = None
    preview_only: bool = True
