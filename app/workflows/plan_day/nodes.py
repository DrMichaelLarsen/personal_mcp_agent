from __future__ import annotations

from app.workflows.plan_day.state import PlanDayState


def fetch_calendar(state: PlanDayState, deps: dict) -> PlanDayState:
    request = deps["request"]
    calendar_service = deps["calendar_service"]
    if request.include_calendar:
        return {**state, "calendar": calendar_service.get_calendar_for_day(request.target_date)}
    return state


def fetch_tasks(state: PlanDayState, deps: dict) -> PlanDayState:
    request = deps["request"]
    task_service = deps["task_service"]
    return {**state, "tasks": task_service.list_tasks_for_today(request.target_date)}


def build_plan(state: PlanDayState, deps: dict) -> PlanDayState:
    request = deps["request"]
    planning_service = deps["planning_service"]
    result = planning_service.build_plan(
        target_date=request.target_date,
        tasks=state.get("tasks", []),
        calendar=state.get("calendar"),
        max_tasks=request.max_tasks,
        start_hour=request.workday_start_hour,
        end_hour=request.workday_end_hour,
    )
    return {**state, "result": result}
