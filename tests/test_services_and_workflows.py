from __future__ import annotations

from app.config import get_settings
from app.adapters.llm_client import create_llm_client
from app.services.cost_service import CostService
from app.schemas.calendar import CalendarEvent
from app.schemas.email import EmailMessage, ProcessEmailsInput
from app.schemas.notes import NoteCreateInput
from app.schemas.planning import DayPlanInput
from app.schemas.projects import ProjectCreateInput, ProjectRecord
from app.schemas.tasks import TaskCreateInput
from app.services.calendar_service import CalendarService
from app.services.email_service import EmailAnalysisService, EmailService
from app.services.matching_service import MatchingService
from app.services.note_service import NoteService
from app.services.planning_service import PlanningService
from app.services.project_service import ProjectService
from app.services.task_service import TaskService
from app.workflows.plan_day.graph import PlanDayWorkflow
from app.workflows.process_emails.graph import ProcessEmailsWorkflow
from app.adapters.calendar_client import CalendarClient
from app.schemas.calendar import EventCreateInput
from app.adapters.gmail_client import GmailClient
from tests.fakes import FakeCalendarClient, FakeDriveClient, FakeGmailClient, FakeLLMClient, FakeNotionClient, make_attachment


def build_context():
    settings = get_settings(
        tasks_db={"database_id": "tasks-db"},
        projects_db={"database_id": "projects-db"},
        notes_db={"database_id": "notes-db"},
    )
    notion = FakeNotionClient()
    projects = ProjectService(notion, settings)
    matching = MatchingService(settings)
    tasks = TaskService(notion, projects, matching, settings)
    notes = NoteService(notion, projects, matching, settings)
    calendar = CalendarService(FakeCalendarClient([CalendarEvent(id="busy-1", title="Standup", start="2026-03-25T10:00:00", end="2026-03-25T10:30:00")]), settings)
    email = EmailService(
        FakeGmailClient(
            [
                EmailMessage(id="e1", thread_id="t1", subject="Project Alpha: follow up task", sender="a@example.com", body="Please action this todo"),
                EmailMessage(id="e2", thread_id="t2", subject="Reference note", sender="b@example.com", body="FYI note for later"),
            ]
        ),
        settings,
    )
    planning = PlanningService(settings)
    return settings, notion, projects, matching, tasks, notes, calendar, email, planning


def seed_area_tree(settings, notion):
    settings.areas_db.database_id = "areas-db"
    root = notion.create_page(
        "areas-db",
        {
            settings.areas_db.title_property: "Work",
            settings.areas_db.status_property: "Active",
            settings.areas_db.parent_property: None,
        },
    )
    child = notion.create_page(
        "areas-db",
        {
            settings.areas_db.title_property: "Engineering",
            settings.areas_db.status_property: "Active",
            settings.areas_db.parent_property: root["id"],
        },
    )
    leaf = notion.create_page(
        "areas-db",
        {
            settings.areas_db.title_property: "Platform",
            settings.areas_db.status_property: "Active",
            settings.areas_db.parent_property: child["id"],
        },
    )
    return root, child, leaf


def test_task_creation_mapping():
    settings, notion, projects, matching, tasks, *_ = build_context()
    project = projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1", importance=100, priority=True))
    assert project.id
    result = tasks.create_task(
        TaskCreateInput(
            title="Write update",
            project_id=project.id,
            estimated_minutes=45,
            contexts=["Computer"],
            importance=100,
            scheduled="2026-03-25",
            deadline="2026-03-26",
        )
    )
    assert result.created is True
    assert result.task is not None
    raw = notion.get_page(result.task.id)
    assert raw["properties"][settings.tasks_db.title_property] == "Write update"
    assert raw["properties"][settings.tasks_db.relation_property] == project.id
    assert raw["properties"][settings.tasks_db.contexts_property] == ["Computer"]
    assert raw["properties"][settings.tasks_db.importance_property] == 100


