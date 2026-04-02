from __future__ import annotations

import logging

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.tasks import TaskCreateInput, TaskRecord, TaskResult, TaskUpdateInput
from app.utils.confidence import build_confidence
from app.utils.text import similarity

from app.services.matching_service import MatchingService
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(
        self,
        notion_client: NotionClient,
        project_service: ProjectService,
        matching_service: MatchingService,
        settings: Settings,
    ):
        self.notion = notion_client
        self.project_service = project_service
        self.matching_service = matching_service
        self.settings = settings

    def create_task(self, data: TaskCreateInput) -> TaskResult:
        cfg = self.settings.tasks_db
        project_id = data.project_id
        project_title = None
        review_items = []
        confidence = build_confidence(1.0, "Task created with explicit input.", False)

        if data.scheduled and data.deadline and data.scheduled > data.deadline:
            return TaskResult(
                created=False,
                confidence=build_confidence(0.2, "Scheduled date cannot be later than deadline.", True),
                message="Scheduled date cannot be later than deadline.",
            )

        if not project_id and data.project_name:
            match = self.matching_service.match_project(data.project_name, self.project_service.list_active_projects())
            confidence = match.confidence
            review_items.extend(match.review_items)
            if match.matched and match.selected_project:
                project_id = match.selected_project.id
                project_title = match.selected_project.title
            elif not cfg.allow_tasks_without_project:
                return TaskResult(created=False, confidence=confidence, review_items=review_items, message="Project selection requires review.")

        notes_value = data.notes
        if data.ai_cost_summary:
            notes_value = f"{notes_value}\n\n{data.ai_cost_summary}" if notes_value else data.ai_cost_summary

        properties = {
            cfg.title_property: data.title,
            cfg.status_property: data.status or cfg.default_status,
            cfg.importance_property: data.importance if data.importance is not None else cfg.default_importance,
            cfg.scheduled_property: data.scheduled,
            cfg.deadline_property: data.deadline,
            cfg.estimate_property: data.estimated_minutes,
            cfg.relation_property: project_id,
            cfg.contexts_property: data.contexts,
            cfg.assigned_property: data.assigned_to,
            cfg.url_property: data.source_url,
            cfg.tags_property: data.tags,
            cfg.phone_property: data.phone,
            cfg.budget_property: data.budget,
            cfg.goal_property: data.goal_id,
            cfg.parent_property: data.parent_id,
            cfg.dependency_of_property: data.dependency_of_ids,
            cfg.depends_on_property: data.depends_on_ids,
            cfg.ai_cost_property: data.ai_cost,
        }
        page_properties = {k: v for k, v in properties.items() if k is not None}
        children = self.notion.markdown_to_blocks(notes_value) if notes_value else None
        raw = self.notion.create_page(cfg.database_id, page_properties, children=children)
        task = self._to_record(raw)
        if project_title and not task.project_title:
            task.project_title = project_title
        return TaskResult(created=True, task=task, confidence=confidence, review_items=review_items, message="Task created.")

    def update_task(self, data: TaskUpdateInput) -> TaskResult:
        cfg = self.settings.tasks_db
        if data.scheduled and data.deadline and data.scheduled > data.deadline:
            return TaskResult(
                created=False,
                confidence=build_confidence(0.2, "Scheduled date cannot be later than deadline.", True),
                message="Scheduled date cannot be later than deadline.",
            )
        properties = {
            key: value
            for key, value in {
                cfg.title_property: data.title,
                cfg.contexts_property: data.contexts,
                cfg.scheduled_property: data.scheduled,
                cfg.deadline_property: data.deadline,
                cfg.estimate_property: data.estimated_minutes,
                cfg.importance_property: data.importance,
                cfg.assigned_property: data.assigned_to,
                cfg.tags_property: data.tags,
                cfg.status_property: data.status,
                cfg.relation_property: data.project_id,
                cfg.phone_property: data.phone,
                cfg.budget_property: data.budget,
                cfg.goal_property: data.goal_id,
                cfg.parent_property: data.parent_id,
                cfg.dependency_of_property: data.dependency_of_ids,
                cfg.depends_on_property: data.depends_on_ids,
                cfg.ai_cost_property: data.ai_cost,
            }.items()
            if key is not None and value is not None
        }
        raw = self.notion.update_page(data.task_id, properties)
        return TaskResult(created=False, task=self._to_record(raw), confidence=build_confidence(1.0, "Task updated.", False), message="Task updated.")

    def get_task(self, task_id: str) -> TaskRecord:
        return self._to_record(self.notion.get_page(task_id))

    def list_tasks_for_today(self, day: str) -> list[TaskRecord]:
        cfg = self.settings.tasks_db
        filters = {cfg.scheduled_property: day} if cfg.scheduled_property else None
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id, filters)]
        filtered = [item for item in items if item.status != "Complete"]
        return sorted(filtered, key=lambda item: item.score or 0, reverse=True)

    def list_tasks_for_project(self, project_id: str) -> list[TaskRecord]:
        cfg = self.settings.tasks_db
        filters = {cfg.relation_property: project_id} if cfg.relation_property else None
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id, filters)]
        filtered = [item for item in items if item.status != "Complete"]
        return sorted(filtered, key=lambda item: item.score or 0, reverse=True)

    def list_open_tasks(self, project_id: str | None = None) -> list[TaskRecord]:
        cfg = self.settings.tasks_db
        filters = {cfg.relation_property: project_id} if project_id and cfg.relation_property else None
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id, filters)]
        return [item for item in items if item.status != "Complete"]

    def list_inbox_candidates(
        self,
        max_count: int = 50,
        include_statuses: list[str] | None = None,
        processed_tag: str = "Inbox Processed",
        inbox_formula_property: str | None = "Inbox",
    ) -> list[TaskRecord]:
        cfg = self.settings.tasks_db
        include = {(item or "").strip().lower() for item in (include_statuses or ["Inbox"]) if (item or "").strip()}
        processed_key = (processed_tag or "").strip().lower()
        inbox_formula_key = (inbox_formula_property or "").strip()
        all_items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        candidates: list[TaskRecord] = []
        status_filtered_count = 0
        inbox_filtered_count = 0
        processed_filtered_count = 0
        for task in all_items:
            status_key = (task.status or "").strip().lower()
            if include and status_key not in include:
                status_filtered_count += 1
                continue
            if inbox_formula_key:
                inbox_value = (task.raw.get("properties", {}) or {}).get(inbox_formula_key)
                if not bool(inbox_value):
                    inbox_filtered_count += 1
                    continue
            tag_keys = {(tag or "").strip().lower() for tag in (task.tags or []) if (tag or "").strip()}
            if processed_key and processed_key in tag_keys:
                processed_filtered_count += 1
                continue
            candidates.append(task)
        limited = candidates[: max(1, max_count)]
        logger.info(
            "Selected task inbox candidates.",
            extra={
                "event": "service.task.list_inbox_candidates",
                "context": {
                    "total_tasks": len(all_items),
                    "status_filtered": status_filtered_count,
                    "inbox_formula_filtered": inbox_filtered_count,
                    "processed_filtered": processed_filtered_count,
                    "eligible_count": len(candidates),
                    "returned_count": len(limited),
                    "max_count": max_count,
                    "include_statuses": sorted(list(include)),
                    "inbox_formula_property": inbox_formula_property,
                    "processed_tag": processed_tag,
                },
            },
        )
        return limited

    def find_similar_open_task(self, title: str, project_id: str | None = None) -> tuple[TaskRecord | None, float]:
        def _normalize_candidate(value: str) -> str:
            lowered = value.strip()
            # Common email reminder prefixes should not block duplicate detection.
            prefixes = ["reminder:", "re:", "fw:", "fwd:", "follow up:", "follow-up:"]
            changed = True
            while changed:
                changed = False
                compact = lowered.lower()
                for prefix in prefixes:
                    if compact.startswith(prefix):
                        lowered = lowered[len(prefix):].strip()
                        changed = True
                        break
            return lowered

        candidates = self.list_open_tasks(project_id=project_id)
        best_task: TaskRecord | None = None
        best_score = 0.0
        normalized_title = _normalize_candidate(title)
        for task in candidates:
            raw_score = similarity(title, task.title)
            normalized_score = similarity(normalized_title, _normalize_candidate(task.title))
            score = max(raw_score, normalized_score)
            if score > best_score:
                best_score = score
                best_task = task
        return best_task, best_score

    def _to_record(self, raw: dict) -> TaskRecord:
        props = raw.get("properties", {})
        cfg = self.settings.tasks_db

        def _as_single_id(value):
            if isinstance(value, list):
                if not value:
                    return None
                first = value[0]
                return first if isinstance(first, str) else str(first)
            if value is None:
                return None
            return value if isinstance(value, str) else str(value)

        def _as_list(value):
            if value is None:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                return [value]
            return []

        def _as_str(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                if "phone_number" in value:
                    return value.get("phone_number")
                if "url" in value:
                    return value.get("url")
                if "email" in value:
                    return value.get("email")
                return None
            return str(value)

        def _notes_from_children() -> str | None:
            children = raw.get("children") or []
            if not isinstance(children, list):
                return None
            lines: list[str] = []
            for block in children:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    block_type = block.get("type")
                    if block_type == "heading_1":
                        lines.append(f"# {text}")
                    elif block_type == "heading_2":
                        lines.append(f"## {text}")
                    elif block_type == "heading_3":
                        lines.append(f"### {text}")
                    elif block_type == "to_do":
                        checked = bool(block.get("checked", False))
                        lines.append(f"- [{'x' if checked else ' '}] {text}")
                    elif block_type == "bulleted_list_item":
                        lines.append(f"- {text}")
                    elif block_type == "numbered_list_item":
                        lines.append(f"1. {text}")
                    else:
                        lines.append(text)
            return "\n".join(lines) if lines else None

        notes_value = props.get(cfg.notes_property) if cfg.notes_property else None
        if not notes_value:
            notes_value = _notes_from_children()

        return TaskRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            status=props.get(cfg.status_property) if cfg.status_property else None,
            scheduled=props.get(cfg.scheduled_property) if cfg.scheduled_property else None,
            deadline=props.get(cfg.deadline_property) if cfg.deadline_property else None,
            estimated_minutes=props.get(cfg.estimate_property) if cfg.estimate_property else None,
            importance=props.get(cfg.importance_property) if cfg.importance_property else None,
            project_id=_as_single_id(props.get(cfg.relation_property)) if cfg.relation_property else None,
            project_title=props.get("Project Title"),
            contexts=_as_list(props.get(cfg.contexts_property)) if cfg.contexts_property else [],
            assigned_to=_as_list(props.get(cfg.assigned_property)) if cfg.assigned_property else [],
            tags=_as_list(props.get(cfg.tags_property)) if cfg.tags_property else [],
            source_url=_as_str(props.get(cfg.url_property)) if cfg.url_property else None,
            notes=notes_value,
            phone=_as_str(props.get(cfg.phone_property)) if cfg.phone_property else None,
            budget=props.get(cfg.budget_property) if cfg.budget_property else None,
            goal_id=_as_single_id(props.get(cfg.goal_property)) if cfg.goal_property else None,
            parent_id=_as_single_id(props.get(cfg.parent_property)) if cfg.parent_property else None,
            dependency_of_ids=_as_list(props.get(cfg.dependency_of_property)) if cfg.dependency_of_property else [],
            depends_on_ids=_as_list(props.get(cfg.depends_on_property)) if cfg.depends_on_property else [],
            score=props.get(cfg.score_property) if cfg.score_property else None,
            ai_cost=props.get(cfg.ai_cost_property) if cfg.ai_cost_property else None,
            raw=raw,
        )
