from __future__ import annotations

from app.schemas.planning import DayPlanInput


def register(server, container) -> None:
    @server.tool(name="plan_day", description="Build a structured day plan from tasks and calendar data, optionally committing focus blocks.")
    async def plan_day_tool(arguments: dict):
        return container.plan_day_workflow.run(DayPlanInput.model_validate(arguments)).model_dump()

    @server.tool(name="preview_day_plan", description="Preview a structured day plan without committing focus blocks.")
    async def preview_day_plan_tool(arguments: dict):
        payload = DayPlanInput.model_validate({**arguments, "preview_only": True})
        return container.plan_day_workflow.run(payload).model_dump()
