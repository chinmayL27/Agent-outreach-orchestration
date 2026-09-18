"""LLM provider isolation and result caching (TDD s12)."""

from __future__ import annotations

import os

from sqlalchemy import select

from app.llm.claude_cli import ClaudeCLIProvider
from app.llm.template import TemplateProvider
from app.models.lead import Lead
from app.models.outreach import LLMCacheEntry, Personalization
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus


class CountingProvider(TemplateProvider):
    """Template provider that records how often it is asked to generate."""

    name = "counting"

    def __init__(self):
        self.calls = 0

    def generate_structured(self, prompt, schema, context=None):
        self.calls += 1
        return super().generate_structured(prompt, schema, context)


def test_validated_personalization_is_cached_and_reused(session, campaign, settings):
    """A downstream failure must not force paid regeneration (TDD s12/s20)."""
    pipeline.discover(session, campaign)
    pipeline.enrich(session, campaign, settings=settings)
    pipeline.score(session, campaign)
    session.flush()

    provider = CountingProvider()
    first = pipeline.personalize(session, campaign, provider=provider, limit=2, settings=settings)
    session.flush()
    assert first.succeeded >= 1
    generated = provider.calls
    assert generated >= 1

    cached = list(session.execute(select(LLMCacheEntry)).scalars())
    assert len(cached) == generated
    assert all(entry.schema_name == "PersonalizationOutput" for entry in cached)

    # Re-running the stage for the same packets reuses the cache.
    for lead in session.execute(
        select(Lead).where(Lead.status == LeadStatus.PERSONALIZED.value)
    ).scalars():
        session.delete(
            session.execute(
                select(Personalization).where(Personalization.lead_id == lead.id)
            ).scalar_one()
        )
        lead.status = LeadStatus.QUALIFIED.value
    session.flush()

    second = pipeline.personalize(session, campaign, provider=provider, limit=2, settings=settings)
    assert second.succeeded == first.succeeded
    assert provider.calls == generated, "cached packets must not hit the provider again"


def test_cache_key_changes_with_the_prompt_version(session, campaign, settings):
    from app.llm import prompts

    key_inputs = ({"a": 1}, {"b": 2}, prompts.PROMPT_VERSION, "template")
    original = pipeline.fingerprint(*key_inputs)
    bumped = pipeline.fingerprint({"a": 1}, {"b": 2}, "2", "template")
    assert original != bumped


def test_claude_cli_command_disables_tools_and_isolates_the_process(monkeypatch):
    """Website-derived text must never reach a tool-enabled subprocess."""
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = '{"result": "{\\"ok\\": true}"}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr("app.llm.claude_cli.shutil.which", lambda binary: "/usr/bin/claude")
    monkeypatch.setattr("app.llm.claude_cli.cli_help", lambda binary: "--disallowedTools  --output-format")
    monkeypatch.setattr("app.llm.claude_cli.subprocess.run", fake_run)
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")

    ClaudeCLIProvider().complete("a prompt containing untrusted website text")

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list), "argument array, never a shell string"
    assert kwargs["shell"] is False
    assert kwargs["timeout"] > 0
    assert "--disallowedTools" in command
    assert kwargs["cwd"] and kwargs["cwd"] != os.getcwd(), "runs in a throwaway directory"

    env = kwargs["env"]
    assert "SMTP_PASSWORD" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_claude_cli_skips_unknown_flags(monkeypatch):
    """Flag names are checked against the installed CLI, not assumed."""
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = '{"result": "{}"}'
        stderr = ""

    monkeypatch.setattr("app.llm.claude_cli.shutil.which", lambda binary: "/usr/bin/claude")
    monkeypatch.setattr("app.llm.claude_cli.cli_help", lambda binary: "--output-format only")
    monkeypatch.setattr(
        "app.llm.claude_cli.subprocess.run",
        lambda command, **kwargs: (captured.update(command=command), Completed())[1],
    )

    ClaudeCLIProvider().complete("prompt")
    assert "--disallowedTools" not in captured["command"]
    assert "--allowedTools" not in captured["command"]
