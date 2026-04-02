from __future__ import annotations

import logging

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:  # pragma: no cover
    from app.workflows.simple_graph import END, StateGraph

from app.schemas.tasks import ProcessTaskInboxInput, ProcessTaskInboxResult
from app.workflows.process_task_inbox.nodes import build_result, enrich_tasks, fetch_tasks
from app.workflows.process_task_inbox.state import ProcessTaskInboxState

logger = logging.getLogger(__name__)


class ProcessTaskInboxWorkflow:
    def __init__(self, deps: dict):
        self.deps = deps
        graph = StateGraph(ProcessTaskInboxState)
        graph.add_node("fetch_tasks", lambda state: fetch_tasks(state, self.deps))
        graph.add_node("enrich_tasks", lambda state: enrich_tasks(state, self.deps))
        graph.add_node("build_result", lambda state: build_result(state, self.deps))
        graph.set_entry_point("fetch_tasks")
        graph.add_edge("fetch_tasks", "enrich_tasks")
        graph.add_edge("enrich_tasks", "build_result")
        graph.add_edge("build_result", END)
        self.graph = graph.compile()

    def run(self, request: ProcessTaskInboxInput) -> ProcessTaskInboxResult:
        logger.info(
            "Starting process_task_inbox workflow.",
            extra={
                "event": "workflow.process_task_inbox.start",
                "context": {
                    "preview_only": request.preview_only,
                    "max_count": request.max_count,
                    "include_statuses": request.include_statuses,
                    "inbox_formula_property": request.inbox_formula_property,
                    "processed_tag": request.processed_tag,
                },
            },
        )
        self.deps["request"] = request
        state = self.graph.invoke({})
        result = state["result"]
        logger.info(
            "Completed process_task_inbox workflow.",
            extra={
                "event": "workflow.process_task_inbox.complete",
                "context": {
                    "preview_only": result.preview_only,
                    "processed_count": result.processed_count,
                    "updated_count": result.updated_count,
                    "created_projects": result.created_projects,
                    "review_item_count": sum(len(item.review_items) for item in result.results),
                },
            },
        )
        return result
