from __future__ import annotations

from app.schemas.calendar import CalendarEvent


class CalendarClient:
    def create_event(self, calendar_id: str, payload: dict) -> CalendarEvent:
        raise NotImplementedError("Implement Google Calendar event creation logic.")

    def list_events_for_day(self, calendar_id: str, day: str) -> list[CalendarEvent]:
        raise NotImplementedError("Implement Google Calendar list logic.")
