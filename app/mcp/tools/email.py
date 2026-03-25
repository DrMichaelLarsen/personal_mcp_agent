from __future__ import annotations

from app.schemas.email import ProcessEmailsInput


def register(server, container) -> None:
    @server.tool(name="process_tagged_emails", description="Read configured tagged emails, classify them, extract tasks/notes/events, and optionally commit them.")
    async def process_tagged_emails_tool(arguments: dict):
        return container.process_emails_workflow.run(ProcessEmailsInput.model_validate(arguments)).model_dump()

    @server.tool(name="preview_tagged_emails", description="Preview processing of configured tagged emails without committing writes.")
    async def preview_tagged_emails_tool(arguments: dict):
        payload = ProcessEmailsInput.model_validate({**arguments, "preview_only": True})
        return container.process_emails_workflow.run(payload).model_dump()

    @server.tool(name="get_unprocessed_tagged_emails", description="List candidate tagged emails that have not been processed yet.")
    async def get_unprocessed_tagged_emails_tool(max_count: int = 10, query: str | None = None):
        return [email.model_dump() for email in container.email_service.get_unprocessed_tagged_emails(max_count, query)]
