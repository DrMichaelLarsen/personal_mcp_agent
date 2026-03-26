from __future__ import annotations

import json
import re

from app.adapters.llm_client import LLMClient
from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.services.cost_service import CostService
from app.schemas.common import ConfidenceInfo, ReviewItem
from app.schemas.projects import AreaRecord, ContextRecord, ProjectCreateInput, ProjectRecord
from app.utils.confidence import build_confidence
from app.utils.text import similarity


class ProjectService:
    def __init__(
        self,
        notion_client: NotionClient,
        settings: Settings,
        llm_client: LLMClient | None = None,
        cost_service: CostService | None = None,
    ):
        self.notion = notion_client
        self.settings = settings
        self.llm = llm_client
        self.cost_service = cost_service

    def create_project(self, data: ProjectCreateInput) -> ProjectRecord:
        cfg = self.settings.projects_db
        area_id = data.area_id
        if not area_id:
            area_query = data.area_name or data.title
            match, _, _ = self.match_area_name(area_query)
            area_id = match.id if match else None
        if cfg.require_area and not area_id:
            raise ValueError("Projects require an Area relation.")
        properties = {
            cfg.title_property: data.title,
            cfg.status_property: data.status or cfg.default_status,
            cfg.area_property: area_id,
            cfg.parent_project_property: data.parent_project_id,
            cfg.target_deadline_property: data.target_deadline,
            cfg.importance_property: data.importance if data.importance is not None else cfg.default_importance,
            cfg.priority_checkbox_property: bool(data.priority) if data.priority is not None else False,
            cfg.budget_property: data.budget,
            cfg.notes_property: data.notes,
            cfg.tags_property: data.tags,
        }
        raw = self.notion.create_page(cfg.database_id, {k: v for k, v in properties.items() if k is not None})
        return self._to_record(raw)

    def get_project(self, project_id: str) -> ProjectRecord:
        return self._to_record(self.notion.get_page(project_id))

    def list_projects(self) -> list[ProjectRecord]:
        cfg = self.settings.projects_db
        items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        area_lookup = {area.id: area.path for area in self.list_areas()}
        by_id = {item.id: item for item in items}

        def _project_path(project: ProjectRecord) -> str:
            parts = [project.title]
            seen = {project.id}
            parent_id = project.parent_project_id
            while parent_id and parent_id in by_id and parent_id not in seen:
                seen.add(parent_id)
                parent = by_id[parent_id]
                parts.append(parent.title)
                parent_id = parent.parent_project_id
            return " / ".join(reversed(parts))

        for item in items:
            item.area_path = area_lookup.get(item.area_id) if item.area_id else None
            item.project_path = _project_path(item)
        return sorted(items, key=lambda item: item.score or 0, reverse=True)

    def list_active_projects(self) -> list[ProjectRecord]:
        completed = {status.strip().lower() for status in self.settings.project_completed_statuses}
        return [project for project in self.list_projects() if (project.status or "").strip().lower() not in completed]

    def list_contexts(self) -> list[ContextRecord]:
        cfg = self.settings.contexts_db
        if not cfg.database_id:
            return []
        active_values = {status.strip().lower() for status in self.settings.context_active_statuses}

        def _as_text(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                text_items = [item for item in value if isinstance(item, str)]
                return " | ".join(text_items) if text_items else None
            if isinstance(value, dict):
                for key in ("name", "title", "text", "plain_text"):
                    part = value.get(key)
                    if isinstance(part, str):
                        return part
                return None
            return str(value)

        records: list[ContextRecord] = []
        for raw in self.notion.query_database(cfg.database_id):
            props = raw.get("properties", {})
            title = props.get(cfg.title_property) or raw.get("title", "")
            raw_description = props.get(cfg.description_property) if cfg.description_property else None
            if raw_description is None and cfg.notes_property:
                raw_description = props.get(cfg.notes_property)
            description = _as_text(raw_description)
            status = props.get(cfg.status_property) if cfg.status_property else None
            status_value = (status or "").strip().lower()
            if active_values and status_value and status_value not in active_values:
                continue
            records.append(ContextRecord(id=raw["id"], title=title, description=description, status=status, raw=raw))
        return records

    def list_areas(self) -> list[AreaRecord]:
        cfg = self.settings.areas_db
        if not cfg.database_id:
            return []
        active_values = {status.strip().lower() for status in self.settings.area_active_statuses}

        def _as_single_id(value):
            if isinstance(value, list):
                if not value:
                    return None
                first = value[0]
                return first if isinstance(first, str) else str(first)
            return value

        records: list[AreaRecord] = []
        for raw in self.notion.query_database(cfg.database_id):
            props = raw.get("properties", {})
            title = props.get(cfg.title_property) or raw.get("title", "")
            status = props.get(cfg.status_property) if cfg.status_property else None
            status_value = (status or "").strip().lower()
            if active_values and status_value and status_value not in active_values:
                continue
            records.append(
                AreaRecord(
                    id=raw["id"],
                    title=title,
                    parent_area_id=_as_single_id(props.get(cfg.parent_property)) if cfg.parent_property else None,
                    status=status,
                    raw=raw,
                )
            )
        by_id = {item.id: item for item in records}

        def _path(item: AreaRecord) -> str:
            parts = [item.title]
            seen = {item.id}
            parent_id = item.parent_area_id
            while parent_id and parent_id in by_id and parent_id not in seen:
                seen.add(parent_id)
                parent = by_id[parent_id]
                parts.append(parent.title)
                parent_id = parent.parent_area_id
            return " / ".join(reversed(parts))

        for item in records:
            item.path = _path(item)
        return records

    def match_area_name(self, area_name: str) -> tuple[AreaRecord | None, list[ReviewItem], ConfidenceInfo]:
        areas = self.list_areas()
        if not areas:
            confidence = build_confidence(0.0, "No active areas available for matching.", True)
            return None, [], confidence
        scored = sorted([(area, similarity(area_name, area.path or area.title)) for area in areas], key=lambda item: item[1], reverse=True)
        best_area, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        if best_score >= self.settings.confidence.auto_create and (best_score - second_score >= 0.08):
            return best_area, [], build_confidence(best_score, f"Matched area '{best_area.path or best_area.title}'.", False)
        review = ReviewItem(
            item_type="area_match",
            reason="Area matching was ambiguous.",
            options=[{"id": area.id, "title": area.title, "path": area.path, "score": round(score, 3)} for area, score in scored[:5]],
            confidence=build_confidence(best_score, "Top area candidate requires review.", True),
        )
        if self._can_use_ambiguous_llm():
            options = [area.path or area.title for area, _ in scored[:5]]
            selected = self._llm_select_best(area_name, options)
            if selected:
                winner = next((area for area, _ in scored if (area.path or area.title) == selected), None)
                if winner:
                    return winner, [], build_confidence(0.8, f"LLM disambiguated area to '{selected}'.", False)
        return None, [review], review.confidence

    def _can_use_ambiguous_llm(self) -> bool:
        return bool(self.llm and self.settings.llm.enabled and self.settings.llm.use_for_ambiguous_matching)

    def _llm_select_best(self, query: str, options: list[str]) -> str | None:
        if not self.llm or not options:
            return None
        result = self.llm.chat_json(
            system_prompt=(
                "Choose the single best option for the query. "
                "Return JSON with key 'selected'. If none fit, return selected as empty string."
            ),
            user_prompt=json.dumps({"query": query, "options": options}),
            model=self._model_for_ambiguous_matching(),
            operation="ambiguous_area_match",
        )
        selected = (result.get("selected") or "").strip()
        return selected if selected in options else None

    def _model_for_ambiguous_matching(self) -> str:
        tier = self.settings.llm.ambiguous_matching_tier
        if self.cost_service:
            return self.cost_service.get_tier_model(tier)
        if tier == "fast":
            return self.settings.llm.cheap_model
        if tier == "balanced":
            return self.settings.llm.standard_model
        if tier == "smart":
            return self.settings.llm.premium_model
        return self.settings.llm.best_model or self.settings.llm.premium_model

    def _to_record(self, raw: dict) -> ProjectRecord:
        props = raw.get("properties", {})
        cfg = self.settings.projects_db

        def _as_single_id(value):
            if isinstance(value, list):
                if not value:
                    return None
                first = value[0]
                return first if isinstance(first, str) else str(first)
            return value

        def _as_text(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                text_items = [item for item in value if isinstance(item, str)]
                return " | ".join(text_items) if text_items else None
            if isinstance(value, dict):
                for key in ("name", "title", "text", "plain_text"):
                    part = value.get(key)
                    if isinstance(part, str):
                        return part
                return None
            return str(value)

        def _looks_like_page_id(value: str) -> bool:
            # Support both UUID-style Notion IDs and short fake IDs used in tests.
            return bool(
                re.match(r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|page-\d+)$", value, re.IGNORECASE)
            )

        def _list_is_relation_ids(value) -> bool:
            return isinstance(value, list) and bool(value) and all(isinstance(item, str) and _looks_like_page_id(item.strip()) for item in value)

        def _description_from_related_notes(value):
            if not isinstance(value, list):
                return None
            note_ids = [item for item in value if isinstance(item, str) and item.strip()]
            if not note_ids:
                return None
            snippets: list[str] = []
            for note_id in note_ids[:5]:
                try:
                    note_raw = self.notion.get_page(note_id)
                except Exception:
                    continue
                note_props = note_raw.get("properties", {})
                note_text = _as_text(note_props.get(self.settings.notes_db.notes_property)) if self.settings.notes_db.notes_property else None
                if not note_text:
                    note_text = _as_text(note_props.get(self.settings.notes_db.title_property)) or _as_text(note_raw.get("title"))
                if note_text:
                    snippets.append(note_text)
            return " | ".join(snippets) if snippets else None

        raw_description = props.get(cfg.description_property) if cfg.description_property else None
        if raw_description is None and cfg.notes_property:
            raw_description = props.get(cfg.notes_property)
        description = _as_text(raw_description)
        if _list_is_relation_ids(raw_description):
            description = None
        if description is None and cfg.notes_property and cfg.description_property != cfg.notes_property:
            relation_value = props.get(cfg.notes_property)
            description = _description_from_related_notes(relation_value) or _as_text(relation_value)

        return ProjectRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            status=props.get(cfg.status_property) if cfg.status_property else None,
            description=description,
            area_id=_as_single_id(props.get(cfg.area_property)) if cfg.area_property else None,
            parent_project_id=_as_single_id(props.get(cfg.parent_project_property)) if cfg.parent_project_property else None,
            target_deadline=props.get(cfg.target_deadline_property) if cfg.target_deadline_property else None,
            importance=props.get(cfg.importance_property) if cfg.importance_property else None,
            priority=props.get(cfg.priority_checkbox_property) if cfg.priority_checkbox_property else None,
            budget=props.get(cfg.budget_property) if cfg.budget_property else None,
            score=props.get(cfg.score_property) if cfg.score_property else None,
            tags=props.get(cfg.tags_property, []) if cfg.tags_property else [],
            url=raw.get("url"),
            raw=raw,
        )
