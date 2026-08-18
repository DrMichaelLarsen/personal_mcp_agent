from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.calendar_client import CalendarClient
from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.calendar import CalendarEvent
from app.schemas.calendar_sync import CalendarSyncInput, CalendarSyncItem, CalendarSyncResult


class CalendarSyncService:
    """Idempotent Calendar/Notion sync, with Google authoritative by default."""

    LINK_MARKER = "[Synced by Personal Productivity MCP]"

    def __init__(self, calendar: CalendarClient, notion: NotionClient, settings: Settings):
        self.calendar = calendar
        self.notion = notion
        self.settings = settings
        self._lock = threading.Lock()

    def sync(self, data: CalendarSyncInput | None = None) -> CalendarSyncResult:
        data = data or CalendarSyncInput()
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A calendar sync is already running.")
        try:
            return self._sync_locked(data)
        finally:
            self._lock.release()

    def _sync_locked(self, data: CalendarSyncInput) -> CalendarSyncResult:
        start_dt, end_dt = self._resolve_window(data)
        mode = data.mode or self.settings.calendar_sync.mode
        result = CalendarSyncResult(
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            mode=mode,
            dry_run=data.dry_run,
        )
        calendars = self._resolve_calendars(data.calendar_ids)
        google_events: list[CalendarEvent] = []
        for calendar_id, calendar_name in calendars:
            try:
                google_events.extend(
                    self.calendar.list_events(
                        calendar_id,
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                        calendar_name=calendar_name,
                    )
                )
            except Exception as exc:
                result.errors.append(f"Calendar {calendar_name}: {exc}")
        result.google_events_seen = len(google_events)

        pages = self._query_notion_window(start_dt.date().isoformat(), end_dt.date().isoformat())
        result.notion_pages_seen = len(pages)
        pages_by_event_id: dict[str, list[dict[str, Any]]] = {}
        event_id_property = self.settings.calendar_sync.event_id_property
        for page in pages:
            event_id = (page.get("properties") or {}).get(event_id_property)
            if isinstance(event_id, str) and event_id:
                pages_by_event_id.setdefault(event_id, []).append(page)

        state = self._load_state()
        seen_page_ids: set[str] = set()
        for event in google_events:
            if not event.id:
                result.skipped += 1
                continue
            try:
                candidates = pages_by_event_id.get(event.id, [])
                if not candidates:
                    # A moved event's old Notion date may be outside this window.
                    candidates = self.notion.query_database(
                        self.settings.events_db.database_id,
                        {event_id_property: event.id},
                    )
                    if candidates:
                        pages_by_event_id[event.id] = candidates
                page = self._pick_page(candidates, event.calendar_name)
                if page:
                    seen_page_ids.add(page.get("id", ""))
                self._sync_google_event(event, page, mode, state, result, data.dry_run)
            except Exception as exc:
                result.errors.append(f"Event {event.id}: {exc}")

        if mode == "two_way":
            default_calendar_id, default_calendar_name = calendars[0]
            for page in pages:
                page_id = page.get("id", "")
                props = page.get("properties") or {}
                if page_id in seen_page_ids or props.get(event_id_property):
                    continue
                if props.get(self.settings.calendar_sync.sync_status_property) in {"Ignored", "Removed"}:
                    result.skipped += 1
                    continue
                try:
                    self._create_google_from_notion(
                        page,
                        default_calendar_id,
                        default_calendar_name,
                        state,
                        result,
                        data.dry_run,
                    )
                except Exception as exc:
                    result.errors.append(f"Notion page {page_id}: {exc}")

        if not data.dry_run:
            self._save_state(state)
        return result

    def _resolve_window(self, data: CalendarSyncInput) -> tuple[datetime, datetime]:
        zone = ZoneInfo(self.settings.calendar.timezone or "America/Denver")
        now = datetime.now(zone)

        def parse(value: str | None, fallback: datetime) -> datetime:
            if not value:
                return fallback
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zone)
            return parsed.astimezone(zone)

        default_start = datetime.combine(now.date(), time.min, tzinfo=zone) - timedelta(
            days=self.settings.calendar_sync.past_days
        )
        start = parse(data.start, default_start)
        days = data.days_ahead or self.settings.calendar_sync.lookahead_days
        end = parse(data.end, start + timedelta(days=days))
        if end <= start:
            raise ValueError("Calendar sync end must be after start.")
        return start, end

    def _resolve_calendars(self, requested: list[str] | None) -> list[tuple[str, str]]:
        ids = requested or self.settings.calendar_sync.calendar_ids or [self.settings.calendar.calendar_id]
        if "*" in ids:
            rows = self.calendar.list_calendars()
            if self.settings.calendar_sync.editable_calendars_only:
                rows = [row for row in rows if row.get("access_role") in {"owner", "writer"}]
            if not rows:
                raise RuntimeError("Google Calendar returned no editable calendars.")
            return [(row["id"], row.get("name") or row["id"]) for row in rows]
        return [(calendar_id, self.calendar.get_calendar_name(calendar_id)) for calendar_id in ids]

    def _query_notion_window(self, start: str, end: str) -> list[dict[str, Any]]:
        cfg = self.settings.events_db
        if not cfg.start_property:
            raise RuntimeError("Events database start/date property is not configured.")
        query_range = getattr(self.notion, "query_database_date_range", None)
        if query_range:
            return query_range(cfg.database_id, cfg.start_property, start, end)
        return self.notion.query_database(cfg.database_id)

    def _pick_page(self, pages: list[dict[str, Any]], calendar_name: str | None) -> dict[str, Any] | None:
        if not pages:
            return None
        calendar_property = self.settings.calendar_sync.calendar_name_property
        for page in pages:
            if (page.get("properties") or {}).get(calendar_property) == calendar_name:
                return page
        return pages[0]

    def _sync_google_event(
        self,
        event: CalendarEvent,
        page: dict[str, Any] | None,
        mode: str,
        state: dict[str, Any],
        result: CalendarSyncResult,
        dry_run: bool,
    ) -> None:
        state_key = f"{event.calendar_id or self.settings.calendar.calendar_id}:{event.id}"
        previous = (state.get("events") or {}).get(state_key)
        if event.status == "cancelled":
            if page:
                updates = {
                    self.settings.calendar_sync.event_status_property: "Cancelled",
                    self.settings.calendar_sync.sync_status_property: "Removed",
                }
                if not dry_run:
                    self.notion.update_page(page["id"], updates)
                result.notion_pages_updated += 1
                result.items.append(self._item("update_notion_cancelled", event, page))
            else:
                result.skipped += 1
            return

        google_fingerprint = self._google_fingerprint(event)
        notion_fingerprint = self._notion_fingerprint(page) if page else None
        notion_changed = bool(previous and page and previous.get("notion_fingerprint") != notion_fingerprint)
        google_changed = bool(previous and previous.get("google_fingerprint") != google_fingerprint)

        if mode == "two_way" and page and previous and notion_changed and not google_changed:
            payload = self._google_payload_from_notion(page)
            notion_url = page.get("url")
            payload["description"] = self._with_notion_link(payload.get("description"), notion_url)
            payload["metadata"] = self._link_metadata(event.metadata, page)
            if not dry_run:
                event = self.calendar.update_event(event.calendar_id or self.settings.calendar.calendar_id, event.id, payload)
            result.google_events_updated += 1
            result.items.append(self._item("update_google_from_notion", event, page))
        else:
            desired = self._notion_properties_from_google(event)
            if page is None:
                if dry_run:
                    result.notion_pages_created += 1
                    result.items.append(self._item("create_notion", event, None, "Would create a Notion event page and add its link to Google."))
                    return
                page = self.notion.create_page(self.settings.events_db.database_id, desired)
                result.notion_pages_created += 1
                result.items.append(self._item("create_notion", event, page))
            else:
                changes = self._changed_properties(page.get("properties") or {}, desired)
                if changes:
                    if not dry_run:
                        updated = self.notion.update_page(page["id"], changes)
                        page = {**page, **updated}
                    result.notion_pages_updated += 1
                    result.items.append(self._item("update_notion_from_google", event, page))

        if page:
            linked_description = self._with_notion_link(event.description, page.get("url"))
            metadata = self._link_metadata(event.metadata, page)
            if linked_description != (event.description or "") or metadata != event.metadata:
                if not dry_run:
                    event = self.calendar.update_event(
                        event.calendar_id or self.settings.calendar.calendar_id,
                        event.id,
                        {
                            "description": linked_description,
                            "metadata": metadata,
                            "calendar_name": event.calendar_name,
                        },
                    )
                result.google_events_updated += 1
                result.items.append(self._item("add_notion_link_to_google", event, page))

            state.setdefault("events", {})[state_key] = {
                "notion_page_id": page.get("id"),
                "notion_url": page.get("url"),
                "google_fingerprint": self._google_fingerprint(event),
                "notion_fingerprint": self._notion_fingerprint(page),
            }

    def _create_google_from_notion(
        self,
        page: dict[str, Any],
        calendar_id: str,
        calendar_name: str,
        state: dict[str, Any],
        result: CalendarSyncResult,
        dry_run: bool,
    ) -> None:
        payload = self._google_payload_from_notion(page)
        if not payload.get("start") or not payload.get("end"):
            result.skipped += 1
            result.items.append(CalendarSyncItem(action="skip_notion_without_date", notion_page_id=page.get("id")))
            return
        payload["description"] = self._with_notion_link(payload.get("description"), page.get("url"))
        payload["metadata"] = self._link_metadata({}, page)
        payload["calendar_name"] = calendar_name
        if dry_run:
            result.google_events_created += 1
            result.items.append(CalendarSyncItem(action="create_google", calendar_id=calendar_id, notion_page_id=page.get("id"), notion_url=page.get("url")))
            return
        event = self.calendar.create_event(calendar_id, payload)
        event.calendar_id = calendar_id
        event.calendar_name = calendar_name
        updates = self._notion_properties_from_google(event)
        self.notion.update_page(page["id"], updates)
        result.google_events_created += 1
        result.notion_pages_updated += 1
        result.items.append(self._item("create_google", event, page))
        state.setdefault("events", {})[f"{calendar_id}:{event.id}"] = {
            "notion_page_id": page.get("id"),
            "notion_url": page.get("url"),
            "google_fingerprint": self._google_fingerprint(event),
            "notion_fingerprint": self._notion_fingerprint(
                {**page, "properties": {**(page.get("properties") or {}), **updates}}
            ),
        }

    def _notion_properties_from_google(self, event: CalendarEvent) -> dict[str, Any]:
        db = self.settings.events_db
        sync = self.settings.calendar_sync
        start, end = self._google_dates_for_notion(event)
        props: dict[str, Any] = {
            db.title_property: event.title or "(no title)",
            sync.event_id_property: event.id,
            sync.item_link_property: event.html_link,
            sync.calendar_name_property: event.calendar_name,
            sync.source_property: "Google Calendar",
            sync.sync_status_property: "Synced",
            sync.event_status_property: self._title_case_status(event.status),
            sync.freebusy_property: "Free" if event.transparency == "transparent" else "Busy",
            sync.attendees_property: ", ".join(event.attendees) or None,
            sync.organizer_property: event.organizer,
            sync.visibility_property: self._title_case_status(event.visibility),
            sync.conference_link_property: event.conference_link,
            sync.automation_name_property: "Google Calendar & Notion Sync",
            sync.labels_property: ["Event"],
            "Location": event.location,
        }
        if db.notes_property:
            props[db.notes_property] = self._strip_notion_link(event.description)
        if db.start_property:
            if db.end_property == db.start_property:
                props[db.start_property] = {"start": start, "end": end}
            else:
                props[db.start_property] = start
                if db.end_property:
                    props[db.end_property] = end
        props["Response Status"] = self._title_case_status(event.response_status) if event.response_status else None
        return {key: value for key, value in props.items() if key}

    def _google_payload_from_notion(self, page: dict[str, Any]) -> dict[str, Any]:
        props = page.get("properties") or {}
        db = self.settings.events_db
        date_value = props.get(db.start_property) if db.start_property else None
        if isinstance(date_value, dict):
            start = date_value.get("start")
            end = date_value.get("end")
        else:
            start = date_value
            end = props.get(db.end_property) if db.end_property and db.end_property != db.start_property else None
        if isinstance(end, dict):
            end = end.get("end") or end.get("start")
        if start and "T" not in start:
            inclusive_end = date.fromisoformat(end or start)
            end = (inclusive_end + timedelta(days=1)).isoformat()
        elif start and not end:
            parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end = (parsed + timedelta(hours=1)).isoformat()
        return {
            "title": props.get(db.title_property) or page.get("title") or "(no title)",
            "start": start,
            "end": end,
            "description": props.get(db.notes_property) if db.notes_property else None,
            "location": props.get("Location"),
            "transparency": "transparent" if props.get(self.settings.calendar_sync.freebusy_property) == "Free" else "opaque",
            "visibility": str(props.get(self.settings.calendar_sync.visibility_property) or "default").lower(),
        }

    def _google_dates_for_notion(self, event: CalendarEvent) -> tuple[str, str | None]:
        if event.all_day or (event.start and "T" not in event.start):
            start = date.fromisoformat(event.start)
            exclusive_end = date.fromisoformat(event.end) if event.end else start + timedelta(days=1)
            inclusive_end = exclusive_end - timedelta(days=1)
            return start.isoformat(), None if inclusive_end == start else inclusive_end.isoformat()
        return event.start, event.end or None

    def _changed_properties(self, current: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in desired.items() if not self._values_equal(current.get(key), value)}

    def _values_equal(self, left: Any, right: Any) -> bool:
        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: normalize(v) for k, v in value.items() if v is not None}
            if isinstance(value, list):
                return [normalize(v) for v in value]
            return value
        return normalize(left) == normalize(right)

    def _with_notion_link(self, description: str | None, notion_url: str | None) -> str:
        clean = self._strip_notion_link(description).rstrip()
        if not notion_url:
            return clean
        label = self.settings.calendar_sync.notion_link_label
        block = f"{label}: {notion_url}\n{self.LINK_MARKER}"
        return f"{clean}\n\n---\n{block}" if clean else block

    def _strip_notion_link(self, description: str | None) -> str:
        value = description or ""
        pattern = rf"\s*(?:---\s*)?[^\n:]+:\s*https?://[^\s]+\s*\n{re.escape(self.LINK_MARKER)}\s*$"
        return re.sub(pattern, "", value, flags=re.IGNORECASE).rstrip()

    def _link_metadata(self, metadata: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
        return {
            **(metadata or {}),
            "notion_page_id": page.get("id"),
            "notion_url": page.get("url"),
            "managed_by": "personal-productivity-mcp",
        }

    def _google_fingerprint(self, event: CalendarEvent) -> str:
        return self._fingerprint(
            {
                "title": event.title,
                "start": event.start,
                "end": event.end,
                "description": self._strip_notion_link(event.description),
                "location": event.location,
                "status": event.status,
                "transparency": event.transparency,
                "visibility": event.visibility,
            }
        )

    def _notion_fingerprint(self, page: dict[str, Any] | None) -> str | None:
        if not page:
            return None
        payload = self._google_payload_from_notion(page)
        return self._fingerprint(payload)

    def _fingerprint(self, value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _title_case_status(self, value: str) -> str:
        spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value or "")
        return spaced.replace("_", " ").strip().title()

    def _item(
        self,
        action: str,
        event: CalendarEvent,
        page: dict[str, Any] | None,
        message: str | None = None,
    ) -> CalendarSyncItem:
        return CalendarSyncItem(
            action=action,
            event_id=event.id,
            calendar_id=event.calendar_id,
            notion_page_id=page.get("id") if page else None,
            notion_url=page.get("url") if page else None,
            message=message,
        )

    def _load_state(self) -> dict[str, Any]:
        path = Path(self.settings.calendar_sync.state_path)
        if not path.exists():
            return {"version": 1, "events": {}}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and isinstance(state.get("events"), dict):
                return state
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "events": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = Path(self.settings.calendar_sync.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
