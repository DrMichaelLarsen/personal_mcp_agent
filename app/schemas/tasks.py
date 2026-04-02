from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ConfidenceInfo, ReviewItem


class TaskCreateInput(BaseModel):
    title: str
    project_id: str | None = None
    project_name: str | None = None
    contexts: list[str] = Field(default_factory=list)
    scheduled: str | None = None
    deadline: str | None = None
    estimated_minutes: int | None = None
    importance: int | None = None
    assigned_to: list[str] = Field(default_factory=list)
    source_url: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str | None = None
    phone: str | None = None
    budget: float | None = None
    goal_id: str | None = None
    parent_id: str | None = None
    dependency_of_ids: list[str] = Field(default_factory=list)
    depends_on_ids: list[str] = Field(default_factory=list)
    ai_cost: float | None = None
    ai_cost_summary: str | None = None


class TaskUpdateInput(BaseModel):
    task_id: str
    title: str | None = None
    contexts: list[str] | None = None
    scheduled: str | None = None
    deadline: str | None = None
    estimated_minutes: int | None = None
    importance: int | None = None
    assigned_to: list[str] | None = None
    notes: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    project_id: str | None = None
    phone: str | None = None
    budget: float | None = None
    goal_id: str | None = None
    parent_id: str | None = None
    dependency_of_ids: list[str] | None = None
    depends_on_ids: list[str] | None = None
    ai_cost: float | None = None


class TaskRecord(BaseModel):
    id: str
    title: str
    status: str | None = None
    scheduled: str | None = None
    deadline: str | None = None
    estimated_minutes: int | None = None
    importance: int | None = None
    project_id: str | None = None
    project_title: str | None = None
    contexts: list[str] = Field(default_factory=list)
    assigned_to: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_url: str | None = None
    notes: str | None = None
    phone: str | None = None
    budget: float | None = None
    goal_id: str | None = None
    parent_id: str | None = None
    dependency_of_ids: list[str] = Field(default_factory=list)
    depends_on_ids: list[str] = Field(default_factory=list)
    score: float | None = None
    ai_cost: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    created: bool = False
    task: TaskRecord | None = None
    confidence: ConfidenceInfo
    review_items: list[ReviewItem] = Field(default_factory=list)
    message: str = ""


class ProcessTaskInboxInput(BaseModel):
    max_count: int = 50
    preview_only: bool = True
    include_statuses: list[str] = Field(default_factory=lambda: ["To do", "Not started"])
    inbox_formula_property: str | None = "Inbox"
    processed_tag: str = "Inbox Processed"


class TaskInboxItemResult(BaseModel):
    task_id: str
    updated: bool = False
    created_project_id: str | None = None
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    review_items: list[ReviewItem] = Field(default_factory=list)


class ProcessTaskInboxResult(BaseModel):
    preview_only: bool
    processed_count: int
    updated_count: int
    created_projects: int
    results: list[TaskInboxItemResult] = Field(default_factory=list)
