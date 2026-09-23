"""Anthropic API provider - reserved for when API credits are available."""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAPIProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client

    def complete(self, prompt: str) -> str:
        if not self.settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        client = self._client or httpx.Client(timeout=120)
        try:
            response = client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": self.settings.anthropic_api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.settings.anthropic_model,
                    "max_tokens": 2000,
                    "temperature": 0.4,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic request failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        blocks = response.json().get("content", [])
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
