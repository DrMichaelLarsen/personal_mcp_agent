from __future__ import annotations

import re
from datetime import date, timedelta

from app.schemas.projects import ProjectCreateInput
from app.schemas.tasks import ProcessTaskInboxResult, TaskInboxItemResult, TaskUpdateInput
from app.workflows.process_task_inbox.state import ProcessTaskInboxState


def _extract_explicit_project_name(text: str) -> str | None:
    patterns = [
        r"new\s+project\s*[:\-]\s*([^\n\r\.]{3,80})",
        r"project\s+name\s*[:\-]\s*([^\n\r\.]{3,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip(" :-\t")
    return None


def _infer_importance(text: str) -> int:
    lowered = text.lower()
    if any(token in lowered for token in ["urgent", "asap", "immediately", "critical"]):
        return 175
    if any(token in lowered for token in ["important", "deadline", "due", "priority"]):
        return 120
    return 50


def _infer_context_names(text: str) -> list[str]:
    lowered = text.lower()
    contexts: list[str] = []
    if any(token in lowered for token in ["call", "phone", "voicemail"]):
        contexts.append("Phone")
    if any(token in lowered for token in ["home", "house", "errand", "store", "pickup"]):
        contexts.append("Home")
    if any(token in lowered for token in ["email", "doc", "review", "submit", "send", "online", "computer"]):
        contexts.append("Computer")
    if not contexts:
        contexts.append("Computer")
    deduped: list[str] = []
    for item in contexts:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _infer_dates(text: str, importance: int) -> tuple[str | None, str | None]:
    lowered = text.lower()
    explicit = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    scheduled = None
    deadline = None
    if explicit:
        if re.search(r"\b(due|deadline|by)\b", lowered):
            deadline = explicit[0]
            scheduled = explicit[0]
        else:
            scheduled = explicit[0]
    if not scheduled and "tomorrow" in lowered:
        scheduled = (date.today() + timedelta(days=1)).isoformat()
    if not scheduled and "today" in lowered:
        scheduled = date.today().isoformat()
    if not scheduled and not deadline:
        if importance >= 150:
            offset = 1
        elif importance >= 100:
            offset = 3
        elif importance >= 75:
            offset = 7
        else:
            offset = 14
        scheduled = (date.today() + timedelta(days=offset)).isoformat()
        deadline = None
    return scheduled, deadline


def _infer_estimate_minutes(text: str) -> int:
    match = re.search(r"\b(\d{1,3})\s*(min|mins|minute|minutes|m|hr|hrs|hour|hours)\b", text, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit in {"hr", "hrs", "hour", "hours"}:
            return min(8 * 60, value * 60)
        return max(5, value)
    lowered = text.lower()
    if any(token in lowered for token in ["quick", "brief", "follow up", "reply"]):
        return 30
    return 45


def fetch_tasks(state: ProcessTaskInboxState, deps: dict) -> ProcessTaskInboxState:
    request = deps["request"]
    task_service = deps["task_service"]
    tasks = task_service.list_inbox_candidates(
        max_count=request.max_count,
        include_statuses=request.include_statuses,
        processed_tag=request.processed_tag,
    )
    return {**state, "tasks": tasks}


def enrich_tasks(state: ProcessTaskInboxState, deps: dict) -> ProcessTaskInboxState:
    request = deps["request"]
    preview_only = request.preview_only
    task_service = deps["task_service"]
    project_service = deps["project_service"]
    matching_service = deps["matching_service"]

    contexts = project_service.list_contexts()
    active_projects = project_service.list_active_projects()
    results: list[TaskInboxItemResult] = []

    for task in state.get("tasks", []):
        content = f"{task.title}\n\n{task.notes or ''}"
        changed: dict = {}
        review_items = []
        created_project_id = None
        project_id = task.project_id

        if not project_id:
            explicit_name = _extract_explicit_project_name(content)
            if explicit_name:
                existing = next((p for p in active_projects if p.title.strip().lower() == explicit_name.strip().lower()), None)
                if existing:
                    project_id = existing.id
                elif not preview_only:
                    created = project_service.create_project(ProjectCreateInput(title=explicit_name, tags=[project_service.settings.review_project_tag]))
                    project_id = created.id
                    created_project_id = created.id
                    active_projects.append(created)
            if not project_id:
                token = task.title.split(":")[0].strip() if ":" in task.title else task.title
                match = matching_service.match_project(token, active_projects, metadata={"source": "task_inbox", "task_id": task.id})
                if match.matched and match.selected_project:
                    project_id = match.selected_project.id
                else:
                    review_items.extend(match.review_items)
        if project_id and project_id != task.project_id:
            changed["project_id"] = project_id

        importance = task.importance if task.importance is not None else _infer_importance(content)
        if task.importance is None:
            changed["importance"] = importance

        if not task.contexts:
            requested = _infer_context_names(content)
            matched_contexts, context_reviews = matching_service.match_contexts(
                requested,
                contexts,
                metadata={"source": "task_inbox", "task_id": task.id},
            )
            review_items.extend(context_reviews)
            changed["contexts"] = matched_contexts or requested

        inferred_scheduled, inferred_deadline = _infer_dates(content, importance)
        if not task.scheduled and inferred_scheduled:
            changed["scheduled"] = inferred_scheduled
        if not task.deadline and inferred_deadline:
            changed["deadline"] = inferred_deadline

        if task.estimated_minutes is None:
            changed["estimated_minutes"] = _infer_estimate_minutes(content)

        current_tags = list(task.tags or [])
        if request.processed_tag not in current_tags:
            changed["tags"] = [*current_tags, request.processed_tag]

        updated = False
        if changed and not preview_only:
            task_service.update_task(
                TaskUpdateInput(
                    task_id=task.id,
                    project_id=changed.get("project_id"),
                    importance=changed.get("importance"),
                    contexts=changed.get("contexts"),
                    scheduled=changed.get("scheduled"),
                    deadline=changed.get("deadline"),
                    estimated_minutes=changed.get("estimated_minutes"),
                    tags=changed.get("tags"),
                )
            )
            updated = True

        results.append(
            TaskInboxItemResult(
                task_id=task.id,
                updated=updated,
                created_project_id=created_project_id,
                changed_fields=changed,
                review_items=review_items,
            )
        )
    return {**state, "results": results}


def build_result(state: ProcessTaskInboxState, deps: dict) -> ProcessTaskInboxState:
    request = deps["request"]
    results = state.get("results", [])
    final = ProcessTaskInboxResult(
        preview_only=request.preview_only,
        processed_count=len(results),
        updated_count=sum(1 for item in results if item.updated),
        created_projects=sum(1 for item in results if item.created_project_id),
        results=results,
    )
    return {**state, "result": final}
