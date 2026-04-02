from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from app.schemas.common import ReviewItem
from app.schemas.projects import ProjectCreateInput
from app.schemas.tasks import ProcessTaskInboxResult, TaskInboxItemResult, TaskUpdateInput
from app.utils.confidence import build_confidence
from app.workflows.process_task_inbox.state import ProcessTaskInboxState

logger = logging.getLogger(__name__)


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


def _llm_enrichment_for_task(task, deps: dict) -> tuple[dict, list[ReviewItem]]:
    llm = deps.get("llm_client")
    settings = deps.get("settings")
    cost_service = deps.get("cost_service")
    if not llm or not settings or not settings.llm.enabled or not settings.llm.use_for_task_inbox:
        return {}, []
    tier = settings.llm.task_inbox_tier
    model = cost_service.get_tier_model(tier) if cost_service else settings.llm.standard_model
    system_prompt = (
        "You enrich Notion task inbox items. Return strict JSON keys only: "
        "importance (int 0-200 or null), contexts (array of short strings), scheduled (YYYY-MM-DD or null), "
        "deadline (YYYY-MM-DD or null), estimated_minutes (int or null), project_name (string or null), rationale (array of strings)."
    )
    user_prompt = json.dumps(
        {
            "task_id": task.id,
            "title": task.title,
            "notes": task.notes,
            "existing": {
                "importance": task.importance,
                "contexts": task.contexts,
                "scheduled": task.scheduled,
                "deadline": task.deadline,
                "estimated_minutes": task.estimated_minutes,
                "project_id": task.project_id,
            },
        }
    )
    try:
        data = llm.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            operation="task_inbox_enrichment",
            metadata={"task_id": task.id},
        )
    except Exception as exc:
        logger.warning(
            "Task inbox LLM enrichment failed; using deterministic fallback.",
            extra={
                "event": "workflow.process_task_inbox.enrich.llm_failed",
                "context": {"task_id": task.id, "error": str(exc), "tier": tier, "model": model},
            },
        )
        return {}, [
            ReviewItem(
                item_type="task_inbox_llm",
                reason="LLM enrichment failed; deterministic fallback used.",
                options=[{"task_id": task.id}],
                confidence=build_confidence(0.4, "Task enrichment fell back to deterministic rules.", True),
            )
        ]
    logger.info(
        "Task inbox LLM enrichment completed.",
        extra={
            "event": "workflow.process_task_inbox.enrich.llm_complete",
            "context": {"task_id": task.id, "model": model, "returned_keys": sorted(list((data or {}).keys()))},
        },
    )
    return data or {}, []


