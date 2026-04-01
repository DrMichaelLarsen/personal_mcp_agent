from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfidenceThresholds(BaseModel):
    auto_create: float = 0.85
    review_required: float = 0.65


class WorkdayConfig(BaseModel):
    start_hour: int = 9
    end_hour: int = 17
    lunch_minutes: int = 30
    buffer_minutes: int = 15


class NotionDatabaseConfig(BaseModel):
    database_id: str = ""
    title_property: str = "Name"
    status_property: str | None = "Status"
    priority_property: str | None = "Priority"
    importance_property: str | None = "Importance"
    scheduled_property: str | None = "Scheduled"
    deadline_property: str | None = "Deadline"
    estimate_property: str | None = "Time Required"
    relation_property: str | None = "Project"
    contexts_property: str | None = "Contexts"
    assigned_property: str | None = "Assigned"
    phone_property: str | None = "Phone"
    budget_property: str | None = "Budget"
    goal_property: str | None = "Goal"
    parent_property: str | None = "parent"
    dependency_of_property: str | None = "dependency_of"
    depends_on_property: str | None = "depends_on"
    score_property: str | None = "Score"
    area_property: str | None = "Area"
    parent_project_property: str | None = "Parent Project"
    target_deadline_property: str | None = "Target Deadline"
    budget_property: str | None = "Budget"
    priority_checkbox_property: str | None = "Priority"
    tags_property: str | None = "Tags"
    url_property: str | None = "URL"
    notes_property: str | None = "Description"
    description_property: str | None = None
    source_id_property: str | None = "Source ID"
    ai_cost_property: str | None = "ai_cost"
    store_content_in_property: bool = True
    allowed_statuses: list[str] = Field(
        default_factory=lambda: ["To Do", "Not started", "In Progress", "Completed", "Done"]
    )
    default_status: str = "To Do"
    default_priority: Literal["low", "medium", "high"] = "medium"
    default_importance: int = 50
    require_area: bool = False
    allow_tasks_without_project: bool = True


class GmailConfig(BaseModel):
    credentials_path: str | None = None
    token_path: str | None = None
    label_query: str = "label:Actionable -label:Processed"
    processed_label: str = "Processed"


class CalendarConfig(BaseModel):
    credentials_path: str | None = None
    token_path: str | None = None
    calendar_id: str = "primary"
    timezone: str = "America/Denver"


class AttachmentConfig(BaseModel):
    mode: Literal["none", "drive_link", "notion_file"] = "none"
    max_attachments_per_email: int = 10
    max_attachment_size_mb: int = 20
    include_links_in_notes: bool = True
    drive_folder_id: str | None = None


class LLMConfig(BaseModel):
    enabled: bool = False
    provider: Literal["openai_compatible", "openai", "gemini", "xai", "anthropic"] = "openai_compatible"
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    xai_api_key: str | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    cheap_model: str = "gpt-5.4-nano"
    standard_model: str = "gpt-5.4-mini"
    premium_model: str = "gpt-5.4"
    best_model: str | None = None
    use_for_email_analysis: bool = True
    use_for_task_inbox: bool = True
    use_for_ambiguous_matching: bool = True
    quality_tier: Literal["fast", "balanced", "smart", "best"] = "balanced"
    email_analysis_tier: Literal["fast", "balanced", "smart", "best"] = "balanced"
    task_inbox_tier: Literal["fast", "balanced", "smart", "best"] = "balanced"
    ambiguous_matching_tier: Literal["fast", "balanced", "smart", "best"] = "fast"
    cost_ledger_path: str = "data/ai_costs.jsonl"


class SenderRoutingRule(BaseModel):
    sender: str
    area_contains: list[str] = Field(default_factory=list)
    project_contains: list[str] = Field(default_factory=list)
    score_bonus: float = 0.2


class DomainRoutingRule(BaseModel):
    domain: str
    area_contains: list[str] = Field(default_factory=list)
    project_contains: list[str] = Field(default_factory=list)
    score_bonus: float = 0.15


class ProjectRoutingConfig(BaseModel):
    sender_rules: list[SenderRoutingRule] = Field(default_factory=list)
    domain_rules: list[DomainRoutingRule] = Field(default_factory=list)
    lexical_weight: float = 0.65
    profile_weight: float = 0.25
    sender_bias_weight: float = 1.0
    max_sender_bonus: float = 0.35


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PPMCP_",
        env_nested_delimiter="__",
        env_file=(".env", "/config/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "personal-productivity-mcp"
    environment: str = "dev"
    notion_api_key: str | None = None
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    attachments: AttachmentConfig = Field(default_factory=AttachmentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    project_routing: ProjectRoutingConfig = Field(default_factory=ProjectRoutingConfig)
    tasks_db: NotionDatabaseConfig = Field(default_factory=lambda: NotionDatabaseConfig(store_content_in_property=False))
    projects_db: NotionDatabaseConfig = Field(
        default_factory=lambda: NotionDatabaseConfig(default_status="Active", allowed_statuses=["Active", "On Hold", "Done"])
    )
    contexts_db: NotionDatabaseConfig = Field(
        default_factory=lambda: NotionDatabaseConfig(
            default_status="Active",
            allowed_statuses=["Active", "Archived"],
            relation_property=None,
            contexts_property=None,
            assigned_property=None,
        )
    )
    areas_db: NotionDatabaseConfig = Field(
        default_factory=lambda: NotionDatabaseConfig(
            default_status="Active",
            allowed_statuses=["Active", "Archived"],
            relation_property=None,
            contexts_property=None,
            assigned_property=None,
            parent_property="Parent Area",
            area_property=None,
        )
    )
    notes_db: NotionDatabaseConfig = Field(
        default_factory=lambda: NotionDatabaseConfig(
            status_property=None,
            importance_property=None,
            scheduled_property=None,
            deadline_property=None,
            store_content_in_property=False,
        )
    )
    project_completed_statuses: list[str] = Field(default_factory=lambda: ["done", "complete", "completed", "archived", "canceled", "cancelled"])
    context_active_statuses: list[str] = Field(default_factory=lambda: ["active"])
    area_active_statuses: list[str] = Field(default_factory=lambda: ["active"])
    review_project_tag: str = "Needs Review"
    task_inbox_processed_tag: str = "Inbox Processed"
    confidence: ConfidenceThresholds = Field(default_factory=ConfidenceThresholds)
    workday: WorkdayConfig = Field(default_factory=WorkdayConfig)


def get_settings(**overrides: Any) -> Settings:
    return Settings(**overrides) if overrides else Settings()
