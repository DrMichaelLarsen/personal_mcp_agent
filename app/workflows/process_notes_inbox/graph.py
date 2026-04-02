from __future__ import annotations

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:  # pragma: no cover
    from app.workflows.simple_graph import END, StateGraph

from app.schemas.notes import ProcessNotesInboxInput, ProcessNotesInboxResult
from app.workflows.process_notes_inbox.nodes import build_result, enrich_notes, fetch_notes
from app.workflows.process_notes_inbox.state import ProcessNotesInboxState


class ProcessNotesInboxWorkflow:
    def __init__(self, deps: dict):
        self.deps = deps
        graph = StateGraph(ProcessNotesInboxState)
        graph.add_node("fetch_notes", lambda state: fetch_notes(state, self.deps))
        graph.add_node("enrich_notes", lambda state: enrich_notes(state, self.deps))
        graph.add_node("build_result", lambda state: build_result(state, self.deps))
        graph.set_entry_point("fetch_notes")
        graph.add_edge("fetch_notes", "enrich_notes")
        graph.add_edge("enrich_notes", "build_result")
        graph.add_edge("build_result", END)
        self.graph = graph.compile()

    def run(self, request: ProcessNotesInboxInput) -> ProcessNotesInboxResult:
        self.deps["request"] = request
        state = self.graph.invoke({})
        return state["result"]
