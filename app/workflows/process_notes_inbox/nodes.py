from __future__ import annotations

import json
import re

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
    system_prompt = (
        "You enrich Notion notes inbox items. Return strict JSON keys only: "
        "project_name (string or null), area_name (string or null), "
        "tags (array of short category strings or null), rationale (array of strings)."
    )
    user_prompt = json.dumps(
        {
            "note_id": note.id,
            "title": note.title,
            "content": note.content,
            "existing": {
                "project_id": note.project_id,
                "area_id": note.area_id,
                "tags": note.tags,
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
    results: list[NotesInboxItemResult] = []

    for note in state.get("notes", []):
        content = f"{note.title}\n\n{note.content or ''}"
        changed: dict = {}
        review_items = []
        project_id = note.project_id
        area_id = note.area_id

        llm_data, llm_reviews = _llm_enrichment_for_note(note, deps)
        review_items.extend(llm_reviews)

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
                changed["ai_cost"] = round(ai_cost_value, 8)

        updated = False
        if changed and not preview_only:
            note_service.update_note(
                NoteUpdateInput(
                    note_id=note.id,
                    project_id=changed.get("project_id"),
                    area_id=changed.get("area_id"),
                    tags=changed.get("tags"),
                    ai_cost=changed.get("ai_cost"),
                )
            )
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