def fetch_tasks(state: ProcessTaskInboxState, deps: dict) -> ProcessTaskInboxState:
    request = deps["request"]
    task_service = deps["task_service"]
    tasks = task_service.list_inbox_candidates(
        max_count=request.max_count,
        include_statuses=request.include_statuses,
        processed_tag=request.processed_tag,
    )
    logger.info(
        "Fetched task inbox candidates from Notion.",
        extra={
            "event": "workflow.process_task_inbox.fetch.complete",
            "context": {
                "candidate_count": len(tasks),
                "max_count": request.max_count,
                "include_statuses": request.include_statuses,
                "processed_tag": request.processed_tag,
            },
        },
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
    logger.info(
        "Starting task inbox enrichment stage.",
        extra={
            "event": "workflow.process_task_inbox.enrich.start",
            "context": {
                "task_count": len(state.get("tasks", [])),
                "preview_only": preview_only,
                "context_count": len(contexts),
                "active_project_count": len(active_projects),
            },
        },
    )

    for task in state.get("tasks", []):
        content = f"{task.title}\n\n{task.notes or ''}"
        changed: dict = {}
        review_items = []
        created_project_id = None
        project_id = task.project_id

        llm_data, llm_reviews = _llm_enrichment_for_task(task, deps)
        review_items.extend(llm_reviews)

        if not project_id:
            explicit_name = _extract_explicit_project_name(content) or (llm_data.get("project_name") if isinstance(llm_data.get("project_name"), str) else None)
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

        llm_importance = llm_data.get("importance") if isinstance(llm_data, dict) else None
        if isinstance(llm_importance, bool):
            llm_importance = None
        if not isinstance(llm_importance, int):
            llm_importance = None
        default_importance = project_service.settings.tasks_db.default_importance
        treat_importance_as_missing = task.importance is None or task.importance == default_importance
        importance = (llm_importance if llm_importance is not None else _infer_importance(content)) if treat_importance_as_missing else (task.importance or default_importance)
        if treat_importance_as_missing:
            changed["importance"] = importance

        if not task.contexts:
            llm_contexts = llm_data.get("contexts") if isinstance(llm_data.get("contexts"), list) else None
            requested = [c for c in (llm_contexts or _infer_context_names(content)) if isinstance(c, str) and c.strip()]
            matched_contexts, context_reviews = matching_service.match_contexts(
                requested,
                contexts,
                metadata={"source": "task_inbox", "task_id": task.id},
            )
            review_items.extend(context_reviews)
            changed["contexts"] = matched_contexts or requested

        llm_scheduled = llm_data.get("scheduled") if isinstance(llm_data.get("scheduled"), str) else None
        llm_deadline = llm_data.get("deadline") if isinstance(llm_data.get("deadline"), str) else None
        inferred_scheduled, inferred_deadline = _infer_dates(content, importance)
        if not task.scheduled and inferred_scheduled:
            changed["scheduled"] = llm_scheduled or inferred_scheduled
        if not task.deadline and inferred_deadline:
            changed["deadline"] = llm_deadline or inferred_deadline
        elif not task.deadline and llm_deadline:
            changed["deadline"] = llm_deadline

        if task.estimated_minutes is None:
            llm_estimated = llm_data.get("estimated_minutes") if isinstance(llm_data.get("estimated_minutes"), int) else None
            changed["estimated_minutes"] = llm_estimated if llm_estimated else _infer_estimate_minutes(content)

        cost_service = deps.get("cost_service")
        ai_cost_value = None
        if cost_service:
            ai_summary = cost_service.summarize_recent_usage(
                event_count=0,
                operation_prefix="task_inbox_enrichment",
                metadata_filter={"task_id": task.id},
            )
            ai_cost_value = float(ai_summary.get("total_estimated_cost", 0.0) or 0.0)
            if ai_cost_value > 0:
                changed["ai_cost"] = round((task.ai_cost or 0.0) + ai_cost_value, 8)

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
                    ai_cost=changed.get("ai_cost"),
                )
            )
            updated = True

        logger.info(
            "Processed task inbox candidate.",
            extra={
                "event": "workflow.process_task_inbox.enrich.task_processed",
                "context": {
                    "task_id": task.id,
                    "updated": updated,
                    "created_project": bool(created_project_id),
                    "changed_fields": sorted(list(changed.keys())),
                    "review_item_count": len(review_items),
                },
            },
        )

        results.append(
            TaskInboxItemResult(
                task_id=task.id,
                updated=updated,
                created_project_id=created_project_id,
                changed_fields=changed,
                review_items=review_items,
            )
        )
    logger.info(
        "Completed task inbox enrichment stage.",
        extra={
            "event": "workflow.process_task_inbox.enrich.complete",
            "context": {
                "processed_count": len(results),
                "updated_count": sum(1 for item in results if item.updated),
                "created_projects": sum(1 for item in results if item.created_project_id),
                "review_item_count": sum(len(item.review_items) for item in results),
            },
        },
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
    logger.info(
        "Built process_task_inbox final result.",
        extra={
            "event": "workflow.process_task_inbox.result.built",
            "context": {
                "preview_only": final.preview_only,
                "processed_count": final.processed_count,
                "updated_count": final.updated_count,
                "created_projects": final.created_projects,
            },
        },
    )
    return {**state, "result": final}
