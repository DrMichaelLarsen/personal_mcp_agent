from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlencode

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


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"https?://[^\s)\]>\"]+", text)


def _extract_join_links(text: str) -> list[str]:
    links = _extract_urls(text)
    join_tokens = ("teams.microsoft.com", "meet.google.com", "zoom.us", "webex", "gotomeeting", "join")
    return [link for link in links if any(token in link.lower() for token in join_tokens)]


def _build_event_description(email_sender: str, email_body: str, analysis: EmailAnalysis) -> str:
    join_links = _extract_join_links(email_body)
    base_description = (analysis.event_description or "").strip()

    # Keep exact LLM description when there are no extracted join links to add.
    # This preserves deterministic behavior for existing event-description logic.
    if base_description and not join_links:
        return base_description

    lines: list[str] = []
    if base_description:
        lines.append(base_description)
    elif analysis.summary:
        lines.append(f"Summary: {analysis.summary}")

    join_links = _extract_join_links(email_body)
    if join_links:
        lines.append("\nJoin links:")
        lines.extend(f"- {link}" for link in join_links)

    if analysis.action_items:
        lines.append("\nPrep checklist:")
        lines.extend(f"- {item.text}" for item in analysis.action_items if item.text)

    if analysis.event_hints:
        lines.append("\nEvent hints:")
        lines.extend(f"- {item}" for item in analysis.event_hints)

    all_links = _extract_urls(email_body)
    if all_links:
        lines.append("\nReference links:")
        lines.extend(f"- {link}" for link in all_links[:8])

    lines.append(f"\nFrom: {email_sender}")
    if email_body:
        lines.append("\nOriginal context:\n" + email_body[:4000])
    return "\n".join(lines).strip()


def _build_calendar_template_link(
    *,
    title: str,
    details: str,
    location: str,
    start: str | None = None,
    end: str | None = None,
) -> str:
    params = {
        "action": "TEMPLATE",
        "text": title,
        "details": details,
        "location": location,
    }
    if start and end:
        params["dates"] = f"{start.replace('-', '').replace(':', '')}/{end.replace('-', '').replace(':', '')}"
    return f"https://calendar.google.com/calendar/render?{urlencode(params)}"


def _normalize_importance(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None

    # Normalize common 1-5 LLM scoring into your productivity scale.
    if 1 <= numeric <= 5:
        return {1: 25, 2: 50, 3: 100, 4: 150, 5: 200}[numeric]
    if 6 <= numeric <= 20:
        return min(200, numeric * 10)
    return max(0, min(300, numeric))


def _infer_task_dates(email_body: str, analysis: EmailAnalysis) -> tuple[str | None, str | None]:
    scheduled = analysis.suggested_scheduled
    deadline = analysis.suggested_deadline

    if analysis.event_start and not scheduled:
        scheduled = analysis.event_start.split("T", 1)[0]
    if analysis.event_end and not deadline:
        deadline = analysis.event_end.split("T", 1)[0]

    if scheduled and deadline:
        return scheduled, deadline

    body = (email_body or "").lower()
    today = date.today()

    if not scheduled:
        if "tomorrow" in body:
            scheduled = (today + timedelta(days=1)).isoformat()
        elif "today" in body:
            scheduled = today.isoformat()

    explicit_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", email_body or "")
    if explicit_dates:
        if not deadline and re.search(r"\b(due|deadline|by)\b", body):
            deadline = explicit_dates[0]
        elif not scheduled:
            scheduled = explicit_dates[0]

    return scheduled, deadline


def _infer_item_dates(text: str) -> tuple[str | None, str | None]:
    body = (text or "").lower()
    today = date.today()
    scheduled = None
    deadline = None
    explicit_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text or "")
    if explicit_dates:
        if re.search(r"\b(due|deadline|by)\b", body):
            deadline = explicit_dates[0]
        else:
            scheduled = explicit_dates[0]
    if not scheduled and "tomorrow" in body:
        scheduled = (today + timedelta(days=1)).isoformat()
    if not scheduled and "today" in body:
        scheduled = today.isoformat()
    if not deadline and re.search(r"\b(due|deadline|by)\b", body):
        deadline = scheduled
    return scheduled, deadline