def test_task_record_mapping_coerces_empty_people_relation_phone_shapes():
    settings, _, projects, matching, tasks, *_ = build_context()
    raw = {
        "id": "task-raw-1",
        "title": "Fallback title",
        "properties": {
            settings.tasks_db.title_property: "Mapped title",
            settings.tasks_db.contexts_property: None,
            settings.tasks_db.assigned_property: {"id": "abc", "type": "people", "people": []},
            settings.tasks_db.phone_property: {"id": "xyz", "type": "phone_number", "phone_number": None},
            settings.tasks_db.dependency_of_property: None,
            settings.tasks_db.depends_on_property: None,
        },
    }
    mapped = tasks._to_record(raw)
    assert mapped.title == "Mapped title"
    assert mapped.contexts == []
    assert mapped.assigned_to == []
    assert mapped.phone is None
    assert mapped.dependency_of_ids == []
    assert mapped.depends_on_ids == []


def test_project_matching_ambiguous():
    _, _, projects, matching, *_ = build_context()
    projects.create_project(ProjectCreateInput(title="Alpha Website", area_id="area-1"))
    projects.create_project(ProjectCreateInput(title="Alpha Marketing", area_id="area-1"))
    result = matching.match_project("Alpha", projects.list_projects())
    assert result.matched is False
    assert result.review_items


def test_note_creation_mapping():
    settings, notion, projects, matching, _, notes, *_ = build_context()
    project = projects.create_project(ProjectCreateInput(title="Project Notes", area_id="area-1"))
    assert project.id
    result = notes.create_note(NoteCreateInput(title="API Reference", content="Details", project_id=project.id))
    assert result.created is True
    assert result.note is not None
    raw = notion.get_page(result.note.id)
    assert raw["properties"][settings.notes_db.title_property] == "API Reference"
    assert raw["properties"][settings.notes_db.relation_property] == project.id
    assert raw.get("children")


def test_markdown_blocks_support_bold_italic_and_callout():
    settings, notion, projects, matching, _, notes, *_ = build_context()
    project = projects.create_project(ProjectCreateInput(title="Formatting", area_id="area-1"))
    assert project.id
    result = notes.create_note(
        NoteCreateInput(
            title="Format Test",
            project_id=project.id,
            content="""
> [!IMPORTANT] **Critical** review required
## Heading
This has **bold** and *italic* text.
""".strip(),
        )
    )
    raw = notion.get_page(result.note.id)
    blocks = raw.get("children", [])
    assert any(block.get("type") == "callout" for block in blocks)
    paragraph = next(block for block in blocks if block.get("type") == "paragraph")
    rich = paragraph.get("rich_text", [])
    assert any(part.get("annotations", {}).get("bold") for part in rich)
    assert any(part.get("annotations", {}).get("italic") for part in rich)


def test_confidence_logic_labels():
    from app.utils.confidence import build_confidence

    assert build_confidence(0.9, "high").confidence_label == "high"
    assert build_confidence(0.7, "medium").confidence_label == "medium"
    assert build_confidence(0.4, "low").confidence_label == "low"


def test_email_classification_routing_preview():
    settings, notion, projects, matching, tasks, notes, calendar, email, _ = build_context()
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(ProcessEmailsInput(preview_only=True, max_count=5))
    assert result.preview_only is True
    assert result.processed_count == 2
    assert any(item.classification.category == "task" for item in result.results)
    task_result = next(item for item in result.results if item.classification.category == "task")
    assert task_result.analysis is not None
    assert task_result.analysis.summary
    assert task_result.created_task is not None
    assert task_result.created_task.task is not None
    assert "## Summary" in (task_result.created_task.task.notes or "")
    assert "## Outline" in (task_result.created_task.task.notes or "")
    assert "## Events" in (task_result.created_task.task.notes or "")
    assert "## Original Email" in (task_result.created_task.task.notes or "")
    assert "## AI Cost Summary" in (task_result.created_task.task.notes or "")
    assert task_result.created_task.task is not None
    raw_task = notion.get_page(task_result.created_task.task.id)
    assert raw_task.get("children")


