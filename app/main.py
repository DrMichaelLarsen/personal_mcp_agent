from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.adapters.calendar_client import CalendarClient
from app.adapters.drive_client import DriveClient
from app.adapters.gmail_client import GmailClient
from app.adapters.llm_client import create_llm_client
from app.adapters.notion_client import NotionClient
from app.config import get_settings
from app.logging import configure_logging
from app.mcp.server import ServiceContainer, build_mcp_server
from app.schemas.email import EmailMessage, ProcessEmailsInput
from app.schemas.notes import ProcessNotesInboxInput
from app.schemas.planning import DayScheduleBuildInput, ScheduleTaskAtTimeInput
from app.schemas.tasks import ProcessTaskInboxInput, TaskCreateInput
from app.services.calendar_service import CalendarService
from app.services.checklist_service import ChecklistService
from app.services.cost_service import CostService
from app.services.email_service import EmailAnalysisService, EmailService
from app.services.event_service import EventService
from app.services.matching_service import MatchingService
from app.services.note_service import NoteService
from app.services.planning_service import PlanningService
from app.services.project_service import ProjectService
from app.services.task_service import TaskService
from app.workflows.plan_day.graph import PlanDayWorkflow
from app.workflows.process_emails.graph import ProcessEmailsWorkflow
from app.workflows.process_notes_inbox.graph import ProcessNotesInboxWorkflow
from app.workflows.process_task_inbox.graph import ProcessTaskInboxWorkflow

configure_logging()
settings = get_settings()

notion_client = NotionClient(settings.notion_api_key)
gmail_client = GmailClient(
    credentials_path=settings.gmail.credentials_path,
    token_path=settings.gmail.token_path,
)
calendar_client = CalendarClient(
    credentials_path=settings.calendar.credentials_path,
    token_path=settings.calendar.token_path,
    timezone=settings.calendar.timezone,
)
cost_service = CostService(settings.llm)
llm_selection = create_llm_client(settings.llm, cost_service=cost_service)
llm_client = llm_selection.client

project_service = ProjectService(notion_client, settings, llm_client=llm_client, cost_service=cost_service)
matching_service = MatchingService(settings, llm_client=llm_client, cost_service=cost_service)
task_service = TaskService(notion_client, project_service, matching_service, settings)
checklist_service = ChecklistService(notion_client, settings)
note_service = NoteService(notion_client, project_service, matching_service, settings)
calendar_service = CalendarService(calendar_client, settings)
event_service = EventService(notion_client, settings)
drive_client = (
    DriveClient(
        credentials_path=settings.gmail.credentials_path,
        token_path=settings.gmail.token_path,
    )
    if settings.attachments.mode == "drive_link"
    else None
)
email_service = EmailService(
    gmail_client,
    settings,
    analysis_service=EmailAnalysisService(llm_client=llm_client, settings=settings, cost_service=cost_service),
    drive_client=drive_client,
)
planning_service = PlanningService(settings, llm_client=llm_client)

container = ServiceContainer(
    settings=settings,
    project_service=project_service,
    matching_service=matching_service,
    task_service=task_service,
    checklist_service=checklist_service,
    note_service=note_service,
    calendar_service=calendar_service,
    event_service=event_service,
    email_service=email_service,
    planning_service=planning_service,
    process_emails_workflow=ProcessEmailsWorkflow(
        {
            "email_service": email_service,
            "matching_service": matching_service,
            "project_service": project_service,
            "task_service": task_service,
            "note_service": note_service,
            "calendar_service": calendar_service,
            "cost_service": cost_service,
        }
    ),
    process_task_inbox_workflow=ProcessTaskInboxWorkflow(
        {
            "task_service": task_service,
            "project_service": project_service,
            "matching_service": matching_service,
            "llm_client": llm_client,
            "settings": settings,
            "cost_service": cost_service,
        }
    ),
    process_notes_inbox_workflow=ProcessNotesInboxWorkflow(
        {
            "note_service": note_service,
            "project_service": project_service,
            "matching_service": matching_service,
            "llm_client": llm_client,
            "settings": settings,
            "cost_service": cost_service,
        }
    ),
    plan_day_workflow=PlanDayWorkflow(
        {
            "calendar_service": calendar_service,
            "task_service": task_service,
            "planning_service": planning_service,
        }
    ),
    cost_service=cost_service,
)

mcp_server = build_mcp_server(container)
app = FastAPI(title=settings.app_name)


class ProcessSingleEmailRequest(BaseModel):
    email: EmailMessage
    preview_only: bool = False
    confidence_threshold: float = 0.8
    mark_processed: bool = True
    create_project_if_missing: bool = False


class ProcessInboxRequest(BaseModel):
    max_count: int = 10
    preview_only: bool = False
    confidence_threshold: float = 0.8
    mark_processed: bool = True
    query: str | None = None
    create_project_if_missing: bool = False


class ProcessTaskInboxRequest(BaseModel):
    max_count: int = 50
    preview_only: bool = True
    include_statuses: list[str] = Field(default_factory=lambda: ["To do", "Not started"])
    inbox_formula_property: str | None = "Inbox"
    processed_tag: str | None = None


class ProcessNotesInboxRequest(BaseModel):
    max_count: int = 50
    preview_only: bool = True
    inbox_formula_property: str | None = "Inbox"
    processed_tag: str | None = None


class BuildScheduleRequest(DayScheduleBuildInput):
    pass


class ScheduleTaskAtTimeRequest(ScheduleTaskAtTimeInput):
    pass


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/capabilities")
async def capabilities() -> dict:
    return {
        "preview_tools": ["preview_tagged_emails", "preview_day_plan", "build_day_schedule(preview_only=true)", "schedule_event(dry_run=true)"],
        "write_tools": ["create_task", "create_project", "create_note", "schedule_task_at_time"],
        "workflow_tools": ["process_tagged_emails", "plan_day", "build_day_schedule"],
        "llm_provider": llm_selection.provider,
        "llm_tier": settings.llm.quality_tier,
    }


