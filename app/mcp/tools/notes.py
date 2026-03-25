from __future__ import annotations

from app.schemas.notes import NoteCreateInput


def register(server, container) -> None:
    @server.tool(name="create_note", description="Create a structured note/reference entry in the configured Notes database.")
    async def create_note_tool(arguments: dict):
        return container.note_service.create_note(NoteCreateInput.model_validate(arguments)).model_dump()

    @server.tool(name="search_notes", description="Search notes by a query string.")
    async def search_notes_tool(query: str):
        return [note.model_dump() for note in container.note_service.search_notes(query)]
