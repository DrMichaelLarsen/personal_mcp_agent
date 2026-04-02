from __future__ import annotations

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

    def list_inbox_candidates(self, max_count: int = 50, processed_tag: str = "Inbox Processed") -> list[NoteRecord]:
        cfg = self.settings.notes_db
        processed_key = (processed_tag or "").strip().lower()
        all_items = [self._to_record(item) for item in self.notion.query_database(cfg.database_id)]
        candidates: list[NoteRecord] = []
        for note in all_items:
            tag_keys = {(tag or "").strip().lower() for tag in (note.tags or []) if (tag or "").strip()}
            if processed_key and processed_key in tag_keys:
                continue
            candidates.append(note)
        return candidates[: max(1, max_count)]

    def update_note(self, data: NoteUpdateInput) -> NoteRecord:
        cfg = self.settings.notes_db
        properties = {
            key: value
            for key, value in {
                cfg.relation_property: data.project_id,
                cfg.area_property: data.area_id,
                cfg.tags_property: data.tags,
                cfg.ai_cost_property: data.ai_cost,
            }.items()
            if key is not None and value is not None
        }
        raw = self.notion.update_page(data.note_id, properties)
        return self._to_record(raw)

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
