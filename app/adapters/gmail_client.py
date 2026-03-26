from __future__ import annotations

import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.schemas.email import EmailMessage


class GmailClient:
    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

    def __init__(self, credentials_path: str | None = None, token_path: str | None = None):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service
        if not self.token_path:
            raise RuntimeError("Gmail token path is not configured. Set PPMCP_GMAIL__TOKEN_PATH.")

        token_file = Path(self.token_path)
        if not token_file.exists():
            raise RuntimeError(f"Gmail token file not found: {token_file}. Generate OAuth token first.")

        creds = self._load_credentials(token_file)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_credentials(token_file, creds)
            else:
                raise RuntimeError("Gmail credentials are invalid and cannot be refreshed. Re-run OAuth flow.")

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
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

    def list_tagged_messages(self, query: str, max_count: int) -> list[EmailMessage]:
        service = self._get_service()
        label_map = self._get_label_map(service)
        response = service.users().messages().list(userId="me", q=query, maxResults=max_count).execute()
        messages = response.get("messages", [])
        results: list[EmailMessage] = []
        for msg in messages:
            message_id = msg.get("id")
            if not message_id:
                continue
            detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            payload = detail.get("payload", {})
            headers = payload.get("headers", [])
            header_map = {header.get("name", "").lower(): header.get("value", "") for header in headers}
            body_text = self._extract_text_body(detail, service)
            raw_label_ids = detail.get("labelIds", []) or []
            resolved_labels = [label_map.get(label_id, label_id) for label_id in raw_label_ids]
            results.append(
                EmailMessage(
                    id=detail.get("id", message_id),
                    thread_id=detail.get("threadId", ""),
                    subject=header_map.get("subject", "(no subject)"),
                    sender=header_map.get("from", "unknown"),
                    body=body_text,
                    labels=resolved_labels,
                )
            )
        return results

    def _get_label_map(self, service) -> dict[str, str]:
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        mapping: dict[str, str] = {}
        for label in labels:
            lid = label.get("id")
            name = label.get("name")
            if lid and name:
                mapping[lid] = name
        return mapping

    def mark_processed(self, email_id: str, processed_label: str) -> None:
        service = self._get_service()
        label_id = self._ensure_label(service, processed_label)
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def _ensure_label(self, service, label_name: str) -> str:
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            if label.get("name") == label_name:
                return label["id"]
        created = service.users().labels().create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        return created["id"]

    def _extract_text_body(self, payload: dict, service=None) -> str:
        import base64

        def _decode(data: str | None) -> str:
            if not data:
                return ""
            padding = "=" * (-len(data) % 4)
            return base64.urlsafe_b64decode((data + padding).encode("utf-8")).decode("utf-8", errors="replace")

        def _walk_parts(parts: list[dict], prefer_plain: bool = True) -> str:
            for part in parts:
                mime_type = (part.get("mimeType") or "").lower()
                body = part.get("body") or {}
                if prefer_plain and mime_type == "text/plain" and body.get("data"):
                    return _decode(body.get("data"))
                if not prefer_plain and mime_type == "text/html" and body.get("data"):
                    return _decode(body.get("data"))
                nested = _walk_parts(part.get("parts") or [], prefer_plain=prefer_plain)
                if nested:
                    return nested
                if mime_type == "message/rfc822":
                    nested = _walk_parts(part.get("parts") or [], prefer_plain=prefer_plain)
                    if nested:
                        return nested
            return ""

        def _fetch_attachment_text(message_id: str, part: dict) -> str:
            body = part.get("body") or {}
            attachment_id = body.get("attachmentId")
            if not attachment_id:
                return ""
            local_service = service or self._get_service()
            attachment = (
                local_service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            return _decode(attachment.get("data"))

        detail = payload
        root_payload = detail.get("payload", {}) if "payload" in detail else detail
        message_id = detail.get("id", "")

        body = root_payload.get("body", {})
        body_data = body.get("data")
        if body_data:
            decoded = _decode(body_data).strip()
            if decoded:
                return decoded

        parts = root_payload.get("parts", []) or []

        plain_text = _walk_parts(parts, prefer_plain=True).strip()
        if plain_text:
            return plain_text

        html_text = _walk_parts(parts, prefer_plain=False).strip()
        if html_text:
            return html_text

        for part in parts:
            mime_type = (part.get("mimeType") or "").lower()
            attachment_text = _fetch_attachment_text(message_id, part).strip() if mime_type.startswith("text/") else ""
            if attachment_text:
                return attachment_text

        snippet = detail.get("snippet") or root_payload.get("snippet")
        return snippet or ""
