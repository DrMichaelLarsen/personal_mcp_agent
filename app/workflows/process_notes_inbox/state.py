from __future__ import annotations

from typing import TypedDict

from app.schemas.notes import NotesInboxItemResult, NoteRecord, ProcessNotesInboxResult


class ProcessNotesInboxState(TypedDict, total=False):
    notes: list[NoteRecord]
    results: list[NotesInboxItemResult]
    result: ProcessNotesInboxResult
