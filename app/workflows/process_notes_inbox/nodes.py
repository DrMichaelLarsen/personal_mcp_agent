from __future__ import annotations

import json
import re
from typing import Any

from app.schemas.common import ReviewItem
from app.schemas.notes import NotesInboxItemResult, NoteUpdateInput, ProcessNotesInboxResult
from app.schemas.projects import ProjectCreateInput
from app.utils.confidence import build_confidence
from app.workflows.process_notes_inbox.state import ProcessNotesInboxState


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


def _infer_tags(text: str) -> list[str]:
    """Suggest content-type tags based on keywords in the note."""
    lowered = text.lower()
    tags: list[str] = []
    if any(token in lowered for token in ["meeting", "call", "discussion", "recap"]):
        tags.append("Meeting")
    if any(token in lowered for token in ["idea", "brainstorm", "thought", "concept"]):
        tags.append("Idea")
    if any(token in lowered for token in ["research", "article", "study", "reading", "reference"]):
        tags.append("Reference")
    if any(token in lowered for token in ["decision", "decided", "resolved", "agreed"]):
        tags.append("Decision")
    return tags


def _llm_enrichment_for_note(note, deps: dict) -> tuple[dict, list[ReviewItem]]:
    llm = deps.get("llm_client")
    settings = deps.get("settings")
    cost_service = deps.get("cost_service")
    if not llm or not settings or not settings.llm.enabled or not settings.llm.use_for_notes_inbox:
        return {}, []
    tier = settings.llm.notes_inbox_tier
    model = cost_service.get_tier_model(tier) if cost_service else settings.llm.standard_model
    note_service = deps.get("note_service")
    field_catalog = note_service.get_notes_database_field_catalog() if note_service else []
    system_prompt = (
        "You enrich Notion notes inbox items. Return strict JSON keys only: "
        "project_name (string or null), area_name (string or null), "
        "tags (array of short category strings or null), "
        "additional_properties (object keyed by exact property name -> value, only from field_catalog, or null), "
        "rationale (array of strings)."
    )
    user_prompt = json.dumps(
        {
            "note_id": note.id,
            "title": note.title,
            "content": note.content,
            "field_catalog": field_catalog,
            "existing": {
                "project_id": note.project_id,
                "area_id": note.area_id,
                "tags": note.tags,
                "properties": (note.raw.get("properties", {}) if isinstance(note.raw, dict) else {}),
            },
        }
    )
    try:
        data = llm.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            operation="notes_inbox_enrichment",
            metadata={"note_id": note.id},
        )
    except Exception:
        return {}, [
            ReviewItem(
                item_type="notes_inbox_llm",
                reason="LLM enrichment failed; deterministic fallback used.",
                options=[{"note_id": note.id}],
                confidence=build_confidence(0.4, "Note enrichment fell back to deterministic rules.", True),
            )
        ]
    return data or {}, []


def _coerce_value_for_field_type(value: Any, field_type: str, options: list[str] | None = None) -> Any:
    normalized_type = (field_type or "").strip().lower()
    allowed = {(item or "").strip() for item in (options or []) if isinstance(item, str) and item.strip()}

    if normalized_type in {"title", "rich_text", "url", "email", "phone_number"}:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    if normalized_type == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    if normalized_type == "checkbox":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "checked"}:
                return True
            if lowered in {"false", "no", "0", "unchecked"}:
                return False
        return None

    if normalized_type == "date":
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            start_value = value.get("start")
            if isinstance(start_value, str) and start_value.strip():
                return start_value.strip()
        return None

    if normalized_type in {"status", "select"}:
        if not isinstance(value, str) or not value.strip():
            return None
        selected = value.strip()
        if allowed and selected not in allowed:
            return None
        return selected

    if normalized_type == "multi_select":
        items: list[str] = []
        if isinstance(value, str) and value.strip():
            items = [value.strip()]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        if allowed:
            items = [item for item in items if item in allowed]
        deduped: list[str] = []
        for item in items:
            if item not in deduped:
                deduped.append(item)
        return deduped if deduped else None

    if normalized_type in {"relation", "people"}:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            values = [str(item).strip() for item in value if str(item).strip()]
            if not values:
                return None
            return values[0] if len(values) == 1 else values
        return None

    return None


def fetch_notes(state: ProcessNotesInboxState, deps: dict) -> ProcessNotesInboxState:
    request = deps["request"]
    note_service = deps["note_service"]
    notes = note_service.list_inbox_candidates(
        max_count=request.max_count,
        processed_tag=request.processed_tag,
    )
    return {**state, "notes": notes}