def test_day_planning_output_structure():
    _, _, projects, matching, tasks, notes, calendar, email, planning = build_context()
    project = projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1", importance=100, priority=True))
    assert project.id
    tasks.create_task(TaskCreateInput(title="Deep task", project_id=project.id, estimated_minutes=90, importance=100, scheduled="2026-03-25", contexts=["Computer"]))
    tasks.create_task(TaskCreateInput(title="Quick task", project_id=project.id, estimated_minutes=20, scheduled="2026-03-25", contexts=["Home"]))
    workflow = PlanDayWorkflow({"calendar_service": calendar, "task_service": tasks, "planning_service": planning})
    result = workflow.run(DayPlanInput(target_date="2026-03-25", max_tasks=2))
    assert result.target_date == "2026-03-25"
    assert len(result.prioritized_tasks) == 2
    assert all(block.block_type == "focus" for block in result.suggested_blocks)


def test_calendar_service_falls_back_to_preview_when_adapter_not_implemented():
    settings = get_settings()
    service = CalendarService(CalendarClient(), settings)
    result = service.schedule_event(
        EventCreateInput(
            title="Test event",
            start="2026-03-25T09:00:00",
            end="2026-03-25T10:00:00",
            dry_run=False,
        )
    )
    assert result.created is False
    assert result.dry_run is True
    assert result.confidence.review_required is True
    day = service.get_calendar_for_day("2026-03-25")
    assert day.events == []


def test_task_validation_scheduled_not_later_than_deadline():
    _, _, projects, matching, tasks, *_ = build_context()
    project = projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    assert project.id
    result = tasks.create_task(
        TaskCreateInput(
            title="Bad dates",
            project_id=project.id,
            contexts=["Computer"],
            scheduled="2026-03-27",
            deadline="2026-03-25",
        )
    )
    assert result.created is False
    assert "later than deadline" in result.message


def test_project_creation_mapping_and_sorting():
    settings, notion, projects, *_ = build_context()
    first = projects.create_project(ProjectCreateInput(title="Lower Score", area_id="area-1", importance=50, priority=False))
    second = projects.create_project(ProjectCreateInput(title="Higher Score", area_id="area-2", importance=200, priority=True, budget=500))
    notion.pages[first.id]["properties"][settings.projects_db.score_property] = 10
    notion.pages[second.id]["properties"][settings.projects_db.score_property] = 100
    raw = notion.get_page(second.id)
    assert raw["properties"][settings.projects_db.area_property] == "area-2"
    assert raw["properties"][settings.projects_db.importance_property] == 200
    assert raw["properties"][settings.projects_db.priority_checkbox_property] is True
    assert projects.list_projects()[0].title == "Higher Score"


def test_email_label_based_routing_task_and_note():
    settings, notion, projects, matching, tasks, notes, calendar, email, _ = build_context()
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=True,
            input_emails=[
                EmailMessage(
                    id="label-task-1",
                    thread_id="lt-1",
                    subject="Anything",
                    sender="x@example.com",
                    body="No task keywords here.",
                    labels=["task"],
                ),
                EmailMessage(
                    id="label-note-1",
                    thread_id="ln-1",
                    subject="Anything else",
                    sender="y@example.com",
                    body="No note keywords here either.",
                    labels=["note"],
                ),
            ],
        )
    )
    categories = {item.email_id: item.classification.category for item in result.results}
    assert categories["label-task-1"] == "task"
    assert categories["label-note-1"] == "note"


def test_event_email_creates_calendar_entry_with_ai_review_prefix():
    _, _, projects, matching, tasks, notes, calendar, email, _ = build_context()
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=False,
            input_emails=[
                EmailMessage(
                    id="event-1",
                    thread_id="ev-1",
                    subject="Schedule project sync",
                    sender="lead@example.com",
                    body="Let's schedule a meeting tomorrow at 10am.",
                    labels=["event"],
                )
            ],
        )
    )
    assert result.results[0].created_event is not None
    assert result.results[0].created_event.event.title.startswith("AI Review:")


