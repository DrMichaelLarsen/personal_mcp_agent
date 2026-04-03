from __future__ import annotations

from typing import Any

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.notes import NoteCreateInput, NoteRecord, NoteResult, NoteUpdateInput
from app.utils.confidence import build_confidence

from app.services.matching_service import MatchingService
from app.services.project_service import ProjectService


class NoteService:
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

    def get_notes_database_field_catalog(self) -> list[dict[str, Any]]:
        cfg = self.settings.notes_db
        database_id = cfg.database_id
        if not database_id:
            return []
        getter = getattr(self.notion, "_get_database_schema", None)
        if not callable(getter):
            return []
        try:
            schema_raw = getter(database_id) or {}
        except Exception:
            return []
        if not isinstance(schema_raw, dict):
            return []
        catalog: list[dict[str, Any]] = []
        for name, definition in schema_raw.items():
            if not isinstance(definition, dict):
                continue
            property_type = definition.get("type")
            if not property_type:
                continue
            if property_type in {
                "formula",
                "rollup",
                "created_time",
                "created_by",
                "last_edited_time",
                "last_edited_by",
                "unique_id",
                "verification",
            }:
                continue
            options: list[str] = []
            nested = definition.get(property_type)
            if isinstance(nested, dict):
                raw_options = nested.get("options")
                if isinstance(raw_options, list):
                    for opt in raw_options:
                        if not isinstance(opt, dict):
                            continue
                        option_name = opt.get("name")
                        if isinstance(option_name, str) and option_name.strip():
                            options.append(option_name)
            catalog.append(
                {
                    "name": name,
                    "type": property_type,
                    "options": options,
                }
            )
        return sorted(catalog, key=lambda item: item["name"].lower())

    def create_note(self, data: NoteCreateInput) -> NoteResult:
        cfg = self.settings.notes_db
        project_id = data.project_id
        area_id = data.area_id
        review_items = []
        confidence = build_confidence(1.0, "Note created with explicit input.", False)
        if not project_id and data.project_name:
            match = self.matching_service.match_project(data.project_name, self.project_service.list_active_projects())
            review_items.extend(match.review_items)
            confidence = match.confidence
            if match.matched and match.selected_project:
                project_id = match.selected_project.id
                area_id = area_id or match.selected_project.area_id

        if not area_id:
            area_query = data.area_name or data.title
            area_match, area_reviews, area_confidence = self.project_service.match_area_name(area_query)
            review_items.extend(area_reviews)
            if area_match:
                area_id = area_match.id
            else:
                confidence = area_confidence

        properties = {
            key: value
            for key, value in {
                cfg.title_property: data.title,
                cfg.notes_property: data.content if cfg.store_content_in_property else None,
                cfg.relation_property: project_id,
                cfg.area_property: area_id,
                cfg.url_property: data.source_url,
                cfg.source_id_property: data.source_email_id,
                cfg.tags_property: data.tags,
            }.items()
            if key is not None
        }
        children = self.notion.markdown_to_blocks(data.content) if data.content else None
        raw = self.notion.create_page(cfg.database_id, properties, children=children)
        return NoteResult(created=True, note=self._to_record(raw), confidence=confidence, review_items=review_items, message="Note created.")

    def list_inbox_candidates(
        self,
        max_count: int = 50,
        processed_tag: str = "Inbox Processed",
        inbox_formula_property: str | None = "Inbox",
    ) -> list[NoteRecord]:
        cfg = self.settings.notes_db
        processed_key = (processed_tag or "").strip().lower()
        inbox_formula_key = (inbox_formula_property or "").strip()
        all_items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        candidates: list[NoteRecord] = []
        for note in all_items:
            if inbox_formula_key:
                inbox_value = (note.raw.get("properties", {}) if isinstance(note.raw, dict) else {}).get(inbox_formula_key)
                if not bool(inbox_value):
                    continue
            tag_keys = {(tag or "").strip().lower() for tag in (note.tags or []) if (tag or "").strip()}
            if processed_key and processed_key in tag_keys:
                continue
            candidates.append(note)
        return candidates[: max(1, max_count)]

    def update_note(self, data: NoteUpdateInput) -> NoteRecord:
        cfg = self.settings.notes_db
        properties = {}
        base_properties = {
            cfg.relation_property: data.project_id,
            cfg.area_property: data.area_id,
            cfg.tags_property: data.tags,
            cfg.ai_cost_property: data.ai_cost,
        }
        for key, value in base_properties.items():
            if key is not None and value is not None:
                properties[key] = value
        for key, value in (data.additional_properties or {}).items():
            if key and value is not None:
                properties[key] = value
        raw = self.notion.update_page(data.note_id, properties)
        return self._to_record(raw)

    def append_ai_decision_note(self, note_id: str, changed_fields: dict, source: str = "process_notes_inbox") -> None:
        if not changed_fields:
            return

        def _format_value(value):
            if isinstance(value, list):
                return ", ".join(str(item) for item in value) if value else "(none)"
            if value is None:
                return "(none)"
            return str(value)

        lines = [
            "## AI Decision Log",
            f"Source: {source}",
            "Changes applied:",
        ]
        for key in sorted(changed_fields.keys()):
            lines.append(f"- {key}: {_format_value(changed_fields.get(key))}")
        self.notion.append_markdown(note_id, "\n".join(lines))

    def search_notes(self, query: str) -> list[NoteRecord]:
        cfg = self.settings.notes_db
        return [self._to_record(item) for item in self.notion.query_database(cfg.database_id, {"query": query})]

    def _to_record(self, raw: dict) -> NoteRecord:
        props = raw.get("properties", {})
        cfg = self.settings.notes_db

        def _as_single_id(value):
            if isinstance(value, list):
                if not value:
                    return None
                first = value[0]
                return first if isinstance(first, str) else str(first)
            if value is None:
                return None
            return value if isinstance(value, str) else str(value)

        return NoteRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            content=props.get(cfg.notes_property) if cfg.notes_property else None,
            project_id=_as_single_id(props.get(cfg.relation_property)) if cfg.relation_property else None,
            area_id=_as_single_id(props.get(cfg.area_property)) if cfg.area_property else None,
            source_url=props.get(cfg.url_property) if cfg.url_property else None,
            tags=props.get(cfg.tags_property, []) if cfg.tags_property else [],
            ai_cost=props.get(cfg.ai_cost_property) if cfg.ai_cost_property else None,
            raw=raw,
        )
