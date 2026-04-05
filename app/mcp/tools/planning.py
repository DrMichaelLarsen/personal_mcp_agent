from __future__ import annotations

from datetime import datetime, timedelta

from app.schemas.planning import DayPlanInput, DayScheduleBuildInput, ScheduleTaskAtTimeInput
from app.schemas.tasks import TaskCreateInput


def register(server, container) -> None:
    @server.tool(name="plan_day", description="Build a structured day plan from tasks and calendar data, optionally committing focus blocks.")
    async def plan_day_tool(arguments: dict):
        return container.plan_day_workflow.run(DayPlanInput.model_validate(arguments)).model_dump()

    @server.tool(name="preview_day_plan", description="Preview a structured day plan without committing focus blocks.")
    async def preview_day_plan_tool(arguments: dict):
        payload = DayPlanInput.model_validate({**arguments, "preview_only": True})
        return container.plan_day_workflow.run(payload).model_dump()

    @server.tool(name="build_day_schedule", description="Build today's schedule from Notion Events, Tasks, and Checklist Items, with optional commit mode.")
    async def build_day_schedule_tool(arguments: dict):
        payload = DayScheduleBuildInput.model_validate(arguments)
        if not payload.preserve_existing_scheduled:
            cleared_tasks = container.task_service.clear_schedule_for_day(payload.target_date)
            cleared_checklist = container.checklist_service.clear_schedule_for_day(payload.target_date)
            cleared_existing_count = cleared_tasks + cleared_checklist
        else:
            cleared_existing_count = 0
        result = container.planning_service.build_day_schedule(
            target_date=payload.target_date,
            tasks=container.task_service.list_open_tasks(),
            checklist_items=container.checklist_service.list_open_items(),
            events=container.event_service.list_events_for_day(payload.target_date),
            preserve_existing_scheduled=payload.preserve_existing_scheduled,
            day_start=payload.day_start,
            day_end=payload.day_end,
            day_start_hour=payload.day_start_hour,
            day_end_hour=payload.day_end_hour,
            buffer_minutes=payload.buffer_minutes,
            include_due_tomorrow=payload.include_due_tomorrow,
            max_candidates=payload.max_candidates,
            preview_only=payload.preview_only,
            cleared_existing_count=cleared_existing_count,
        )
        if not payload.preview_only:
            for item in result.scheduled_items:
                if item.source != "new":
                    continue
                if item.item_type == "task":
                    container.task_service.set_schedule(item.item_id, item.start)
                else:
                    container.checklist_service.set_schedule(item.item_id, item.start)
        return result.model_dump()

    @server.tool(name="schedule_task_at_time", description="Schedule an existing task, or create one, at a specific time today.")
    async def schedule_task_at_time_tool(arguments: dict):
        payload = ScheduleTaskAtTimeInput.model_validate(arguments)
        task = container.task_service.get_task(payload.task_id) if payload.task_id else None
        if task is None:
            if payload.preview_only:
                task_title = payload.task_title or "Scheduled task"
            else:
                create_result = container.task_service.create_task(
                    TaskCreateInput(
                        title=payload.task_title or "Scheduled task",
                        project_id=payload.project_id,
                        project_name=payload.project_name,
                        scheduled=payload.start,
                        deadline=payload.deadline,
                        estimated_minutes=payload.duration_minutes,
                    )
                )
                task = create_result.task
                task_title = task.title if task else (payload.task_title or "Scheduled task")
        elif not payload.preview_only:
            task = container.task_service.set_schedule(task.id, payload.start)
            task_title = task.title
        else:
            task_title = task.title
        if task is None:
            return {
                "preview_only": True,
                "task_id": None,
                "title": task_title,
                "scheduled_start": payload.start,
                "scheduled_end": (datetime.fromisoformat(payload.start) + timedelta(minutes=payload.duration_minutes)).isoformat(),
                "estimated_minutes": payload.duration_minutes,
            }
        end = datetime.fromisoformat(payload.start) + timedelta(minutes=payload.duration_minutes)
        return {
            "preview_only": payload.preview_only,
            "task_id": task.id,
            "title": task_title,
            "scheduled_start": payload.start,
            "scheduled_end": end.isoformat(),
            "estimated_minutes": payload.duration_minutes,
        }
