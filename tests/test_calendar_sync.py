from __future__ import annotations

from app.config import Settings
from app.schemas.calendar import CalendarEvent
from app.schemas.calendar_sync import CalendarSyncInput
from app.services.calendar_sync_service import CalendarSyncService
from tests.fakes import FakeCalendarClient, FakeNotionClient


def make_settings(tmp_path, mode: str = "google_authoritative") -> Settings:
    return Settings(
        events_db={
            "database_id": "events-db",
            "title_property": "Name",
            "start_property": "Date",
            "end_property": "Date",
            "notes_property": "Description",
        },
        calendar={"calendar_id": "primary", "timezone": "America/Denver"},
        calendar_sync={
            "state_path": str(tmp_path / "sync-state.json"),
            "mode": mode,
            "calendar_ids": ["primary"],
            "enabled": False,
        },
    )


def test_google_event_creates_notion_page_and_backlink_idempotently(tmp_path):
    settings = make_settings(tmp_path)
    notion = FakeNotionClient()
    calendar = FakeCalendarClient(
        [
            CalendarEvent(
                id="google-1",
                title="Clinic",
                start="2026-08-20T09:00:00-06:00",
                end="2026-08-20T10:00:00-06:00",
                description="Bring notes",
                html_link="https://calendar.google.com/event?eid=google-1",
            )
        ]
    )
    service = CalendarSyncService(calendar, notion, settings)
    request = CalendarSyncInput(start="2026-08-17", end="2026-11-17")

    first = service.sync(request)

    assert first.notion_pages_created == 1
    assert first.google_events_updated == 1
    page = next(iter(notion.pages.values()))
    assert page["properties"]["Event ID"] == "google-1"
    assert page["properties"]["Date"] == {
        "start": "2026-08-20T09:00:00-06:00",
        "end": "2026-08-20T10:00:00-06:00",
    }
    assert page["url"] in calendar.events[0].description
    assert calendar.events[0].metadata["notion_page_id"] == page["id"]

    second = service.sync(request)
    assert second.notion_pages_created == 0
    assert second.notion_pages_updated == 0
    assert second.google_events_updated == 0


def test_all_day_google_end_is_converted_from_exclusive_to_inclusive(tmp_path):
    settings = make_settings(tmp_path)
    notion = FakeNotionClient()
    calendar = FakeCalendarClient(
        [
            CalendarEvent(
                id="all-day-1",
                title="Conference",
                start="2026-08-20",
                end="2026-08-23",
                all_day=True,
            )
        ]
    )

    CalendarSyncService(calendar, notion, settings).sync(
        CalendarSyncInput(start="2026-08-17", end="2026-11-17")
    )

    page = next(iter(notion.pages.values()))
    assert page["properties"]["Date"] == {"start": "2026-08-20", "end": "2026-08-22"}


def test_two_way_mode_pushes_later_notion_edit_to_google(tmp_path):
    settings = make_settings(tmp_path, mode="two_way")
    notion = FakeNotionClient()
    calendar = FakeCalendarClient(
        [
            CalendarEvent(
                id="google-2",
                title="Original title",
                start="2026-08-21T13:00:00-06:00",
                end="2026-08-21T14:00:00-06:00",
            )
        ]
    )
    service = CalendarSyncService(calendar, notion, settings)
    request = CalendarSyncInput(start="2026-08-17", end="2026-11-17", mode="two_way")
    service.sync(request)
    page = next(iter(notion.pages.values()))
    notion.update_page(page["id"], {"Name": "Edited in Notion"})

    result = service.sync(request)

    assert calendar.events[0].title == "Edited in Notion"
    assert any(item.action == "update_google_from_notion" for item in result.items)


def test_two_way_mode_creates_google_event_for_unlinked_notion_page(tmp_path):
    settings = make_settings(tmp_path, mode="two_way")
    notion = FakeNotionClient()
    page = notion.create_page(
        settings.events_db.database_id,
        {
            "Name": "Created in Notion",
            "Date": {"start": "2026-08-24", "end": None},
            "Description": "All-day note",
        },
    )
    calendar = FakeCalendarClient()

    result = CalendarSyncService(calendar, notion, settings).sync(
        CalendarSyncInput(start="2026-08-17", end="2026-11-17", mode="two_way")
    )

    assert result.google_events_created == 1
    assert calendar.events[0].start == "2026-08-24"
    assert calendar.events[0].end == "2026-08-25"
    assert page["url"] in calendar.events[0].description
    assert notion.pages[page["id"]]["properties"]["Event ID"] == calendar.events[0].id
