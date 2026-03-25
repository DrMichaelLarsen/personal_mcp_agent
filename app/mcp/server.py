from __future__ import annotations

from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from app.mcp.resources import projects as project_resources
from app.mcp.resources import schemas as schema_resources
from app.mcp.resources import status as status_resources
from app.mcp.tools import calendar, email, notes, planning, projects, tasks


@dataclass
class ServiceContainer:
    settings: object
    project_service: object
    matching_service: object
    task_service: object
    note_service: object
    calendar_service: object
    email_service: object
    planning_service: object
    process_emails_workflow: object
    plan_day_workflow: object
    cost_service: object | None = None


def build_mcp_server(container: ServiceContainer) -> FastMCP:
    server = FastMCP(name="personal-productivity-mcp")
    tasks.register(server, container)
    projects.register(server, container)
    notes.register(server, container)
    calendar.register(server, container)
    email.register(server, container)
    planning.register(server, container)
    schema_resources.register(server, container)
    project_resources.register(server, container)
    status_resources.register(server, container)
    return server
