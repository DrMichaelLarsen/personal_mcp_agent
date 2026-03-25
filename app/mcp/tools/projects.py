from __future__ import annotations

from app.schemas.projects import ProjectCreateInput


def register(server, container) -> None:
    @server.tool(name="create_project", description="Create a project in the configured Projects database using schema-aware defaults.")
    async def create_project_tool(arguments: dict):
        return container.project_service.create_project(ProjectCreateInput.model_validate(arguments)).model_dump()

    @server.tool(name="find_project", description="Find the best matching project for a provided name and return candidates if ambiguous.")
    async def find_project_tool(name: str):
        result = container.matching_service.match_project(name, container.project_service.list_projects())
        return result.model_dump()

    @server.tool(name="get_project", description="Get a project by ID.")
    async def get_project_tool(project_id: str):
        return container.project_service.get_project(project_id).model_dump()

    @server.tool(name="list_projects", description="List configured projects.")
    async def list_projects_tool():
        return [project.model_dump() for project in container.project_service.list_projects()]
