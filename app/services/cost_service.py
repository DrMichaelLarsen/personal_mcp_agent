from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import LLMConfig


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PricingEntry:
    provider: str
    model: str
    input_per_million: float
    output_per_million: float


DEFAULT_PRICING: list[PricingEntry] = [
    # OpenAI flagship pricing (standard, short-context rates)
    PricingEntry("openai", "gpt-5.4", 2.50, 15.00),
    PricingEntry("openai", "gpt-5.4-mini", 0.75, 4.50),
    PricingEntry("openai", "gpt-5.4-nano", 0.20, 1.25),
    PricingEntry("openai", "gpt-5.4-pro", 30.00, 180.00),
    # Backward-compatible aliases used by existing configs/tests
    PricingEntry("openai", "gpt-5", 2.50, 15.00),
    PricingEntry("openai", "gpt-5-mini", 0.75, 4.50),
    PricingEntry("openai", "gpt-5-nano", 0.20, 1.25),
    PricingEntry("openai", "gpt-5.3-chat-latest", 1.75, 14.00),
    PricingEntry("openai", "gpt-5.3-codex", 1.75, 14.00),
    PricingEntry("openai", "o4-mini", 1.10, 4.40),
    PricingEntry("openai", "gpt-4o-transcribe", 2.50, 10.00),
    PricingEntry("openai", "gpt-4o-mini-transcribe", 1.25, 5.00),
    PricingEntry("anthropic", "claude-3-5-haiku-20241022", 0.80, 4.00),
    PricingEntry("anthropic", "claude-sonnet-4-6", 3.00, 15.00),
    PricingEntry("anthropic", "claude-opus-4-6", 5.00, 25.00),
    PricingEntry("xai", "grok-4-1-fast-reasoning", 0.20, 0.50),
    PricingEntry("xai", "grok-4-0709", 3.00, 15.00),
    # Gemini model rates are based on standard text pricing at <=200k prompt size when tiered.
    PricingEntry("gemini", "gemini-3.1-pro-preview", 2.00, 12.00),
    PricingEntry("gemini", "gemini-3.1-pro-preview-customtools", 2.00, 12.00),
    PricingEntry("gemini", "gemini-3.1-flash-lite-preview", 0.25, 1.50),
    PricingEntry("gemini", "gemini-3-flash-preview", 0.50, 3.00),
    PricingEntry("gemini", "gemini-2.5-pro", 1.25, 10.00),
    PricingEntry("gemini", "gemini-2.5-flash", 0.30, 2.50),
    PricingEntry("gemini", "gemini-2.5-flash-lite", 0.10, 0.40),
    PricingEntry("gemini", "gemini-2.5-flash-lite-preview-09-2025", 0.10, 0.40),
    PricingEntry("gemini", "gemini-2.5-computer-use-preview-10-2025", 1.25, 10.00),
    PricingEntry("gemini", "gemini-2.0-flash", 0.10, 0.40),
    PricingEntry("gemini", "gemini-2.0-flash-lite", 0.075, 0.30),
    PricingEntry("gemini", "gemini-embedding-2-preview", 0.20, 0.0),
    PricingEntry("gemini", "gemini-embedding-001", 0.15, 0.0),
]


class CostService:
    def __init__(self, llm_config: LLMConfig):
        self.llm_config = llm_config
        self.ledger_path = Path(llm_config.cost_ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def get_pricing_table(self) -> list[dict]:
        return [entry.__dict__.copy() for entry in DEFAULT_PRICING]

    def get_tier_model(self, tier: str) -> str:
        if tier == "fast":
            return self.llm_config.cheap_model
        if tier == "balanced":
            return self.llm_config.standard_model
        if tier == "smart":
            return self.llm_config.premium_model
        if tier == "best":
            return self.llm_config.best_model or self.llm_config.premium_model
        return self.llm_config.standard_model

    def estimate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        for entry in DEFAULT_PRICING:
            if entry.provider == provider and entry.model == model:
                return round((input_tokens / 1_000_000) * entry.input_per_million + (output_tokens / 1_000_000) * entry.output_per_million, 8)
        return 0.0

    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        metadata: dict | None = None,
    ) -> dict:
        cost = self.estimate_cost(provider, model, input_tokens, output_tokens)
        event = {
            "timestamp": _utc_now_iso(),
            "provider": provider,
            "model": model,
            "operation": operation,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": cost,
            "metadata": metadata or {},
        }
        with self.ledger_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event) + "\n")
        return event

    def read_usage_events(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        events: list[dict] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def summarize_usage(self) -> dict:
        events = self.read_usage_events()
        total_cost = sum(event.get("estimated_cost", 0.0) for event in events)
        by_provider: dict[str, float] = {}
        for event in events:
            provider = event.get("provider", "unknown")
            by_provider[provider] = by_provider.get(provider, 0.0) + event.get("estimated_cost", 0.0)
        return {
            "event_count": len(events),
            "total_estimated_cost": round(total_cost, 8),
            "by_provider": {key: round(value, 8) for key, value in by_provider.items()},
        }

    def summarize_recent_usage(
        self,
        event_count: int,
        operation_prefix: str | None = None,
        metadata_filter: dict | None = None,
    ) -> dict:
        events = self.read_usage_events()
        if operation_prefix:
            events = [event for event in events if str(event.get("operation", "")).startswith(operation_prefix)]
        if metadata_filter:
            filtered: list[dict] = []
            for event in events:
                metadata = event.get("metadata", {}) or {}
                if all(metadata.get(key) == value for key, value in metadata_filter.items()):
                    filtered.append(event)
            events = filtered
        if event_count > 0:
            events = events[-event_count:]
        total_cost = sum(event.get("estimated_cost", 0.0) for event in events)
        input_tokens = sum(int(event.get("input_tokens", 0) or 0) for event in events)
        output_tokens = sum(int(event.get("output_tokens", 0) or 0) for event in events)
        return {
            "event_count": len(events),
            "total_estimated_cost": round(total_cost, 8),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "events": events,
        }

    def format_cost_summary_markdown(self, summary: dict) -> str:
        return (
            "## AI Cost Summary\n\n"
            f"- Estimated Cost: ${summary.get('total_estimated_cost', 0.0):.6f}\n"
            f"- Input Tokens: {summary.get('input_tokens', 0)}\n"
            f"- Output Tokens: {summary.get('output_tokens', 0)}\n"
            f"- AI Calls: {summary.get('event_count', 0)}"
        )
