from __future__ import annotations

import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.schemas.calendar import CalendarEvent


class CalendarClient:
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, credentials_path: str | None = None, token_path: str | None = None):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

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
        body = {
            "summary": payload.get("title"),
            "description": payload.get("description"),
            "location": payload.get("location"),
            "start": {"dateTime": payload.get("start")},
            "end": {"dateTime": payload.get("end")},
            "extendedProperties": {"private": {k: str(v) for k, v in (payload.get("metadata") or {}).items() if v is not None}},
        }
        try:
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"Google Calendar insert failed for calendar_id='{calendar_id}': {exc}") from exc
        return CalendarEvent(
            id=created.get("id", ""),
            title=created.get("summary", payload.get("title", "")),
            start=(created.get("start") or {}).get("dateTime") or payload.get("start"),
            end=(created.get("end") or {}).get("dateTime") or payload.get("end"),
            description=created.get("description"),
            location=created.get("location"),
            metadata=((created.get("extendedProperties") or {}).get("private") or {}),
        )

    def list_events_for_day(self, calendar_id: str, day: str) -> list[CalendarEvent]:
        service = self._get_service()
        time_min = f"{day}T00:00:00Z"
        time_max = f"{day}T23:59:59Z"
        response = (
            service.events()
            .list(calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy="startTime")
            .execute()
        )
        events = []
        for item in response.get("items", []):
            events.append(
                CalendarEvent(
                    id=item.get("id", ""),
                    title=item.get("summary", "(no title)"),
                    start=((item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or ""),
                    end=((item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date") or ""),
                    description=item.get("description"),
                    location=item.get("location"),
                    metadata=((item.get("extendedProperties") or {}).get("private") or {}),
                )
            )
        return events
