"""Provider selection - the only place that knows provider names."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.anthropic_api import AnthropicAPIProvider
from app.llm.base import LLMProvider
from app.llm.claude_cli import ClaudeCLIProvider
from app.llm.ollama import OllamaProvider
from app.llm.template import TemplateProvider

PROVIDERS = {
    "template": TemplateProvider,
    "claude_cli": ClaudeCLIProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicAPIProvider,
}


def get_provider(name: str | None = None, settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    key = (name or settings.llm_provider or "template").lower()
    try:
        provider_cls = PROVIDERS[key]
    except KeyError:
        raise ValueError(
            f"unknown LLM provider {key!r}; choose one of {', '.join(sorted(PROVIDERS))}"
        ) from None
    if provider_cls is TemplateProvider:
        return TemplateProvider()
    return provider_cls(settings)  # type: ignore[call-arg]
