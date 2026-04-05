from __future__ import annotations

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.checklist import ChecklistItemRecord


class ChecklistService:
    def __init__(self, notion_client: NotionClient, settings: Settings):
        self.notion = notion_client
        self.settings = settings

    def list_open_items(self) -> list[ChecklistItemRecord]:
        cfg = self.settings.checklist_items_db
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        return [item for item in items if not item.done and (item.status or "").strip().lower() != "complete"]

    def set_schedule(self, item_id: str, scheduled: str | dict | None, estimated_minutes: int | float | None = None) -> ChecklistItemRecord:
        cfg = self.settings.checklist_items_db
        properties: dict[str, object] = {}
        if cfg.scheduled_property:
            properties[cfg.scheduled_property] = scheduled
        if estimated_minutes is not None and cfg.estimate_property:
            properties[cfg.estimate_property] = max(1, int(round(estimated_minutes)))

        if not properties:
            return self.get_item(item_id)

        if cfg.scheduled_property and len(properties) == 1:
            self.notion.set_page_property(item_id, cfg.scheduled_property, scheduled)
        else:
            self.notion.update_page(item_id, properties)
        return self.get_item(item_id)

    def clear_schedule_for_day(self, day: str) -> int:
        cleared = 0
        for item in self.list_open_items():
            if (item.scheduled or "").startswith(day):
                self.set_schedule(item.id, None)
                cleared += 1
        return cleared

    def get_item(self, item_id: str) -> ChecklistItemRecord:
        return self._to_record(self.notion.get_page(item_id))

    def _to_record(self, raw: dict) -> ChecklistItemRecord:
        props = raw.get("properties", {})
        cfg = self.settings.checklist_items_db

        def _as_minutes(value):
            if value is None:
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(round(value))
            if isinstance(value, str):
                try:
                    return int(round(float(value.strip())))
                except ValueError:
                    return None
            return None

        return ChecklistItemRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            status=props.get(cfg.status_property) if cfg.status_property else None,
            done=bool(props.get(cfg.done_property)) if cfg.done_property else False,
            scheduled=(
                (props.get(cfg.scheduled_property) or {}).get("start")
                if cfg.scheduled_property and isinstance(props.get(cfg.scheduled_property), dict)
                else (props.get(cfg.scheduled_property) if cfg.scheduled_property else None)
            ),
            deadline=props.get(cfg.deadline_property) if cfg.deadline_property else None,
            estimated_minutes=_as_minutes(props.get(cfg.estimate_property)) if cfg.estimate_property else None,
            score=props.get(cfg.score_property) if cfg.score_property else None,
            preferred_start=props.get(cfg.preferred_start_property) if cfg.preferred_start_property else None,
            preferred_end=props.get(cfg.preferred_end_property) if cfg.preferred_end_property else None,
            preferred_time_mode=props.get(cfg.preferred_time_mode_property) if cfg.preferred_time_mode_property else None,
            schedule_filter=bool(props.get(cfg.schedule_filter_property)) if cfg.schedule_filter_property else None,
            raw=raw,
        )
