from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import LLMConfig
    from app.services.cost_service import CostService


class LLMClient:
    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        operation: str = "general",
        metadata: dict | None = None,
    ) -> dict:
        raise NotImplementedError


class OpenAICompatibleLLMClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 30,
        provider_name: str = "openai",
        cost_service: "CostService | None" = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.provider_name = provider_name
        self.cost_service = cost_service

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        operation: str = "general",
        metadata: dict | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage", {})
        if self.cost_service:
            self.cost_service.record_usage(
                provider=self.provider_name,
                model=model,
                operation=operation,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                metadata={"endpoint": "chat_completions", **(metadata or {})},
            )
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


class GeminiLLMClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: int = 30,
        cost_service: "CostService | None" = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cost_service = cost_service

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        operation: str = "general",
        metadata: dict | None = None,
    ) -> dict:
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/models/{model}:generateContent",
                params={"key": self.api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        usage = data.get("usageMetadata", {})
        if self.cost_service:
            self.cost_service.record_usage(
                provider="gemini",
                model=model,
                operation=operation,
                input_tokens=int(usage.get("promptTokenCount") or 0),
                output_tokens=int(usage.get("candidatesTokenCount") or 0),
                metadata={"endpoint": "generateContent", **(metadata or {})},
            )
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(content)


class XaiLLMClient(OpenAICompatibleLLMClient):
    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1", timeout_seconds: int = 30, cost_service: "CostService | None" = None):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            provider_name="xai",
            cost_service=cost_service,
        )


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com", timeout_seconds: int = 30, cost_service: "CostService | None" = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cost_service = cost_service

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        operation: str = "general",
        metadata: dict | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "max_tokens": 1024,
            "temperature": 0.1,
            "system": system_prompt + "\nReturn valid JSON only.",
            "messages": [{"role": "user", "content": user_prompt}],
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage", {})
        if self.cost_service:
            self.cost_service.record_usage(
                provider="anthropic",
                model=model,
                operation=operation,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                metadata={"endpoint": "messages", **(metadata or {})},
            )
        text_blocks = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        content = "\n".join(text_blocks).strip()
        return json.loads(content)


@dataclass
class LLMClientSelection:
    client: LLMClient | None
    provider: str


def create_llm_client(config: "LLMConfig", cost_service: "CostService | None" = None) -> LLMClientSelection:
    if not config.enabled:
        return LLMClientSelection(client=None, provider="disabled")

    if config.provider == "gemini":
        api_key = config.gemini_api_key or config.api_key
        if not api_key:
            return LLMClientSelection(client=None, provider="gemini_missing_key")
        return LLMClientSelection(
            client=GeminiLLMClient(api_key=api_key, base_url=config.gemini_base_url, cost_service=cost_service),
            provider="gemini",
        )

    if config.provider == "xai":
        api_key = config.xai_api_key or config.api_key
        if not api_key:
            return LLMClientSelection(client=None, provider="xai_missing_key")
        return LLMClientSelection(
            client=XaiLLMClient(api_key=api_key, base_url=config.xai_base_url, cost_service=cost_service),
            provider="xai",
        )

    if config.provider == "anthropic":
        api_key = config.anthropic_api_key or config.api_key
        if not api_key:
            return LLMClientSelection(client=None, provider="anthropic_missing_key")
        return LLMClientSelection(
            client=AnthropicLLMClient(api_key=api_key, base_url=config.anthropic_base_url, cost_service=cost_service),
            provider="anthropic",
        )

    api_key = config.api_key
    if not api_key:
        return LLMClientSelection(client=None, provider="openai_missing_key")
    return LLMClientSelection(
        client=OpenAICompatibleLLMClient(
            api_key=api_key,
            base_url=config.base_url,
            provider_name="openai",
            cost_service=cost_service,
        ),
        provider=config.provider,
    )
