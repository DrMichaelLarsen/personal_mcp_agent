from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.events import EventRecord


class EventService:
    def __init__(self, notion_client: NotionClient, settings: Settings):
        self.notion = notion_client
        self.settings = settings

    def list_events_for_day(self, day: str) -> list[EventRecord]:
        cfg = self.settings.events_db
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        target_day = date.fromisoformat(day)
        day_start = datetime.combine(target_day, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        def _overlaps_target_day(event: EventRecord) -> bool:
            try:
                start = self._parse_iso_local(event.start)
                end = self._parse_iso_local(event.end)
            except ValueError:
                return False
            return start < day_end and end > day_start

        return [
            item
            for item in items
            if not item.done and not self._is_all_day_event(item) and _overlaps_target_day(item)
        ]

    def _is_all_day_event(self, event: EventRecord) -> bool:
        # Date-only values (no time component) are treated as all-day and should
        # not block scheduling windows for focused work.
        start = event.start or ""
        end = event.end or ""
        return "T" not in start or "T" not in end

    def _to_record(self, raw: dict) -> EventRecord:
        props = raw.get("properties", {})
        cfg = self.settings.events_db

        def _as_done(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                normalized = value.strip().lower()
                return normalized in {"done", "complete", "completed", "true", "yes", "1"}
            return False

        start_raw = props.get(cfg.start_property) if cfg.start_property else None
        end_raw = props.get(cfg.end_property) if cfg.end_property else None

        def _date_start(value) -> str | None:
            if isinstance(value, dict):
                return value.get("start")
            return value if isinstance(value, str) else None

        def _date_end(value) -> str | None:
            if isinstance(value, dict):
                return value.get("end") or value.get("start")
            return value if isinstance(value, str) else None

        start = _date_start(start_raw) or ""
        end = _date_end(end_raw) or _date_end(start_raw) or start

        return EventRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            done=_as_done(props.get(cfg.done_property)) if cfg.done_property else False,
            start=start,
            end=end,
            location=props.get("Location"),
            notes=props.get(cfg.notes_property) if cfg.notes_property else None,
            raw=raw,
        )

    def _parse_iso_local(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed
        timezone_name = self.settings.calendar.timezone or "America/Denver"
        return parsed.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
