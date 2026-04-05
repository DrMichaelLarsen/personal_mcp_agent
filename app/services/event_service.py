from __future__ import annotations

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
        return [
            item
            for item in items
            if not item.done and item.start.startswith(day) and not self._is_all_day_event(item)
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
            done=bool(props.get(cfg.done_property)) if cfg.done_property else False,
            start=start,
            end=end,
            location=props.get("Location"),
            notes=props.get(cfg.notes_property) if cfg.notes_property else None,
            raw=raw,
        )
