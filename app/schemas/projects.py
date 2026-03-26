from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ConfidenceInfo, ReviewItem


class ProjectCreateInput(BaseModel):
    title: str
    status: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    parent_project_id: str | None = None
    target_deadline: str | None = None
    importance: int | None = None
    priority: bool | None = None
    budget: float | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    starter_tasks: list[str] = Field(default_factory=list)


class ProjectRecord(BaseModel):
    id: str
    title: str
    status: str | None = None
    description: str | None = None
    area_id: str | None = None
    area_path: str | None = None
    parent_project_id: str | None = None
    project_path: str | None = None
    target_deadline: str | None = None
    importance: int | None = None
    priority: bool | None = None
    budget: float | None = None
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ProjectMatchCandidate(BaseModel):
    id: str
    title: str
    score: float


class ProjectMatchResult(BaseModel):
    matched: bool
    selected_project: ProjectRecord | None = None
    candidates: list[ProjectMatchCandidate] = Field(default_factory=list)
    confidence: ConfidenceInfo
    review_items: list[ReviewItem] = Field(default_factory=list)


class ContextRecord(BaseModel):
    id: str
    title: str
    status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class AreaRecord(BaseModel):
    id: str
    title: str
    parent_area_id: str | None = None
    status: str | None = None
    path: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