def test_event_email_uses_ai_extracted_event_details():
    settings, _, projects, matching, tasks, notes, calendar, _, _ = build_context()
    settings.llm.enabled = True
    settings.llm.use_for_email_analysis = True
    llm = FakeLLMClient(
        response_map={
            "Board meeting": {
                "summary": "Board meeting to review Q2 outlook.",
                "outline": ["Agenda review", "Budget"],
                "action_items": ["Join board meeting"],
                "events": ["Board meeting Friday 3pm"],
                "suggested_title": "Board meeting",
                "suggested_project_name": "Project Alpha",
                "suggested_contexts": ["Computer"],
                "suggested_importance": 90,
                "suggested_time_required": 60,
                "event_start_iso": "2026-03-27T15:00:00",
                "event_end_iso": "2026-03-27T16:00:00",
                "event_location": "HQ Room 4",
                "event_description": "Quarterly board sync with finance update.",
                "rationale": ["Detected explicit date/time/location details."],
            }
        }
    )
    email = EmailService(
        FakeGmailClient(),
        settings,
        analysis_service=EmailAnalysisService(llm_client=llm, settings=settings),
    )
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=False,
            input_emails=[
                EmailMessage(
                    id="event-ai-1",
                    thread_id="ev-ai-1",
                    subject="Board meeting",
                    sender="ceo@example.com",
                    body="Board meeting Friday 3pm at HQ Room 4. Quarterly board sync with finance update.",
                    labels=["event"],
                )
            ],
        )
    )
    event = result.results[0].created_event
    assert event is not None
    assert event.event.start == "2026-03-27T15:00:00"
    assert event.event.end == "2026-03-27T16:00:00"
    assert event.event.location == "HQ Room 4"
    assert event.event.description == "Quarterly board sync with finance update."


def test_process_single_input_email_without_gmail_fetch():
    _, _, projects, matching, tasks, notes, calendar, email, _ = build_context()
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=True,
            input_emails=[
                EmailMessage(
                    id="manual-1",
                    thread_id="thread-1",
                    subject="Project Alpha: please review proposal",
                    sender="boss@example.com",
                    body="Please review the attached proposal and send feedback by tomorrow.",
                )
            ],
        )
    )
    assert result.processed_count == 1
    assert result.results[0].analysis is not None
    assert result.results[0].created_task is not None


def test_mark_processed_labels_are_applied_on_commit_mode():
    _, _, projects, matching, tasks, notes, calendar, email, _ = build_context()
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=False,
            mark_processed=True,
            input_emails=[
                EmailMessage(
                    id="proc-1",
                    thread_id="proc-thread-1",
                    subject="Project Alpha: follow up",
                    sender="lead@example.com",
                    body="Please follow up on this task.",
                    labels=["task"],
                )
            ],
        )
    )
    assert result.processed_count == 1
    assert "proc-1" in email.gmail.processed


def test_gmail_extract_text_body_reads_nested_plain_part():
    client = GmailClient()
    detail = {
        "id": "m1",
        "payload": {
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": "SGVsbG8gZnJvbSBuZXN0ZWQgcGFydA"},
                        }
                    ],
                }
            ]
        },
        "snippet": "snippet fallback",
    }
    extracted = client._extract_text_body(detail)
    assert extracted == "Hello from nested part"


def test_email_workflow_uses_only_active_projects_for_matching():
    _, _, projects, matching, tasks, notes, calendar, email, _ = build_context()
    active = projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1", status="Active"))
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1", status="Done"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=True,
            input_emails=[
                EmailMessage(
                    id="manual-active-1",
                    thread_id="thread-active-1",
                    subject="Project Alpha: follow up",
                    sender="lead@example.com",
                    body="Please follow up on this task.",
                )
            ],
        )
    )
    assert result.results[0].created_task is not None
    assert result.results[0].created_task.task is not None
    assert result.results[0].created_task.task.project_id == active.id


def test_email_workflow_can_create_review_tagged_project_when_missing():
    settings, notion, projects, matching, tasks, notes, calendar, email, _ = build_context()
    settings.contexts_db.database_id = "contexts-db"
    notion.create_page("contexts-db", {settings.contexts_db.title_property: "Computer", settings.contexts_db.status_property: "Active"})
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=False,
            create_project_if_missing=True,
            input_emails=[
                EmailMessage(
                    id="manual-new-1",
                    thread_id="thread-new-1",
                    subject="Brand New Initiative: please review and respond",
                    sender="ceo@example.com",
                    body="Please review this and send a response by tomorrow.",
                )
            ],
        )
    )
    assert result.results[0].created_project is not None
    assert settings.review_project_tag in result.results[0].created_project.tags
    assert any(item.item_type == "project_creation" for item in result.results[0].review_items)


