from __future__ import annotations

from app.adapters.notion_client import NotionClient
from app.config import Settings
from app.schemas.notes import NoteCreateInput, NoteRecord, NoteResult
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

    def create_note(self, data: NoteCreateInput) -> NoteResult:
        cfg = self.settings.notes_db
        project_id = data.project_id
        area_id = data.area_id
        review_items = []
        confidence = build_confidence(1.0, "Note created with explicit input.", False)
        if not project_id and data.project_name:
            match = self.matching_service.match_project(data.project_name, self.project_service.list_projects())
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

    def search_notes(self, query: str) -> list[NoteRecord]:
        cfg = self.settings.notes_db
        return [self._to_record(item) for item in self.notion.query_database(cfg.database_id, {"query": query})]

    def _to_record(self, raw: dict) -> NoteRecord:
        props = raw.get("properties", {})
        cfg = self.settings.notes_db
        return NoteRecord(
            id=raw["id"],
            title=props.get(cfg.title_property) or raw.get("title", ""),
            content=props.get(cfg.notes_property) if cfg.notes_property else None,
            project_id=props.get(cfg.relation_property) if cfg.relation_property else None,
            area_id=props.get(cfg.area_property) if cfg.area_property else None,
            source_url=props.get(cfg.url_property) if cfg.url_property else None,
            tags=props.get(cfg.tags_property, []) if cfg.tags_property else [],
            raw=raw,
        )
