from __future__ import annotations

from app.adapters.calendar_client import CalendarClient
from app.config import Settings
from app.schemas.calendar import CalendarDayResult, CalendarEvent, EventCreateInput, EventResult
from app.schemas.tasks import TaskRecord
from app.utils.confidence import build_confidence


class CalendarService:
    def __init__(self, calendar_client: CalendarClient, settings: Settings):
        self.calendar = calendar_client
        self.settings = settings

    def schedule_event(self, data: EventCreateInput) -> EventResult:
        event = CalendarEvent(
            id=f"preview:{data.title}:{data.start}",
            title=data.title,
            start=data.start,
            end=data.end,
            description=data.description,
            location=data.location,
            metadata={"task_id": data.task_id, "project_id": data.project_id, "email_id": data.email_id},
        )
        if data.dry_run:
            return EventResult(created=False, dry_run=True, event=event, confidence=build_confidence(1.0, "Dry-run event preview.", False))
        created = self.calendar.create_event(self.settings.calendar.calendar_id, event.model_dump())
        return EventResult(created=True, dry_run=False, event=created, confidence=build_confidence(1.0, "Calendar event created.", False))

    def get_calendar_for_day(self, day: str) -> CalendarDayResult:
        return CalendarDayResult(date=day, events=self.calendar.list_events_for_day(self.settings.calendar.calendar_id, day))

    def create_focus_block(self, task: TaskRecord, start: str, end: str, dry_run: bool = True) -> EventResult:
        title = f"Focus: {task.title}" if not task.project_title else f"Focus: {task.project_title} — {task.title}"
        return self.schedule_event(
            EventCreateInput(
                title=title,
                start=start,
                end=end,
                description=task.notes,
                task_id=task.id,
                project_id=task.project_id,
                dry_run=dry_run,
            )
        )
