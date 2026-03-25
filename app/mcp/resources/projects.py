from __future__ import annotations


def register(server, container) -> None:
    @server.resource("mcp://resources/available_project_list")
    async def available_project_list():
        return [project.model_dump() for project in container.project_service.list_projects()]

    @server.resource("mcp://resources/available_area_tree")
    async def available_area_tree():
        areas = container.project_service.list_areas()
        return {
            "areas": [area.model_dump() for area in areas],
            "paths": [area.path for area in areas if area.path],
        }
