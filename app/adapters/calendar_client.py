from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.schemas.calendar import CalendarEvent


class CalendarClient:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, credentials_path: str | None = None, token_path: str | None = None, timezone: str = "America/Denver"):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.timezone = timezone
        self._service = None

    def _build_datetime_payload(self, value: str) -> dict:
        if "T" not in value:
            return {"date": value}
        # If caller already provides offset or Z, pass through directly.
        lower = value.lower()
        if lower.endswith("z") or "+" in value[10:] or "-" in value[10:]:
            return {"dateTime": value}
        return {"dateTime": value, "timeZone": self.timezone}

    def _format_google_error(self, exc: Exception) -> str:
        parts = [repr(exc)]
        status = getattr(exc, "status_code", None)
        if status:
            parts.append(f"status={status}")
        resp = getattr(exc, "resp", None)
        if resp is not None:
            parts.append(f"http_status={getattr(resp, 'status', None)}")
            parts.append(f"reason={getattr(resp, 'reason', None)}")
        content = getattr(exc, "content", None)
        if content:
            try:
                parts.append(content.decode("utf-8", errors="replace") if isinstance(content, (bytes, bytearray)) else str(content))
            except Exception:
                parts.append(str(content))
        return " | ".join(part for part in parts if part and part != "None")

    def _get_service(self):
        if self._service is not None:
            return self._service
        token_source = self.token_path or self.credentials_path
        if not token_source:
            raise RuntimeError(
                "Calendar token path is not configured. Set PPMCP_CALENDAR__TOKEN_PATH "
                "(or use PPMCP_CALENDAR__CREDENTIALS_PATH as a fallback token file path)."
            )

        token_file = Path(token_source)
        if not token_file.exists():
            raise RuntimeError(f"Calendar token file not found: {token_file}. Generate OAuth token first.")

        creds = self._load_credentials(token_file)
        required_scope = self.SCOPES[0]
        granted_scopes = set(getattr(creds, "scopes", []) or [])
        if required_scope not in granted_scopes:
            raise RuntimeError(
                "Calendar token is missing required scope 'https://www.googleapis.com/auth/calendar'. "
                "If reusing a Gmail token, regenerate OAuth token with calendar scope included."
            )
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_credentials(token_file, creds)
            else:
                raise RuntimeError("Calendar credentials are invalid and cannot be refreshed. Re-run OAuth flow.")

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _load_credentials(self, token_file: Path) -> Credentials:
        if token_file.suffix.lower() in {".pickle", ".pkl"}:
            with token_file.open("rb") as fp:
                creds = pickle.load(fp)
            if not isinstance(creds, Credentials):
                raise RuntimeError(f"Token file {token_file} does not contain valid Google Credentials.")
            return creds
        return Credentials.from_authorized_user_file(str(token_file), self.SCOPES)

    def _save_credentials(self, token_file: Path, creds: Credentials) -> None:
        if token_file.suffix.lower() in {".pickle", ".pkl"}:
            with token_file.open("wb") as fp:
                pickle.dump(creds, fp)
            return
        token_file.write_text(creds.to_json(), encoding="utf-8")

    def create_event(self, calendar_id: str, payload: dict) -> CalendarEvent:
        service = self._get_service()
        body: dict[str, Any] = {
            "summary": payload.get("title"),
            "description": payload.get("description"),
            "location": payload.get("location"),
            "start": self._build_datetime_payload(payload.get("start")),
            "end": self._build_datetime_payload(payload.get("end")),
            "extendedProperties": {"private": {k: str(v) for k, v in (payload.get("metadata") or {}).items() if v is not None}},
        }
        for source, target in (
            ("status", "status"),
            ("transparency", "transparency"),
            ("visibility", "visibility"),
        ):
            if payload.get(source):
                body[target] = payload[source]
        try:
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
        except Exception as exc:  # pragma: no cover
            detail = self._format_google_error(exc)
            raise RuntimeError(f"Google Calendar insert failed for calendar_id='{calendar_id}': {detail}") from exc
        return self._to_event(created, calendar_id=calendar_id, calendar_name=payload.get("calendar_name"))

    def update_event(self, calendar_id: str, event_id: str, payload: dict) -> CalendarEvent:
        """Patch writable event fields without replacing attendees or recurrence data."""
        service = self._get_service()
        body: dict[str, Any] = {}
        mapping = {
            "title": "summary",
            "description": "description",
            "location": "location",
            "transparency": "transparency",
            "visibility": "visibility",
        }
        for source, target in mapping.items():
            if source in payload:
                body[target] = payload[source]
        if payload.get("start"):
            body["start"] = self._build_datetime_payload(payload["start"])
        if payload.get("end"):
            body["end"] = self._build_datetime_payload(payload["end"])
        metadata = payload.get("metadata")
        if metadata is not None:
            body["extendedProperties"] = {
                "private": {k: str(v) for k, v in metadata.items() if v is not None}
            }
        try:
            updated = service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
        except Exception as exc:  # pragma: no cover
            detail = self._format_google_error(exc)
            raise RuntimeError(
                f"Google Calendar update failed for calendar_id='{calendar_id}', event_id='{event_id}': {detail}"
            ) from exc
        return self._to_event(updated, calendar_id=calendar_id, calendar_name=payload.get("calendar_name"))

    def list_calendars(self) -> list[dict[str, str]]:
        service = self._get_service()
        results: list[dict[str, str]] = []
        page_token = None
        while True:
            response = service.calendarList().list(pageToken=page_token).execute()
            for item in response.get("items", []):
                if item.get("id"):
                    results.append({"id": item["id"], "name": item.get("summaryOverride") or item.get("summary") or item["id"]})
            page_token = response.get("nextPageToken")
            if not page_token:
                return results

    def get_calendar_name(self, calendar_id: str) -> str:
        try:
            raw = self._get_service().calendars().get(calendarId=calendar_id).execute()
            return raw.get("summary") or calendar_id
        except Exception:  # pragma: no cover - a display name is non-critical
            return calendar_id

    def list_events(self, calendar_id: str, time_min: str, time_max: str, calendar_name: str | None = None) -> list[CalendarEvent]:
        service = self._get_service()
        events: list[CalendarEvent] = []
        page_token = None
        while True:
            try:
                response = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        showDeleted=True,
                        orderBy="startTime",
                        maxResults=2500,
                        pageToken=page_token,
                    )
                    .execute()
                )
            except Exception as exc:  # pragma: no cover
                detail = self._format_google_error(exc)
                raise RuntimeError(f"Google Calendar list failed for calendar_id='{calendar_id}': {detail}") from exc
            events.extend(
                self._to_event(item, calendar_id=calendar_id, calendar_name=calendar_name)
                for item in response.get("items", [])
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    def list_events_for_day(self, calendar_id: str, day: str) -> list[CalendarEvent]:
        time_min = f"{day}T00:00:00Z"
        time_max = f"{day}T23:59:59Z"
        return self.list_events(calendar_id, time_min, time_max)

    def _to_event(self, item: dict[str, Any], calendar_id: str, calendar_name: str | None = None) -> CalendarEvent:
        start_obj = item.get("start") or {}
        end_obj = item.get("end") or {}
        attendees = []
        response_status = None
        for attendee in item.get("attendees") or []:
            label = attendee.get("displayName") or attendee.get("email")
            if label:
                attendees.append(label)
            if attendee.get("self"):
                response_status = attendee.get("responseStatus")
        organizer_obj = item.get("organizer") or {}
        conference_link = item.get("hangoutLink")
        if not conference_link:
            for entry in ((item.get("conferenceData") or {}).get("entryPoints") or []):
                if entry.get("uri"):
                    conference_link = entry["uri"]
                    break
        return CalendarEvent(
            id=item.get("id", ""),
            title=item.get("summary", "(no title)"),
            start=start_obj.get("dateTime") or start_obj.get("date") or "",
            end=end_obj.get("dateTime") or end_obj.get("date") or "",
            description=item.get("description"),
            location=item.get("location"),
            metadata=((item.get("extendedProperties") or {}).get("private") or {}),
            calendar_id=calendar_id,
            calendar_name=calendar_name or calendar_id,
            html_link=item.get("htmlLink"),
            status=item.get("status") or "confirmed",
            transparency=item.get("transparency") or "opaque",
            visibility=item.get("visibility") or "default",
            attendees=attendees,
            organizer=organizer_obj.get("displayName") or organizer_obj.get("email"),
            conference_link=conference_link,
            response_status=response_status,
            updated=item.get("updated"),
            all_day=bool(start_obj.get("date")),
        )
