"""Provider-agnostic structured generation.

The rest of the application never knows which provider is active: it asks for
a Pydantic model back and gets one, or the stage fails.  No free-form text
leaks out of this layer.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when a provider cannot produce valid, schema-conforming output."""


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    if not text or not text.strip():
        raise LLMError("empty response from provider")
    fenced = _FENCE.search(text)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"no JSON object in response: {candidate[:200]!r}") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("expected a JSON object at the top level")
    return parsed


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return raw model text for `prompt`."""

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        context: dict[str, Any] | None = None,
    ) -> T:
        """Generate, parse and validate.  One retry, then fail loudly.

        `context` carries the structured lead packet for providers that do not
        call a model (the deterministic template provider); model-backed
        providers ignore it - they only ever see `prompt`.
        """
        errors: list[str] = []
        for attempt in range(2):
            try:
                raw = self.complete(prompt if attempt == 0 else self._retry_prompt(prompt, errors[-1]))
                return schema.model_validate(extract_json(raw))
            except (LLMError, ValidationError) as exc:
                errors.append(str(exc))
        raise LLMError(
            f"{self.name} failed to produce valid {schema.__name__} after 2 attempts: {errors[-1]}"
        )

    @staticmethod
    def _retry_prompt(prompt: str, error: str) -> str:
        return (
            f"{prompt}\n\nYour previous response was rejected: {error}\n"
            "Respond with a single valid JSON object and nothing else - no prose, no code fences."
        )
