from __future__ import annotations

from datetime import date, datetime

from app.config import Settings
from app.schemas.calendar import CalendarDayResult
from app.schemas.planning import DayPlanResult, PlannedTask, TimeBlock
from app.schemas.tasks import TaskRecord
from app.utils.confidence import build_confidence
from app.utils.datetime import add_minutes, combine_day_and_hour


class PlanningService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_plan(
        self,
        target_date: str,
        tasks: list[TaskRecord],
        calendar: CalendarDayResult | None,
        max_tasks: int,
        start_hour: int | None = None,
        end_hour: int | None = None,
    ) -> DayPlanResult:
        day = date.fromisoformat(target_date)
        start = combine_day_and_hour(day, start_hour or self.settings.workday.start_hour)
        end = combine_day_and_hour(day, end_hour or self.settings.workday.end_hour)
        sorted_tasks = sorted(
            tasks,
            key=lambda t: (
                -(t.score or 0),
                -(t.importance or 0),
                t.deadline or "9999-12-31",
                t.scheduled or "9999-12-31",
                t.title,
            ),
        )
        selected = sorted_tasks[:max_tasks]
        planned_tasks: list[PlannedTask] = []
        suggested_blocks: list[TimeBlock] = []
        cursor: datetime = start

        busy = {(event.start, event.end) for event in (calendar.events if calendar else [])}
        for index, task in enumerate(selected, start=1):
            category = "deep_work" if (task.estimated_minutes or 0) >= 60 else "quick_win"
            planned_tasks.append(
                PlannedTask(
                    task_id=task.id,
                    title=task.title,
                    category=category,
                    recommended_order=index,
                    estimated_minutes=task.estimated_minutes,
                )
            )
            minutes = task.estimated_minutes or 30
            block_start = cursor
            block_end = add_minutes(block_start, minutes)
            while any(not (block_end.isoformat() <= s or block_start.isoformat() >= e) for s, e in busy):
                block_start = add_minutes(block_start, 30)
                block_end = add_minutes(block_start, minutes)
            if block_end <= end:
                suggested_blocks.append(
                    TimeBlock(
                        title=f"Focus: {task.title}",
                        start=block_start.isoformat(),
                        end=block_end.isoformat(),
                        task_id=task.id,
                        block_type="focus",
                    )
                )
                cursor = add_minutes(block_end, self.settings.workday.buffer_minutes)

        deferred = [task.title for task in sorted_tasks[max_tasks:]]
        rationale = [
            "Prioritized high-priority and nearer-due tasks first.",
            "Generated focus blocks conservatively around existing calendar events.",
        ]
        return DayPlanResult(
            target_date=target_date,
            prioritized_tasks=planned_tasks,
            suggested_blocks=suggested_blocks,
            deferred_tasks=deferred,
            rationale=rationale,
            confidence=build_confidence(0.82 if deferred else 0.9, "Planning result based on current tasks and calendar.", False),
            committed_focus_blocks=False,
        )
