from __future__ import annotations

from collections.abc import Callable


END = "__end__"


class StateGraph:
    def __init__(self, _state_type):
        self.nodes: dict[str, Callable] = {}
        self.edges: dict[str, str] = {}
        self.entry_point: str | None = None

    def add_node(self, name: str, fn: Callable) -> None:
        self.nodes[name] = fn

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def compile(self):
        return _CompiledGraph(self.nodes, self.edges, self.entry_point)


class _CompiledGraph:
    def __init__(self, nodes: dict[str, Callable], edges: dict[str, str], entry_point: str | None):
        self.nodes = nodes
        self.edges = edges
        self.entry_point = entry_point

    def invoke(self, state: dict):
        if self.entry_point is None:
            return state
        current = self.entry_point
        current_state = state
        while current and current != END:
            current_state = self.nodes[current](current_state)
            current = self.edges.get(current)
        return current_state
