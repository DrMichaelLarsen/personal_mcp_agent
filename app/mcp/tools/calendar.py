from __future__ import annotations

from app.schemas.calendar import EventCreateInput


def register(server, container) -> None:
    @server.tool(name="schedule_event", description="Create or preview a calendar event with optional linked metadata.")
    async def schedule_event_tool(arguments: dict):
        return container.calendar_service.schedule_event(EventCreateInput.model_validate(arguments)).model_dump()

    @server.tool(name="get_calendar_for_day", description="Get the calendar summary for a target day.")
    async def get_calendar_for_day_tool(day: str):
        return container.calendar_service.get_calendar_for_day(day).model_dump()

    @server.tool(name="create_focus_block", description="Create or preview a focus block from an existing task.")
    async def create_focus_block_tool(task_id: str, start: str, end: str, dry_run: bool = True):
        task = container.task_service.get_task(task_id)
        return container.calendar_service.create_focus_block(task, start, end, dry_run=dry_run).model_dump()
