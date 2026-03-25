from __future__ import annotations


def register(server, container) -> None:
    @server.resource("mcp://resources/current_task_schema")
    async def current_task_schema():
        return container.settings.tasks_db.model_dump()

    @server.resource("mcp://resources/current_project_schema")
    async def current_project_schema():
        return container.settings.projects_db.model_dump()

    @server.resource("mcp://resources/current_note_schema")
    async def current_note_schema():
        return container.settings.notes_db.model_dump()

    @server.resource("mcp://resources/current_area_schema")
    async def current_area_schema():
        return container.settings.areas_db.model_dump()

    @server.resource("mcp://resources/system_capabilities")
    async def system_capabilities():
        return {
            "tools": [
                "create_task",
                "create_project",
                "create_note",
                "find_project",
                "preview_tagged_emails",
                "plan_day",
            ],
            "workflow_backed_tools": ["process_tagged_emails", "plan_day"],
            "preview_first": True,
        }
