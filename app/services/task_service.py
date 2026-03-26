from __future__ import annotations

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.tasks import TaskCreateInput, TaskRecord, TaskResult, TaskUpdateInput
from app.utils.confidence import build_confidence

from app.services.matching_service import MatchingService
from app.services.project_service import ProjectService


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
            match = self.matching_service.match_project(data.project_name, self.project_service.list_projects())
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

        notes_property_value = notes_value if cfg.store_content_in_property else None

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
            cfg.notes_property: notes_property_value,
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
                cfg.notes_property: data.notes,
                cfg.tags_property: data.tags,
                cfg.status_property: data.status,
                cfg.relation_property: data.project_id,
                cfg.phone_property: data.phone,
                cfg.budget_property: data.budget,
                cfg.goal_property: data.goal_id,
                cfg.parent_property: data.parent_id,
                cfg.dependency_of_property: data.dependency_of_ids,
                cfg.depends_on_property: data.depends_on_ids,
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

    def _to_record(self, raw: dict) -> TaskRecord:
        props = raw.get("properties", {})
        cfg = self.settings.tasks_db

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

        return TaskRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            status=props.get(cfg.status_property) if cfg.status_property else None,
            scheduled=props.get(cfg.scheduled_property) if cfg.scheduled_property else None,
            deadline=props.get(cfg.deadline_property) if cfg.deadline_property else None,
            estimated_minutes=props.get(cfg.estimate_property) if cfg.estimate_property else None,
            importance=props.get(cfg.importance_property) if cfg.importance_property else None,
            project_id=props.get(cfg.relation_property) if cfg.relation_property else None,
            project_title=props.get("Project Title"),
            contexts=_as_list(props.get(cfg.contexts_property)) if cfg.contexts_property else [],
            assigned_to=_as_list(props.get(cfg.assigned_property)) if cfg.assigned_property else [],
            tags=_as_list(props.get(cfg.tags_property)) if cfg.tags_property else [],
            source_url=_as_str(props.get(cfg.url_property)) if cfg.url_property else None,
            notes=props.get(cfg.notes_property) if cfg.notes_property else None,
            phone=_as_str(props.get(cfg.phone_property)) if cfg.phone_property else None,
            budget=props.get(cfg.budget_property) if cfg.budget_property else None,
            goal_id=props.get(cfg.goal_property) if cfg.goal_property else None,
            parent_id=props.get(cfg.parent_property) if cfg.parent_property else None,
            dependency_of_ids=_as_list(props.get(cfg.dependency_of_property)) if cfg.dependency_of_property else [],
            depends_on_ids=_as_list(props.get(cfg.depends_on_property)) if cfg.depends_on_property else [],
            score=props.get(cfg.score_property) if cfg.score_property else None,
            ai_cost=props.get(cfg.ai_cost_property) if cfg.ai_cost_property else None,
            raw=raw,
        )