def test_email_attachments_are_added_as_drive_links_in_notes():
    settings, notion, projects, matching, tasks, notes, calendar, email, _ = build_context()
    settings.attachments.mode = "drive_link"
    settings.contexts_db.database_id = "contexts-db"
    notion.create_page("contexts-db", {settings.contexts_db.title_property: "Computer", settings.contexts_db.status_property: "Active"})
    drive = FakeDriveClient()
    email.drive = drive
    projects.create_project(ProjectCreateInput(title="Project Alpha", area_id="area-1", status="Active"))
    workflow = ProcessEmailsWorkflow(
        {
            "email_service": email,
            "matching_service": matching,
            "project_service": projects,
            "task_service": tasks,
            "note_service": notes,
            "calendar_service": calendar,
        }
    )
    result = workflow.run(
        ProcessEmailsInput(
            preview_only=False,
            input_emails=[
                EmailMessage(
                    id="manual-att-1",
                    thread_id="thread-att-1",
                    subject="Project Alpha: task with attachment",
                    sender="ops@example.com",
                    body="Please action this request.",
                    attachments=[make_attachment("details.txt", "Important details")],
                )
            ],
        )
    )
    assert result.results[0].created_task is not None
    assert result.results[0].created_task.task is not None
    notes_text = result.results[0].created_task.task.notes or ""
    assert "## Attachments" in notes_text
    assert "drive.google.com" in notes_text
    assert len(drive.uploads) == 1


def test_area_tree_is_built_with_full_paths():
    settings, notion, projects, *_ = build_context()
    _, _, leaf = seed_area_tree(settings, notion)
    areas = projects.list_areas()
    platform = next(item for item in areas if item.id == leaf["id"])
    assert platform.path == "Work / Engineering / Platform"


def test_project_and_note_can_auto_match_area_from_tree():
    settings, notion, projects, matching, tasks, notes, *_ = build_context()
    _, _, leaf = seed_area_tree(settings, notion)
    settings.projects_db.require_area = True
    project = projects.create_project(ProjectCreateInput(title="Platform Stability", area_name="Work Engineering Platform"))
    assert project.area_id == leaf["id"]
    note_result = notes.create_note(NoteCreateInput(title="Platform incident writeup", area_name="Work / Engineering / Platform"))
    assert note_result.note is not None
    assert note_result.note.area_id == leaf["id"]


def test_email_analysis_uses_llm_when_enabled():
    settings, *_ = build_context()
    settings.llm.enabled = True
    settings.llm.use_for_email_analysis = True
    llm = FakeLLMClient(
        response_map={
            "submit hospital work hours": {
                "summary": "Submit residency work hours.",
                "action_items": ["Submit hospital work hours for residency project"],
                "suggested_title": "Submit residency hours",
                "suggested_project_name": "Residency",
                "suggested_contexts": ["Computer"],
                "suggested_importance": 90,
                "suggested_time_required": 20,
                "rationale": ["Mapped domain language to known project intent."],
            }
        }
    )
    analysis_service = EmailAnalysisService(llm_client=llm, settings=settings)
    analysis = analysis_service.analyze_email(
        EmailMessage(
            id="e-llm-1",
            thread_id="t-llm-1",
            subject="Please submit hospital work hours",
            sender="coordinator@example.com",
            body="Please submit hospital work hours for project residency.",
        )
    )
    assert analysis.suggested_project_name == "Residency"
    assert analysis.action_items
    assert llm.calls


