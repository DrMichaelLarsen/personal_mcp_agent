from __future__ import annotations

from app.schemas.tasks import ProcessTaskInboxInput, TaskCreateInput, TaskUpdateInput


def register(server, container) -> None:
    @server.tool(name="create_task", description="Create a task in the configured Tasks database using schema-aware defaults and project matching rules.")
    async def create_task_tool(arguments: dict):
        return container.task_service.create_task(TaskCreateInput.model_validate(arguments)).model_dump()

    @server.tool(name="update_task", description="Update an existing task in the configured Tasks database.")
    async def update_task_tool(arguments: dict):
        return container.task_service.update_task(TaskUpdateInput.model_validate(arguments)).model_dump()

    @server.tool(name="get_task", description="Get a task by ID.")
    async def get_task_tool(task_id: str):
        return container.task_service.get_task(task_id).model_dump()

    @server.tool(name="list_tasks_for_today", description="List tasks due on a target date.")
    async def list_tasks_for_today_tool(day: str):
        return [task.model_dump() for task in container.task_service.list_tasks_for_today(day)]

    @server.tool(name="list_tasks_for_project", description="List tasks linked to a project.")
    async def list_tasks_for_project_tool(project_id: str):
        return [task.model_dump() for task in container.task_service.list_tasks_for_project(project_id)]

    @server.tool(name="process_task_inbox", description="Process Notion inbox tasks by enriching missing fields and tagging them as processed.")
    async def process_task_inbox_tool(arguments: dict):
        payload_dict = {"processed_tag": container.settings.task_inbox_processed_tag, **arguments}
        return container.process_task_inbox_workflow.run(ProcessTaskInboxInput.model_validate(payload_dict)).model_dump()

    @server.tool(name="preview_task_inbox", description="Preview inbox task enrichment without committing any writes.")
    async def preview_task_inbox_tool(arguments: dict):
        payload = ProcessTaskInboxInput.model_validate({"processed_tag": container.settings.task_inbox_processed_tag, **arguments, "preview_only": True})
        return container.process_task_inbox_workflow.run(payload).model_dump()
