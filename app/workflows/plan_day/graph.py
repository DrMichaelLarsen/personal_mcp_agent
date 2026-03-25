from __future__ import annotations

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without langgraph installed
    from app.workflows.simple_graph import END, StateGraph

from app.schemas.planning import DayPlanInput, DayPlanResult
from app.workflows.plan_day.nodes import build_plan, fetch_calendar, fetch_tasks
from app.workflows.plan_day.state import PlanDayState


class PlanDayWorkflow:
    def __init__(self, deps: dict):
        self.deps = deps
        graph = StateGraph(PlanDayState)
        graph.add_node("fetch_calendar", lambda state: fetch_calendar(state, self.deps))
        graph.add_node("fetch_tasks", lambda state: fetch_tasks(state, self.deps))
        graph.add_node("build_plan", lambda state: build_plan(state, self.deps))
        graph.set_entry_point("fetch_calendar")
        graph.add_edge("fetch_calendar", "fetch_tasks")
        graph.add_edge("fetch_tasks", "build_plan")
        graph.add_edge("build_plan", END)
        self.graph = graph.compile()

    def run(self, request: DayPlanInput) -> DayPlanResult:
        self.deps["request"] = request
        state = self.graph.invoke({})
        return state["result"]



