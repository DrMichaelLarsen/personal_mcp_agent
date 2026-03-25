from __future__ import annotations

from typing import TypedDict

from app.schemas.calendar import CalendarDayResult
from app.schemas.planning import DayPlanResult
from app.schemas.tasks import TaskRecord


class PlanDayState(TypedDict, total=False):
    calendar: CalendarDayResult
    tasks: list[TaskRecord]
    result: DayPlanResult
