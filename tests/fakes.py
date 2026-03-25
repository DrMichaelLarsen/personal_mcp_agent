from __future__ import annotations

import base64
import re

from app.adapters.drive_client import DriveClient
from app.adapters.llm_client import LLMClient
from app.schemas.calendar import CalendarEvent
from app.schemas.email import EmailAttachment, EmailMessage


class FakeNotionClient:
    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.counter = 0

    def create_page(self, database_id: str, properties: dict, children: list[dict] | None = None):
        self.counter += 1
        page_id = f"page-{self.counter}"
        raw = {
            "id": page_id,
            "database_id": database_id,
            "properties": properties,
            "children": children or [],
            "url": f"https://notion.so/{page_id}",
        }
        self.pages[page_id] = raw
        return raw

    def markdown_to_blocks(self, markdown: str) -> list[dict]:
        def _rich_text(value: str) -> list[dict]:
            parts: list[dict] = []
            pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
            cursor = 0
            for match in pattern.finditer(value):
                if match.start() > cursor:
                    parts.append({"text": value[cursor:match.start()], "annotations": {}})
                token = match.group(0)
                if token.startswith("**") and token.endswith("**"):
                    parts.append({"text": token[2:-2], "annotations": {"bold": True}})
                else:
                    parts.append({"text": token[1:-1], "annotations": {"italic": True}})
                cursor = match.end()
            if cursor < len(value):
                parts.append({"text": value[cursor:], "annotations": {}})
            return parts or [{"text": value, "annotations": {}}]

        blocks: list[dict] = []
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("> [!IMPORTANT] "):
                blocks.append({"type": "callout", "text": line[14:], "color": "yellow_background", "rich_text": _rich_text(line[14:])})
                continue
            if line.startswith("## "):
                blocks.append({"type": "heading_2", "text": line[3:], "rich_text": _rich_text(line[3:])})
            elif line.startswith("- [ ] "):
                blocks.append({"type": "to_do", "text": line[6:], "checked": False, "rich_text": _rich_text(line[6:])})
            elif line.startswith("- "):
                blocks.append({"type": "bulleted_list_item", "text": line[2:], "rich_text": _rich_text(line[2:])})
            else:
                blocks.append({"type": "paragraph", "text": line, "rich_text": _rich_text(line)})
        return blocks

    def update_page(self, page_id: str, properties: dict):
        self.pages[page_id]["properties"].update(properties)
        return self.pages[page_id]

    def get_page(self, page_id: str):
        return self.pages[page_id]

    def query_database(self, database_id: str, filters: dict | None = None):
        items = [page for page in self.pages.values() if page["database_id"] == database_id]
        if not filters:
            return items
        output = []
        for item in items:
            props = item["properties"]
            matched = True
            for key, value in filters.items():
                if key == "query":
                    matched = value.lower() in str(props).lower()
                else:
                    matched = props.get(key) == value
                if not matched:
                    break
            if matched:
                output.append(item)
        return output


class FakeCalendarClient:
    def __init__(self, events: list[CalendarEvent] | None = None):
        self.events = events or []

    def create_event(self, calendar_id: str, payload: dict) -> CalendarEvent:
        payload_copy = dict(payload)
        payload_copy.pop("id", None)
        event = CalendarEvent(id=f"evt-{len(self.events)+1}", **payload_copy)
        self.events.append(event)
        return event

    def list_events_for_day(self, calendar_id: str, day: str) -> list[CalendarEvent]:
        return [event for event in self.events if event.start.startswith(day)]


class FakeGmailClient:
    def __init__(self, messages: list[EmailMessage] | None = None):
        self.messages = messages or []
        self.processed: list[str] = []

    def list_tagged_messages(self, query: str, max_count: int):
        return self.messages[:max_count]

    def mark_processed(self, email_id: str, processed_label: str):
        self.processed.append(email_id)


class FakeDriveClient(DriveClient):
    def __init__(self):
        self.uploads: list[dict] = []

    def upload_bytes(self, filename: str, mime_type: str, content: bytes, folder_id: str | None = None) -> dict:
        record = {
            "id": f"drive-{len(self.uploads)+1}",
            "filename": filename,
            "mime_type": mime_type,
            "size": len(content),
            "folder_id": folder_id,
            "webViewLink": f"https://drive.google.com/file/d/drive-{len(self.uploads)+1}/view",
        }
        self.uploads.append(record)
        return record


class FakeLLMClient(LLMClient):
    def __init__(self, response_map: dict[str, dict] | None = None, default_response: dict | None = None):
        self.response_map = response_map or {}
        self.default_response = default_response or {"selected": ""}
        self.calls: list[dict] = []

    def chat_json(self, *, system_prompt: str, user_prompt: str, model: str, operation: str = "general", metadata: dict | None = None) -> dict:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
                "operation": operation,
                "metadata": metadata or {},
            }
        )
        for token, response in self.response_map.items():
            if token in user_prompt:
                return response
        return self.default_response


def make_attachment(filename: str, text: str, mime_type: str = "text/plain") -> EmailAttachment:
    return EmailAttachment(
        attachment_id=f"att-{filename}",
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(text.encode("utf-8")),
        content_b64=base64.b64encode(text.encode("utf-8")).decode("utf-8"),
    )
