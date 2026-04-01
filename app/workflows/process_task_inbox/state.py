from __future__ import annotations

from typing import TypedDict

from app.schemas.tasks import ProcessTaskInboxResult, TaskInboxItemResult, TaskRecord


class ProcessTaskInboxState(TypedDict, total=False):
    tasks: list[TaskRecord]
    results: list[TaskInboxItemResult]
    result: ProcessTaskInboxResult