def _infer_contexts_for_item(text: str, fallback_contexts: list[str]) -> list[str]:
    lower = (text or "").lower()
    contexts: list[str] = []
    if any(token in lower for token in ["call", "phone", "voicemail"]):
        contexts.append("Phone")
    if any(token in lower for token in ["email", "doc", "review", "spreadsheet", "submit", "send", "reply"]):
        contexts.append("Computer")
    if any(token in lower for token in ["home", "house", "errand", "pickup", "drop off", "store"]):
        contexts.append("Home")
    return contexts or list(fallback_contexts)


def _should_split_into_subtasks(analysis: EmailAnalysis, parent_contexts: list[str]) -> bool:
    items = [item.text.strip() for item in analysis.action_items if item.text and item.text.strip()]
    if len(items) < 2:
        return False
    if all(re.match(r"^(step\s+\d+|\d+[\.)])", item.lower()) for item in items):
        return False

    deadline_values = {d for _, d in (_infer_item_dates(item) for item in items) if d}
    if len(deadline_values) >= 2:
        return True

    per_item_context = {tuple(_infer_contexts_for_item(item, parent_contexts)) for item in items}
    if len(per_item_context) >= 2:
        return True

    due_marked_count = sum(1 for item in items if re.search(r"\b(due|deadline|by|before)\b", item.lower()))
    return len(items) >= 3 and due_marked_count >= 2


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
        has_rigid_time_signal = any(
            keyword in body or keyword in subject
            for keyword in ["meeting", "zoom", "teams", "calendar invite", "call at", "meet at", "scheduled at", "appointment"]
        )
        has_task_signal = any(
            keyword in body or keyword in subject
            for keyword in ["todo", "task", "follow up", "action", "please", "review", "reply", "send", "deadline", "due", "asap"]
        )
        has_event_tag = "event" in normalized_labels
        has_todo_tag = "todo" in normalized_labels
        has_task_tag = "task" in normalized_labels
        has_note_tag = "note" in normalized_labels

        # Deterministic, tag-first routing: labels are explicit user intent.
        if has_event_tag and (has_todo_tag or has_task_tag):
            category = "event+task"
            score = 0.99
        elif has_event_tag:
            category = "event"
            score = 0.99
        elif has_todo_tag and has_note_tag:
            category = "task+note"
            score = 0.98
        elif has_todo_tag:
            category = "task"
            score = 0.98
        elif has_task_tag and has_note_tag:
            category = "task+note"
            score = 0.95
        elif has_task_tag:
            category = "task"
            score = 0.95
        elif has_note_tag:
            category = "note"
            score = 0.95
        elif has_rigid_time_signal and has_task_signal:
            category = "event+task"
            score = 0.86
        elif has_rigid_time_signal:
            category = "event"
            score = 0.82
        elif any(keyword in body or keyword in subject for keyword in ["note", "reference", "fyi"]):
            category = "note"
            score = 0.78
        elif has_task_signal:
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
    event_items = []
    join_links = _extract_join_links(email_body)
    base_details = (analysis.event_description or "").strip() or f"From: {email_sender}"
    if join_links:
        base_details = base_details + "\n\nJoin links:\n" + "\n".join(f"- {link}" for link in join_links)

    for hint in (analysis.event_hints or []):
        hint_title = hint.strip() or (analysis.suggested_title or email_subject)
        link = _build_calendar_template_link(
            title=hint_title,
            details=base_details,
            location=analysis.event_location or "",
            # Use extracted time window if available; otherwise Google calendar
            # opens prefilled without a fixed datetime.
            start=analysis.event_start,
            end=analysis.event_end,
        )
        event_items.append(f"{hint_title} — [Add to Google Calendar]({link})")

    if not event_items and (analysis.event_start and analysis.event_end):
        fallback_title = analysis.suggested_title or email_subject
        fallback_link = _build_calendar_template_link(
            title=fallback_title,
            details=base_details,
            location=analysis.event_location or "",
            start=analysis.event_start,
            end=analysis.event_end,
        )
        event_items.append(f"{fallback_title} — [Add to Google Calendar]({fallback_link})")
    if not event_items:
        event_items = ["No event signals extracted."]
    events_markdown = "## Events\n\n" + "\n".join(f"- {item}" for item in event_items)
    original_email_markdown = (
        "## Original Email\n\n"
        f"**Subject:** {email_subject}\n\n"
        f"**From:** {email_sender}\n\n"
        "```text\n"
        f"{safe_body}\n"
        "```"
    )
    quick_links_markdown = "## Quick Links\n\n" + "\n".join(
        [
            "- Gmail message: use source URL if provided in task metadata.",
            f"- Sender: {email_sender}",
            f"- Subject: {email_subject}",
        ]
    )
    full_markdown = "\n\n".join([summary_markdown, emphasis_markdown, outline_markdown, action_items_markdown, events_markdown, quick_links_markdown, original_email_markdown])
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
            inferred_scheduled, inferred_deadline = _infer_task_dates(email.body, analysis) if analysis else (None, None)
            tasks[email.id] = ExtractedTaskCandidate(
                title=analysis.suggested_title if analysis else email.subject,
                notes=structured.full_markdown if structured else email.body[:500],
                structured_content=structured,
                scheduled=inferred_scheduled,
                deadline=inferred_deadline,
                contexts=analysis.suggested_contexts if analysis else [],
                importance=_normalize_importance(analysis.suggested_importance) if analysis else None,
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
                description=_build_event_description(
                    email.sender,
                    email.body,
                    analysis,
                ) if analysis else email.body[:2000],
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
            if len(matched_contexts) > 1:
                matched_contexts = matched_contexts[:1]
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

            if (
                created_task
                and created_task.task
                and analysis
                and _should_split_into_subtasks(analysis, matched_contexts)
            ):
                created_subtasks: list[dict] = []
                parent_title = created_task.task.title
                for action_item in analysis.action_items:
                    text = (action_item.text or "").strip()
                    if not text:
                        continue
                    sub_scheduled, sub_deadline = _infer_item_dates(text)
                    sub_contexts = _infer_contexts_for_item(text, matched_contexts)
                    sub_result = task_service.create_task(
                        TaskCreateInput(
                            title=text[:180],
                            notes=f"Subtask extracted from email task: {parent_title}\n\n{text}",
                            contexts=sub_contexts,
                            importance=candidate.importance,
                            estimated_minutes=max(10, int((candidate.estimated_minutes or 30) / max(1, len(analysis.action_items)))),
                            scheduled=sub_scheduled or candidate.scheduled,
                            deadline=sub_deadline or candidate.deadline,
                            status="Inbox",
                            project_id=selected_project_id,
                            parent_id=created_task.task.id,
                        )
                    )
                    if preview_only:
                        sub_result.created = False
                    created_subtasks.append(
                        {
                            "title": text,
                            "task_id": sub_result.task.id if sub_result.task else None,
                            "created": sub_result.created,
                            "contexts": sub_contexts,
                            "scheduled": sub_scheduled or candidate.scheduled,
                            "deadline": sub_deadline or candidate.deadline,
                        }
                    )
                if created_subtasks:
                    review_items.append(
                        {
                            "item_type": "subtasks_created",
                            "reason": "Multiple distinct action items detected; created child tasks under parent email task.",
                            "options": created_subtasks,
                            "confidence": build_confidence(0.86, "Subtask split applied from multiple distinct actions.", False).model_dump(),
                        }
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
            try:
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
            except Exception as exc:
                review_items.append(
                    {
                        "item_type": "note_creation",
                        "reason": f"Note creation skipped due to schema/API error: {exc}",
                        "options": [{"email_id": email.id, "note_title": candidate.title}],
                        "confidence": build_confidence(0.35, "Note creation failed and was skipped.", True).model_dump(),
                    }
                )

        if classification.category in {"event"} and email.id in event_candidates:
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
            if not created_event.created:
                review_items.append(
                    {
                        "item_type": "calendar_commit",
                        "reason": created_event.confidence.rationale,
                        "options": [
                            {
                                "dry_run_requested": preview_only,
                                "event_title": created_event.event.title,
                                "start": created_event.event.start,
                                "end": created_event.event.end,
                            }
                        ],
                        "confidence": created_event.confidence.model_dump(),
                    }
                )

        if request.mark_processed and not preview_only:
            try:
                deps["email_service"].mark_email_processed(email.id)
            except Exception as exc:
                review_items.append(
                    {
                        "item_type": "email_mark_processed",
                        "reason": f"Failed to apply processed label: {exc}",
                        "options": [{"email_id": email.id, "processed_label": deps["email_service"].settings.gmail.processed_label}],
                        "confidence": build_confidence(0.3, "Processed label update failed.", True).model_dump(),
                    }
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