@app.get("/resources/system-capabilities")
async def http_system_capabilities() -> dict:
    return {
        "schemas": {
            "tasks": settings.tasks_db.model_dump(),
            "checklist_items": settings.checklist_items_db.model_dump(),
            "events": settings.events_db.model_dump(),
            "projects": settings.projects_db.model_dump(),
            "notes": settings.notes_db.model_dump(),
        }
    }


@app.get("/debug/projects")
async def debug_projects() -> dict:
    projects = project_service.list_active_projects()
    return {
        "count": len(projects),
        "items": [
            {
                "id": project.id,
                "title": project.title,
                "description": project.description,
                "status": project.status,
                "area_path": project.area_path,
                "project_path": project.project_path,
            }
            for project in projects
        ],
    }


@app.get("/ai/pricing")
async def ai_pricing() -> dict:
    return {"pricing": cost_service.get_pricing_table()}


@app.get("/ai/cost-summary")
async def ai_cost_summary() -> dict:
    return cost_service.summarize_usage()


@app.post("/workflows/process-email-preview")
async def process_email_preview(payload: ProcessSingleEmailRequest) -> dict:
    try:
        result = container.process_emails_workflow.run(
            ProcessEmailsInput(
                preview_only=True,
                confidence_threshold=payload.confidence_threshold,
                mark_processed=False,
                input_emails=[payload.email],
                max_count=1,
                create_project_if_missing=False,
            )
        )
        return result.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/workflows/process-email")
async def process_email(payload: ProcessSingleEmailRequest) -> dict:
    try:
        result = container.process_emails_workflow.run(
            ProcessEmailsInput(
                preview_only=payload.preview_only,
                confidence_threshold=payload.confidence_threshold,
                mark_processed=payload.mark_processed,
                input_emails=[payload.email],
                max_count=1,
                create_project_if_missing=payload.create_project_if_missing,
            )
        )
        return result.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/workflows/process-inbox")
async def process_inbox(payload: ProcessInboxRequest) -> dict:
    try:
        result = container.process_emails_workflow.run(
            ProcessEmailsInput(
                max_count=payload.max_count,
                preview_only=payload.preview_only,
                confidence_threshold=payload.confidence_threshold,
                mark_processed=payload.mark_processed,
                query=payload.query,
                create_project_if_missing=payload.create_project_if_missing,
            )
        )
        return result.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/workflows/process-task-inbox")
async def process_task_inbox(payload: ProcessTaskInboxRequest) -> dict:
    try:
        result = container.process_task_inbox_workflow.run(
            ProcessTaskInboxInput(
                max_count=payload.max_count,
                preview_only=payload.preview_only,
                include_statuses=payload.include_statuses,
                inbox_formula_property=payload.inbox_formula_property,
                processed_tag=payload.processed_tag or settings.task_inbox_processed_tag,
            )
        )
        return result.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/workflows/process-notes-inbox")
async def process_notes_inbox(payload: ProcessNotesInboxRequest) -> dict:
    try:
        result = container.process_notes_inbox_workflow.run(
            ProcessNotesInboxInput(
                max_count=payload.max_count,
                preview_only=payload.preview_only,
                inbox_formula_property=payload.inbox_formula_property,
                processed_tag=payload.processed_tag or settings.notes_inbox_processed_tag,
            )
        )
        return result.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _build_day_schedule(payload: DayScheduleBuildInput):
    if not payload.preserve_existing_scheduled:
        cleared_tasks = task_service.clear_schedule_for_day(payload.target_date)
        cleared_checklist = checklist_service.clear_schedule_for_day(payload.target_date)
        cleared_existing_count = cleared_tasks + cleared_checklist
    else:
        cleared_existing_count = 0

    result = planning_service.build_day_schedule(
        target_date=payload.target_date,
        tasks=task_service.list_open_tasks(),
        checklist_items=checklist_service.list_open_items(),
        events=event_service.list_events_for_day(payload.target_date),
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
                task_service.set_schedule(item.item_id, item.start)
            else:
                checklist_service.set_schedule(item.item_id, item.start)
    return result


@app.post("/planning/build-day-schedule")
async def build_day_schedule(payload: BuildScheduleRequest) -> dict:
    try:
        return _build_day_schedule(payload).model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/planning/schedule-task-at-time")
async def schedule_task_at_time(payload: ScheduleTaskAtTimeRequest) -> dict:
    try:
        task = task_service.get_task(payload.task_id) if payload.task_id else None
        if task is None:
            if payload.preview_only:
                task_title = payload.task_title or "Scheduled task"
            else:
                create_result = task_service.create_task(
                    TaskCreateInput(
                        title=payload.task_title or "Scheduled task",
                        project_id=payload.project_id,
                        project_name=payload.project_name,
                        scheduled=payload.start,
                        deadline=payload.deadline,
                        estimated_minutes=payload.duration_minutes,
                    )
                )
                if create_result.task is None:
                    raise RuntimeError(create_result.message or "Unable to create task.")
                task = create_result.task
                task_title = task.title
        elif not payload.preview_only:
            task = task_service.set_schedule(task.id, payload.start)
            task_title = task.title
        else:
            task_title = task.title

        end = datetime.fromisoformat(payload.start) + timedelta(minutes=payload.duration_minutes)
        return {
            "preview_only": payload.preview_only,
            "task_id": task.id if task else None,
            "title": task_title,
            "scheduled_start": payload.start,
            "scheduled_end": end.isoformat(),
            "estimated_minutes": payload.duration_minutes,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