def test_ambiguous_project_matching_uses_cheap_llm_tier():
    settings, notion, projects, *_ = build_context()
    settings.llm.enabled = True
    settings.llm.use_for_ambiguous_matching = True
    llm = FakeLLMClient(default_response={"selected": "Residency Rotation Tracking"})
    matching = MatchingService(settings, llm_client=llm)
    projects.create_project(ProjectCreateInput(title="Residency Rotation Tracking", area_id="area-1"))
    projects.create_project(ProjectCreateInput(title="Hospital Payroll", area_id="area-1"))
    result = matching.match_project("submit hospital work hours for residency", projects.list_projects())
    assert result.matched is True
    assert result.selected_project is not None
    assert result.selected_project.title == "Residency Rotation Tracking"
    assert llm.calls
    assert llm.calls[0]["model"] == settings.llm.cheap_model
    assert llm.calls[0]["operation"] == "ambiguous_match"


def test_llm_factory_selects_gemini_provider():
    settings = get_settings()
    settings.llm.enabled = True
    settings.llm.provider = "gemini"
    settings.llm.gemini_api_key = "test-gemini-key"
    selection = create_llm_client(settings.llm)
    assert selection.client is not None
    assert selection.provider == "gemini"


def test_llm_factory_selects_xai_provider():
    settings = get_settings()
    settings.llm.enabled = True
    settings.llm.provider = "xai"
    settings.llm.xai_api_key = "test-xai-key"
    selection = create_llm_client(settings.llm)
    assert selection.client is not None
    assert selection.provider == "xai"


def test_llm_factory_selects_anthropic_provider():
    settings = get_settings()
    settings.llm.enabled = True
    settings.llm.provider = "anthropic"
    settings.llm.anthropic_api_key = "test-anthropic-key"
    selection = create_llm_client(settings.llm)
    assert selection.client is not None
    assert selection.provider == "anthropic"


def test_cost_service_tier_selection_and_summary(tmp_path):
    settings = get_settings()
    settings.llm.cost_ledger_path = str(tmp_path / "ai_costs.jsonl")
    settings.llm.cheap_model = "gpt-5-nano"
    settings.llm.standard_model = "gpt-5-mini"
    settings.llm.premium_model = "gpt-5"
    cost = CostService(settings.llm)
    assert cost.get_tier_model("fast") == "gpt-5-nano"
    assert cost.get_tier_model("balanced") == "gpt-5-mini"
    assert cost.get_tier_model("smart") == "gpt-5"
    event = cost.record_usage(
        provider="openai",
        model="gpt-5-mini",
        operation="email_analysis",
        input_tokens=1000,
        output_tokens=500,
    )
    assert event["estimated_cost"] > 0
    summary = cost.summarize_usage()
    assert summary["event_count"] == 1
    assert summary["total_estimated_cost"] > 0


def test_cost_service_metadata_filtered_summary(tmp_path):
    settings = get_settings()
    settings.llm.cost_ledger_path = str(tmp_path / "ai_costs_filtered.jsonl")
    cost = CostService(settings.llm)
    cost.record_usage(
        provider="openai",
        model="gpt-5-mini",
        operation="email_analysis",
        input_tokens=100,
        output_tokens=50,
        metadata={"email_id": "e1"},
    )
    cost.record_usage(
        provider="openai",
        model="gpt-5-mini",
        operation="ambiguous_match",
        input_tokens=80,
        output_tokens=20,
        metadata={"email_id": "e2"},
    )
    filtered = cost.summarize_recent_usage(event_count=0, metadata_filter={"email_id": "e1"})
    assert filtered["event_count"] == 1
    assert filtered["events"][0]["metadata"]["email_id"] == "e1"


def test_cost_service_supports_new_gemini_models(tmp_path):
    settings = get_settings()
    settings.llm.cost_ledger_path = str(tmp_path / "ai_costs_gemini.jsonl")
    cost = CostService(settings.llm)
    value = cost.estimate_cost(
        provider="gemini",
        model="gemini-3.1-pro-preview",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert value == 14.0


def test_cost_service_supports_new_openai_flagship_models(tmp_path):
    settings = get_settings()
    settings.llm.cost_ledger_path = str(tmp_path / "ai_costs_openai.jsonl")
    cost = CostService(settings.llm)
    value = cost.estimate_cost(
        provider="openai",
        model="gpt-5.4-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert value == 5.25
