from __future__ import annotations


class DriveClient:
    def upload_bytes(self, filename: str, mime_type: str, content: bytes, folder_id: str | None = None) -> dict:
        raise NotImplementedError("Implement Google Drive upload logic.")
