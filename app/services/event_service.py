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
            if not item.done and item.start.startswith(day)
        ]

    def _to_record(self, raw: dict) -> EventRecord:
        props = raw.get("properties", {})
        cfg = self.settings.events_db
        return EventRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            done=bool(props.get(cfg.done_property)) if cfg.done_property else False,
            start=props.get(cfg.start_property) or "",
            end=props.get(cfg.end_property) or "",
            location=props.get("Location"),
            notes=props.get(cfg.notes_property) if cfg.notes_property else None,
            raw=raw,
        )
