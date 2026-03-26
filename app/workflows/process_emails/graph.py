from __future__ import annotations

import logging

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without langgraph installed
    from app.workflows.simple_graph import END, StateGraph

from app.schemas.email import ProcessEmailsInput, ProcessEmailsResult
from app.workflows.process_emails.nodes import analyze_emails, build_results, classify_emails, extract_candidates, fetch_emails, match_projects
from app.workflows.process_emails.state import ProcessEmailsState

logger = logging.getLogger(__name__)


class ProcessEmailsWorkflow:
    def __init__(self, deps: dict):
        self.deps = deps
        graph = StateGraph(ProcessEmailsState)
        graph.add_node("fetch_emails", lambda state: fetch_emails(state, self.deps))
        graph.add_node("classify_emails", lambda state: classify_emails(state, self.deps))
        graph.add_node("analyze_emails", lambda state: analyze_emails(state, self.deps))
        graph.add_node("extract_candidates", lambda state: extract_candidates(state, self.deps))
        graph.add_node("match_projects", lambda state: match_projects(state, self.deps))
        graph.add_node("build_results", lambda state: build_results(state, self.deps))
        graph.set_entry_point("fetch_emails")
        graph.add_edge("fetch_emails", "classify_emails")
        graph.add_edge("classify_emails", "analyze_emails")
        graph.add_edge("analyze_emails", "extract_candidates")
        graph.add_edge("extract_candidates", "match_projects")
        graph.add_edge("match_projects", "build_results")
        graph.add_edge("build_results", END)
        self.graph = graph.compile()

    def run(self, request: ProcessEmailsInput) -> ProcessEmailsResult:
        logger.info(
            "Starting process_emails workflow.",
            extra={
                "event": "workflow.process_emails.start",
                "context": {
                    "preview_only": request.preview_only,
                    "max_count": request.max_count,
                    "confidence_threshold": request.confidence_threshold,
                    "mark_processed": request.mark_processed,
                    "has_input_emails": bool(request.input_emails),
                },
            },
        )
        self.deps["request"] = request
        state = self.graph.invoke({"preview_only": request.preview_only, "confidence_threshold": request.confidence_threshold})
        results = state.get("results", [])
        summary = {
            "emails": len(state.get("emails", [])),
            "tasks": sum(1 for item in results if item.created_task),
            "notes": sum(1 for item in results if item.created_note),
            "events": sum(1 for item in results if item.created_event),
            "review_required": sum(1 for item in results if item.review_items),
        }
        logger.info(
            "Completed process_emails workflow.",
            extra={
                "event": "workflow.process_emails.complete",
                "context": {
                    **summary,
                    "processed_count": len(results),
                    "preview_only": request.preview_only,
                },
            },
        )
        return ProcessEmailsResult(preview_only=request.preview_only, processed_count=len(results), results=results, summary=summary)
