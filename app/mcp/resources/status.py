from __future__ import annotations

from datetime import date


def register(server, container) -> None:
    @server.resource("today_calendar_summary")
    async def today_calendar_summary():
        return container.calendar_service.get_calendar_for_day(date.today().isoformat()).model_dump()

    @server.resource("today_task_summary")
    async def today_task_summary():
        return [task.model_dump() for task in container.task_service.list_tasks_for_today(date.today().isoformat())]

    @server.resource("ai_pricing")
    async def ai_pricing():
        if not container.cost_service:
            return {"pricing": []}
        return {"pricing": container.cost_service.get_pricing_table()}

    @server.resource("ai_cost_summary")
    async def ai_cost_summary():
        if not container.cost_service:
            return {"event_count": 0, "total_estimated_cost": 0.0, "by_provider": {}}
        return container.cost_service.summarize_usage()
