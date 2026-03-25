from __future__ import annotations

from app.schemas.email import EmailMessage


class GmailClient:
    def list_tagged_messages(self, query: str, max_count: int) -> list[EmailMessage]:
        raise NotImplementedError("Implement Gmail query logic.")

    def mark_processed(self, email_id: str, processed_label: str) -> None:
        raise NotImplementedError("Implement Gmail label update logic.")
