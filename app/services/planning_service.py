from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.config import Settings
from app.schemas.checklist import ChecklistItemRecord
from app.schemas.events import EventRecord
from app.schemas.planning import DayPlanResult, DayScheduleBuildResult, ScheduledItem, PlannedTask, TimeBlock
from app.schemas.tasks import TaskRecord
from app.utils.confidence import build_confidence
from app.utils.datetime import add_minutes, combine_day_and_hour


class PlanningService:
    def __init__(self, settings: Settings, llm_client=None):
        self.settings = settings
        self.llm_client = llm_client

    def build_plan(
        self,
        target_date: str,
        tasks: list[TaskRecord],
        calendar,
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

    def build_day_schedule(
        self,
        *,
        target_date: str,
        tasks: list[TaskRecord],
        checklist_items: list[ChecklistItemRecord],
        events: list[EventRecord],
        preserve_existing_scheduled: bool = True,
        day_start: str | None = None,
        day_end: str | None = None,
        day_start_hour: int | None = None,
        day_end_hour: int | None = None,
        buffer_minutes: int | None = None,
        include_due_tomorrow: bool = True,
        max_candidates: int = 25,
        preview_only: bool = True,
        cleared_existing_count: int = 0,
    ) -> DayScheduleBuildResult:
        schedule_day = date.fromisoformat(target_date)
        work_start = self._resolve_bound(schedule_day, day_start, day_start_hour, self.settings.workday.start_hour)
        work_end = self._resolve_bound(schedule_day, day_end, day_end_hour, self.settings.workday.end_hour)
        buffer_value = buffer_minutes if buffer_minutes is not None else self.settings.workday.buffer_minutes

        busy_blocks: list[TimeBlock] = []
        intervals: list[tuple[datetime, datetime]] = []
        for event in sorted(events, key=lambda item: item.start):
            start = self._parse_iso(event.start) - timedelta(minutes=buffer_value)
            end = self._parse_iso(event.end) + timedelta(minutes=buffer_value)
            intervals.append((start, end))
            busy_blocks.append(
                TimeBlock(title=event.title, start=start.isoformat(), end=end.isoformat(), task_id=event.id, block_type="event")
            )

        scheduled_items: list[ScheduledItem] = []
        if preserve_existing_scheduled:
            for existing in self._existing_scheduled_items(tasks, checklist_items, target_date):
                start = self._parse_iso(existing["scheduled"])
                end = start + timedelta(minutes=existing["estimated_minutes"])
                intervals.append((start, end))
                scheduled_items.append(
                    ScheduledItem(
                        item_id=existing["id"],
                        item_type=existing["item_type"],
                        title=existing["title"],
                        start=start.isoformat(),
                        end=end.isoformat(),
                        estimated_minutes=existing["estimated_minutes"],
                        source="existing",
                        deadline=existing.get("deadline"),
                        score=existing.get("score"),
                    )
                )

        candidates = self._select_candidates(
            target_date=target_date,
            tasks=tasks,
            checklist_items=checklist_items,
            preserve_existing_scheduled=preserve_existing_scheduled,
            include_due_tomorrow=include_due_tomorrow,
            max_candidates=max_candidates,
        )
        free_slots = self._compute_free_slots(work_start, work_end, intervals)
        unscheduled_items: list[dict] = []

        for candidate in candidates:
            placement = self._place_candidate(candidate, free_slots)
            if placement is None and candidate["urgency_rank"] <= 1:
                late_start = max(work_end, max((end for _, end in intervals), default=work_end))
                placement = (late_start, late_start + timedelta(minutes=candidate["estimated_minutes"]))
            if placement is None:
                unscheduled_items.append(candidate)
                continue
            start, end = placement
            self._consume_slot(free_slots, start, end)
            scheduled_items.append(
                ScheduledItem(
                    item_id=candidate["id"],
                    item_type=candidate["item_type"],
                    title=candidate["title"],
                    start=start.isoformat(),
                    end=end.isoformat(),
                    estimated_minutes=candidate["estimated_minutes"],
                    source="new",
                    deadline=candidate.get("deadline"),
                    score=candidate.get("score"),
                )
            )

        rationale = [
            "Blocked time around incomplete events before placing work.",
            "Scheduled overdue and due-today items first, then due tomorrow, then by Score.",
            "Kept existing scheduled items fixed unless start-from-scratch mode was requested.",
        ]
        if not preserve_existing_scheduled:
            rationale[2] = "Start-from-scratch mode rebuilt today's schedule and reassigned time blocks."

        return DayScheduleBuildResult(
            target_date=target_date,
            scheduled_items=sorted(scheduled_items, key=lambda item: item.start),
            unscheduled_items=unscheduled_items,
            busy_blocks=sorted(busy_blocks, key=lambda item: item.start),
            rationale=rationale,
            confidence=build_confidence(0.9 if not unscheduled_items else 0.78, "Daily schedule built from events, tasks, and checklist items.", False),
            preview_only=preview_only,
            cleared_existing_count=cleared_existing_count,
        )

    def _resolve_bound(self, day: date, iso_value: str | None, hour_value: int | None, fallback_hour: int) -> datetime:
        if iso_value:
            return self._parse_iso(iso_value)
        return combine_day_and_hour(day, hour_value if hour_value is not None else fallback_hour)

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _existing_scheduled_items(self, tasks: list[TaskRecord], checklist_items: list[ChecklistItemRecord], target_date: str) -> list[dict]:
        existing: list[dict] = []
        for task in tasks:
            if (task.scheduled or "").startswith(target_date):
                existing.append(
                    {
                        "id": task.id,
                        "item_type": "task",
                        "title": task.title,
                        "scheduled": task.scheduled,
                        "estimated_minutes": task.estimated_minutes or 30,
                        "deadline": task.deadline,
                        "score": task.score,
                    }
                )
        for item in checklist_items:
            if (item.scheduled or "").startswith(target_date):
                existing.append(
                    {
                        "id": item.id,
                        "item_type": "checklist_item",
                        "title": item.title,
                        "scheduled": item.scheduled,
                        "estimated_minutes": item.estimated_minutes or 30,
                        "deadline": item.deadline,
                        "score": item.score,
                    }
                )
        return sorted(existing, key=lambda value: value["scheduled"])

    def _select_candidates(
        self,
        *,
        target_date: str,
        tasks: list[TaskRecord],
        checklist_items: list[ChecklistItemRecord],
        preserve_existing_scheduled: bool,
        include_due_tomorrow: bool,
        max_candidates: int,
    ) -> list[dict]:
        tomorrow = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()
        selected: list[dict] = []

        def _add(item_type: str, item_id: str, title: str, scheduled: str | None, deadline: str | None, estimated_minutes: int | None, score: float | None, preferred_start: str | None = None, preferred_end: str | None = None, preferred_time_mode: str | None = None):
            if preserve_existing_scheduled and (scheduled or "").startswith(target_date):
                return
            urgency_rank = 3
            if deadline and deadline < target_date:
                urgency_rank = 0
            elif deadline == target_date:
                urgency_rank = 1
            elif include_due_tomorrow and deadline == tomorrow:
                urgency_rank = 2
            selected.append(
                {
                    "id": item_id,
                    "item_type": item_type,
                    "title": title,
                    "scheduled": scheduled,
                    "deadline": deadline,
                    "estimated_minutes": estimated_minutes or 30,
                    "score": score or 0,
                    "urgency_rank": urgency_rank,
                    "preferred_start": preferred_start,
                    "preferred_end": preferred_end,
                    "preferred_time_mode": preferred_time_mode,
                }
            )

        for task in tasks:
            _add("task", task.id, task.title, task.scheduled, task.deadline, task.estimated_minutes, task.score)
        for item in checklist_items:
            _add(
                "checklist_item",
                item.id,
                item.title,
                item.scheduled,
                item.deadline,
                item.estimated_minutes,
                item.score,
                item.preferred_start,
                item.preferred_end,
                item.preferred_time_mode,
            )

        ordered = sorted(
            selected,
            key=lambda item: (
                item["urgency_rank"],
                -(item["score"] or 0),
                item["deadline"] or "9999-12-31",
                item["title"],
            ),
        )
        return ordered[:max_candidates]

    def _compute_free_slots(self, work_start: datetime, work_end: datetime, intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
        slots: list[tuple[datetime, datetime]] = []
        cursor = work_start
        for start, end in sorted(intervals, key=lambda item: item[0]):
            if end <= cursor:
                continue
            normalized_start = max(start, work_start)
            normalized_end = min(end, work_end)
            if normalized_start > cursor:
                slots.append((cursor, normalized_start))
            cursor = max(cursor, normalized_end)
        if cursor < work_end:
            slots.append((cursor, work_end))
        return slots

    def _place_candidate(self, candidate: dict, free_slots: list[tuple[datetime, datetime]]) -> tuple[datetime, datetime] | None:
        duration = timedelta(minutes=candidate["estimated_minutes"])
        preferred_start = self._parse_iso(candidate["preferred_start"]) if candidate.get("preferred_start") else None
        preferred_end = self._parse_iso(candidate["preferred_end"]) if candidate.get("preferred_end") else None
        preferred_mode = (candidate.get("preferred_time_mode") or "soft").strip().lower()

        for start, end in free_slots:
            candidate_start = start
            candidate_end = start + duration
            if preferred_start and candidate_start < preferred_start:
                candidate_start = preferred_start
                candidate_end = candidate_start + duration
            if candidate_end > end:
                continue
            if preferred_end and candidate_end > preferred_end and preferred_mode == "hard":
                continue
            return candidate_start, candidate_end
        return None

    def _consume_slot(self, free_slots: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> None:
        for index, (slot_start, slot_end) in enumerate(list(free_slots)):
            if start < slot_start or end > slot_end:
                continue
            replacement: list[tuple[datetime, datetime]] = []
            if start > slot_start:
                replacement.append((slot_start, start))
            if end < slot_end:
                replacement.append((end, slot_end))
            free_slots[index:index + 1] = replacement
            return
