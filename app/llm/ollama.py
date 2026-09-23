"""Local model provider (Ollama) - zero marginal cost, runs offline."""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client

    def complete(self, prompt: str) -> str:
        client = self._client or httpx.Client(timeout=300)
        try:
            response = client.post(
                f"{self.settings.ollama_host.rstrip('/')}/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.4},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        return response.json().get("response", "")