def enrich_notes(state: ProcessNotesInboxState, deps: dict) -> ProcessNotesInboxState:
    request = deps["request"]
    preview_only = request.preview_only
    note_service = deps["note_service"]
    project_service = deps["project_service"]
    matching_service = deps["matching_service"]

    active_projects = project_service.list_active_projects()
    field_catalog = note_service.get_notes_database_field_catalog()
    field_catalog_by_name = {
        item.get("name"): item
        for item in field_catalog
        if isinstance(item, dict) and isinstance(item.get("name"), str) and bool(item.get("name", "").strip())
    }
    cfg = note_service.settings.notes_db
    managed_properties = {
        cfg.title_property,
        cfg.notes_property,
        cfg.relation_property,
        cfg.area_property,
        cfg.tags_property,
        cfg.ai_cost_property,
        cfg.url_property,
        cfg.source_id_property,
    }
    results: list[NotesInboxItemResult] = []

    for note in state.get("notes", []):
        content = f"{note.title}\n\n{note.content or ''}"
        changed: dict = {}
        review_items = []
        project_id = note.project_id
        area_id = note.area_id

        llm_data, llm_reviews = _llm_enrichment_for_note(note, deps)
        review_items.extend(llm_reviews)

        llm_additional_properties = llm_data.get("additional_properties") if isinstance(llm_data, dict) else None
        additional_properties: dict[str, Any] = {}
        if isinstance(llm_additional_properties, dict):
            existing_properties = note.raw.get("properties", {}) if isinstance(note.raw, dict) else {}
            for key, value in llm_additional_properties.items():
                if not isinstance(key, str) or not key.strip() or key in managed_properties:
                    continue
                field_info = field_catalog_by_name.get(key)
                if not field_info:
                    continue
                field_type = field_info.get("type")
                if not isinstance(field_type, str):
                    continue
                coerced = _coerce_value_for_field_type(value, field_type, field_info.get("options") if isinstance(field_info.get("options"), list) else None)
                if coerced is None:
                    continue
                if existing_properties.get(key) == coerced:
                    continue
                additional_properties[key] = coerced
                changed[key] = coerced

        # --- Project ---
        if not project_id:
            explicit_name = _extract_explicit_project_name(content) or (
                llm_data.get("project_name") if isinstance(llm_data.get("project_name"), str) else None
            )
            if explicit_name:
                existing = next(
                    (p for p in active_projects if p.title.strip().lower() == explicit_name.strip().lower()),
                    None,
                )
                if existing:
                    project_id = existing.id
                elif not preview_only:
                    created = project_service.create_project(
                        ProjectCreateInput(title=explicit_name, tags=[project_service.settings.review_project_tag])
                    )
                    project_id = created.id
                    active_projects.append(created)
            if not project_id:
                token = note.title.split(":")[0].strip() if ":" in note.title else note.title
                match = matching_service.match_project(
                    token, active_projects, metadata={"source": "notes_inbox", "note_id": note.id}
                )
                if match.matched and match.selected_project:
                    project_id = match.selected_project.id
                else:
                    review_items.extend(match.review_items)
        if project_id and project_id != note.project_id:
            changed["project_id"] = project_id

        # --- Area ---
        if not area_id:
            # Prefer area from matched project
            if project_id:
                matched_proj = next((p for p in active_projects if p.id == project_id), None)
                if matched_proj and getattr(matched_proj, "area_id", None):
                    area_id = matched_proj.area_id

        if not area_id:
            area_hint = (
                llm_data.get("area_name") if isinstance(llm_data.get("area_name"), str) else None
            ) or note.title
            area_match, area_reviews, _ = project_service.match_area_name(area_hint)
            review_items.extend(area_reviews)
            if area_match:
                area_id = area_match.id

        if area_id and area_id != note.area_id:
            changed["area_id"] = area_id

        # --- Tags ---
        llm_tags = llm_data.get("tags") if isinstance(llm_data.get("tags"), list) else None
        inferred_tags = llm_tags or _infer_tags(content)
        current_tags = list(note.tags or [])
        new_tags = [t for t in inferred_tags if isinstance(t, str) and t.strip() and t not in current_tags]
        if request.processed_tag not in current_tags:
            new_tags.append(request.processed_tag)
        if new_tags:
            changed["tags"] = [*current_tags, *new_tags]

        # --- AI cost ---
        cost_service = deps.get("cost_service")
        if cost_service:
            ai_summary = cost_service.summarize_recent_usage(
                event_count=0,
                operation_prefix="notes_inbox_enrichment",
                metadata_filter={"note_id": note.id},
            )
            ai_cost_value = float(ai_summary.get("total_estimated_cost", 0.0) or 0.0)
            if ai_cost_value > 0:
                changed["ai_cost"] = round((note.ai_cost or 0.0) + ai_cost_value, 8)

        updated = False
        if changed and not preview_only:
            note_service.update_note(
                NoteUpdateInput(
                    note_id=note.id,
                    project_id=changed.get("project_id"),
                    area_id=changed.get("area_id"),
                    tags=changed.get("tags"),
                    ai_cost=changed.get("ai_cost"),
                    additional_properties=additional_properties or None,
                )
            )
            note_service.append_ai_decision_note(note.id, changed, source="process_notes_inbox")
            updated = True

        results.append(
            NotesInboxItemResult(
                note_id=note.id,
                updated=updated,
                changed_fields=changed,
                review_items=review_items,
            )
        )
    return {**state, "results": results}


def build_result(state: ProcessNotesInboxState, deps: dict) -> ProcessNotesInboxState:
    request = deps["request"]
    results = state.get("results", [])
    final = ProcessNotesInboxResult(
        preview_only=request.preview_only,
        processed_count=len(results),
        updated_count=sum(1 for item in results if item.updated),
        results=results,
    )
    return {**state, "result": final}
