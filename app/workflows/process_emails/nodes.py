from __future__ import annotations

from app.schemas.calendar import EventCreateInput
from app.schemas.email import (
    EmailAnalysis,
    EmailClassification,
    EmailStructuredContent,
    EmailTaskItem,
    ExtractedEventCandidate,
    ExtractedNoteCandidate,
    ExtractedTaskCandidate,
    PerEmailProcessResult,
)
from app.schemas.notes import NoteCreateInput
from app.schemas.projects import ProjectCreateInput
from app.schemas.tasks import TaskCreateInput
from app.utils.confidence import build_confidence
from app.utils.ids import stable_hash
from app.workflows.process_emails.state import ProcessEmailsState


def fetch_emails(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    email_service = deps["email_service"]
    request = deps["request"]
    if request.input_emails:
        return {**state, "emails": request.input_emails}
    return {**state, "emails": email_service.get_unprocessed_tagged_emails(request.max_count, request.query)}


def classify_emails(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    classifications: list[EmailClassification] = []
    for email in state.get("emails", []):
        body = email.body.lower()
        subject = email.subject.lower()
        normalized_labels = {label.strip().lower() for label in email.labels}
        if "task" in normalized_labels and "note" in normalized_labels:
            category = "task+note"
            score = 0.95
        elif "event" in normalized_labels and "task" in normalized_labels:
            category = "event+task"
            score = 0.95
        elif "event" in normalized_labels:
            category = "event"
            score = 0.95
        elif "task" in normalized_labels:
            category = "task"
            score = 0.95
        elif "note" in normalized_labels:
            category = "note"
            score = 0.95
        elif any(keyword in body or keyword in subject for keyword in ["meeting", "call", "schedule"]):
            category = "event"
            score = 0.82
        elif any(keyword in body or keyword in subject for keyword in ["note", "reference", "fyi"]):
            category = "note"
            score = 0.78
        elif any(
            keyword in body or keyword in subject
            for keyword in ["todo", "task", "follow up", "action", "please", "review", "reply", "send"]
        ):
            category = "task"
            score = 0.86
        else:
            category = "ignore"
            score = 0.55
        classifications.append(
            EmailClassification(
                email_id=email.id,
                category=category,
                confidence=build_confidence(score, f"Heuristic classification for email '{email.subject}'.", score < 0.8),
            )
        )
    return {**state, "classifications": classifications}


def analyze_emails(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    email_service = deps["email_service"]
    analyses: dict[str, EmailAnalysis] = {}
    for email in state.get("emails", []):
        analyses[email.id] = email_service.analyze_email(email)
    return {**state, "analyses": analyses}


def _build_structured_content(email_subject: str, email_sender: str, email_body: str, analysis: EmailAnalysis) -> EmailStructuredContent:
    safe_body = (email_body or "").strip() or "[No body text was available from Gmail for this message.]"
    summary_markdown = f"## Summary\n\n**Quick summary:** {analysis.summary}"
    emphasis_lines = ["> [!TIP] *Captured from email* — review and refine before final execution."]
    if analysis.event_hints:
        emphasis_lines.insert(0, "> [!IMPORTANT] **Potential event detected** — verify date/time before keeping on calendar.")
    emphasis_markdown = "\n".join(emphasis_lines)
    outline_items = analysis.outline or ["No outline extracted."]
    outline_markdown = "## Outline\n\n" + "\n".join(f"- {item}" for item in outline_items)
    action_items = analysis.action_items or [EmailTaskItem(text="No clear action items extracted.")]
    action_items_markdown = "## Action Items\n\n" + "\n".join(f"- [ ] {item.text}" for item in action_items)
    event_items = analysis.event_hints or ["No event signals extracted."]
    events_markdown = "## Events\n\n" + "\n".join(f"- {item}" for item in event_items)
    original_email_markdown = (
        "## Original Email\n\n"
        f"**Subject:** {email_subject}\n\n"
        f"**From:** {email_sender}\n\n"
        "```text\n"
        f"{safe_body}\n"
        "```"
    )
    full_markdown = "\n\n".join([summary_markdown, emphasis_markdown, outline_markdown, action_items_markdown, events_markdown, original_email_markdown])
    return EmailStructuredContent(
        summary_markdown=summary_markdown,
        action_items_markdown=action_items_markdown,
        original_email_markdown=original_email_markdown,
        full_markdown=full_markdown,
    )


def _append_attachment_links(base_markdown: str, attachment_links: list) -> str:
    if not attachment_links:
        return base_markdown
    lines = ["## Attachments"]
    for link in attachment_links:
        target = link.drive_url or link.notion_file_url or "(pending/manual)"
        lines.append(f"- {link.filename} ({link.mime_type}) -> {target}")
    return f"{base_markdown}\n\n" + "\n".join(lines)


def extract_candidates(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    tasks: dict[str, ExtractedTaskCandidate] = {}
    notes: dict[str, ExtractedNoteCandidate] = {}
    events: dict[str, ExtractedEventCandidate] = {}
    emails = {email.id: email for email in state.get("emails", [])}
    analyses = state.get("analyses", {})
    for classification in state.get("classifications", []):
        email = emails[classification.email_id]
        analysis = analyses.get(email.id)
        if classification.category in {"task", "task+note", "event+task"}:
            structured = _build_structured_content(email.subject, email.sender, email.body, analysis) if analysis else None
            tasks[email.id] = ExtractedTaskCandidate(
                title=analysis.suggested_title if analysis else email.subject,
                notes=structured.full_markdown if structured else email.body[:500],
                structured_content=structured,
                scheduled=analysis.suggested_scheduled if analysis else None,
                deadline=analysis.suggested_deadline if analysis else None,
                contexts=analysis.suggested_contexts if analysis else [],
                importance=analysis.suggested_importance if analysis else None,
                estimated_minutes=analysis.suggested_time_required if analysis else None,
                project_name=analysis.suggested_project_name if analysis else None,
            )
        if classification.category in {"note", "task+note"}:
            structured = _build_structured_content(email.subject, email.sender, email.body, analysis) if analysis else None
            notes[email.id] = ExtractedNoteCandidate(
                title=analysis.suggested_title if analysis else email.subject,
                content=structured.full_markdown if structured else email.body[:1000],
                project_name=analysis.suggested_project_name if analysis else None,
            )
        if classification.category in {"event", "event+task"}:
            event_title = analysis.suggested_title if analysis and analysis.suggested_title else email.subject
            events[email.id] = ExtractedEventCandidate(
                title=f"AI Review: {event_title}",
                start=analysis.event_start if analysis else None,
                end=analysis.event_end if analysis else None,
                description=analysis.event_description if analysis else None,
                location=analysis.event_location if analysis else None,
                project_name=analysis.suggested_project_name if analysis else None,
            )
    return {**state, "task_candidates": tasks, "note_candidates": notes, "event_candidates": events}


def match_projects(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    matching_service = deps["matching_service"]
    project_service = deps["project_service"]
    projects = project_service.list_active_projects()
    contexts = project_service.list_contexts()
    matches = {}
    resolved_contexts: dict[str, list[str]] = {}
    context_review_items = {}
    analyses = state.get("analyses", {})
    for email in state.get("emails", []):
        analysis = analyses.get(email.id)
        token = (analysis.suggested_project_name if analysis and analysis.suggested_project_name else email.subject.split(":")[0]).strip()
        if token:
            matches[email.id] = matching_service.match_project(token, projects, metadata={"email_id": email.id})
        requested_contexts = analysis.suggested_contexts if analysis else []
        matched_contexts, context_reviews = matching_service.match_contexts(requested_contexts, contexts, metadata={"email_id": email.id})
        resolved_contexts[email.id] = matched_contexts
        context_review_items[email.id] = context_reviews
    return {
        **state,
        "project_matches": matches,
        "resolved_contexts": resolved_contexts,
        "context_review_items": context_review_items,
    }


def build_results(state: ProcessEmailsState, deps: dict) -> ProcessEmailsState:
    preview_only = state.get("preview_only", True)
    task_service = deps["task_service"]
    note_service = deps["note_service"]
    calendar_service = deps["calendar_service"]
    project_service = deps["project_service"]
    matching_service = deps["matching_service"]
    request = deps["request"]
    cost_service = deps.get("cost_service")
    emails = {email.id: email for email in state.get("emails", [])}
    analyses = state.get("analyses", {})
    task_candidates = state.get("task_candidates", {})
    note_candidates = state.get("note_candidates", {})
    event_candidates = state.get("event_candidates", {})
    results: list[PerEmailProcessResult] = []
    for classification in state.get("classifications", []):
        email = emails[classification.email_id]
        analysis = analyses.get(email.id)
        review_items = []
        attachment_links, attachment_reviews = deps["email_service"].process_attachments(email, preview_only=preview_only)
        review_items.extend(attachment_reviews)
        created_project = None
        created_task = None
        created_note = None
        created_event = None
        match = state.get("project_matches", {}).get(email.id)
        if match and match.review_items:
            review_items.extend(match.review_items)
        review_items.extend(state.get("context_review_items", {}).get(email.id, []))

        actionable = classification.category in {"task", "note", "event", "task+note", "event+task"}
        has_matched_project = bool(match and match.matched and match.selected_project)
        if actionable and not has_matched_project:
            suggested_name = (analysis.suggested_project_name if analysis else None) or email.subject.split(":")[0].strip()
            if suggested_name:
                review_item = matching_service.build_project_creation_review(suggested_name)
                review_items.append(review_item)
                if request.create_project_if_missing and not preview_only:
                    created_project = project_service.create_project(
                        ProjectCreateInput(
                            title=suggested_name,
                            tags=[project_service.settings.review_project_tag],
                        )
                    )

        selected_project_id = None
        if created_project:
            selected_project_id = created_project.id
        elif match and match.matched and match.selected_project:
            selected_project_id = match.selected_project.id

        if classification.category in {"task", "task+note", "event+task"} and email.id in task_candidates:
            candidate = task_candidates[email.id]
            matched_contexts = state.get("resolved_contexts", {}).get(email.id, candidate.contexts)
            ai_summary = cost_service.summarize_recent_usage(
                event_count=0,
                operation_prefix="",
                metadata_filter={"email_id": email.id},
            ) if cost_service else {
                "event_count": 0,
                "total_estimated_cost": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
            ai_cost_md = (
                cost_service.format_cost_summary_markdown(ai_summary)
                if cost_service
                else (
                    "## AI Cost Summary\n\n"
                    f"- Estimated Cost: ${float(ai_summary.get('total_estimated_cost', 0.0) or 0.0):.6f}\n"
                    f"- Input Tokens: {int(ai_summary.get('input_tokens', 0) or 0)}\n"
                    f"- Output Tokens: {int(ai_summary.get('output_tokens', 0) or 0)}\n"
                    f"- AI Calls: {int(ai_summary.get('event_count', 0) or 0)}"
                )
            )
            notes_with_attachments = _append_attachment_links(candidate.notes or "", attachment_links)
            ai_cost_value = float(ai_summary.get("total_estimated_cost", 0.0) or 0.0)
            if preview_only or classification.confidence.confidence_score < request.confidence_threshold:
                created_task = task_service.create_task(
                    TaskCreateInput(
                        title=candidate.title,
                        notes=notes_with_attachments,
                        contexts=matched_contexts,
                        estimated_minutes=candidate.estimated_minutes,
                        importance=candidate.importance,
                        scheduled=candidate.scheduled,
                        deadline=candidate.deadline,
                        status="Inbox",
                        project_id=selected_project_id,
                        ai_cost=ai_cost_value,
                        ai_cost_summary=ai_cost_md,
                    )
                )
                created_task.created = not preview_only and created_task.created
            else:
                created_task = task_service.create_task(
                    TaskCreateInput(
                        title=candidate.title,
                        notes=notes_with_attachments,
                        contexts=matched_contexts,
                        estimated_minutes=candidate.estimated_minutes,
                        importance=candidate.importance,
                        scheduled=candidate.scheduled,
                        deadline=candidate.deadline,
                        status="Inbox",
                        project_id=selected_project_id,
                        source_url=f"gmail://{stable_hash(email.id)}",
                        ai_cost=ai_cost_value,
                        ai_cost_summary=ai_cost_md,
                    )
                )

        if classification.category in {"note", "task+note"} and email.id in note_candidates:
            candidate = note_candidates[email.id]
            content_with_attachments = _append_attachment_links(candidate.content or "", attachment_links)
            created_note = note_service.create_note(
                NoteCreateInput(
                    title=candidate.title,
                    content=content_with_attachments,
                    project_id=selected_project_id,
                    source_email_id=email.id,
                )
            )
            if preview_only:
                created_note.created = False

        if classification.category in {"event", "event+task"} and email.id in event_candidates:
            candidate = event_candidates[email.id]
            start = candidate.start or "2099-01-01T09:00:00"
            end = candidate.end or "2099-01-01T10:00:00"
            created_event = calendar_service.schedule_event(
                EventCreateInput(
                    title=candidate.title,
                    start=start,
                    end=end,
                    description=candidate.description,
                    location=candidate.location,
                    email_id=email.id,
                    project_id=selected_project_id,
                    dry_run=preview_only,
                )
            )

        results.append(
            PerEmailProcessResult(
                email_id=email.id,
                classification=classification,
                analysis=analysis,
                created_project=created_project,
                created_task=created_task,
                created_note=created_note,
                created_event=created_event,
                review_items=review_items,
            )
        )
    return {**state, "results": results}
