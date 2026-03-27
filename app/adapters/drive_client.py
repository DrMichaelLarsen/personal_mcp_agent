from __future__ import annotations

import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload


class DriveClient:
    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

    def __init__(self, credentials_path: str | None = None, token_path: str | None = None):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service
        token_source = self.token_path or self.credentials_path
        if not token_source:
            raise RuntimeError("Drive token path is not configured.")
        token_file = Path(token_source)
        if not token_file.exists():
            raise RuntimeError(f"Drive token file not found: {token_file}")
        creds = self._load_credentials(token_file)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_credentials(token_file, creds)
            else:
                raise RuntimeError("Drive credentials are invalid and cannot be refreshed.")
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
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

    def upload_bytes(self, filename: str, mime_type: str, content: bytes, folder_id: str | None = None) -> dict:
        service = self._get_service()
        metadata = {"name": filename}
        if folder_id:
            metadata["parents"] = [folder_id]
        media = MediaInMemoryUpload(content, mimetype=mime_type, resumable=False)
        created = service.files().create(body=metadata, media_body=media, fields="id,name,webViewLink,webContentLink").execute()
        try:
            service.permissions().create(fileId=created["id"], body={"type": "anyone", "role": "reader"}).execute()
            created = service.files().get(fileId=created["id"], fields="id,name,webViewLink,webContentLink").execute()
        except Exception:
            # Keep private if org policy blocks public links.
            pass
        return created
