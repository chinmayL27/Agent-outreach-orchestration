"""Email assembly, CAN-SPAM requirements and the console sender."""

from __future__ import annotations

import pytest

from app.models.lead import Lead
from app.models.outreach import Personalization
from app.outreach.email_generator import EmailValidationError, render_email
from app.outreach.sender import ConsoleEmailProvider, get_email_provider


def sample_lead() -> Lead:
    return Lead(
        organization_name="ABC Dermatology",
        dedupe_key="test:abc",
        emails=["info@abcdermatology.example"],
        city="San Jose",
        state="CA",
    )


def sample_personalization() -> Personalization:
    return Personalization(
        lead_id="lead-1",
        sales_angle="angle",
        email_subject="A 60-second assistant demo for ABC Dermatology",
        email_body="Hi there,\n\nI looked at your site and noticed two locations.\n\nWorth 15 minutes?",
        video_intro="i",
        video_outro="o",
    )


def test_render_email_appends_links_and_the_legal_footer(settings):
    rendered = render_email(
        sample_lead(),
        sample_personalization(),
        demo_url="https://demos.testco.example/lead-1.html",
        video_note="60-second walkthrough attached: lead-1.mp4",
        settings=settings,
    )
    assert rendered.recipient == "info@abcdermatology.example"
    assert "https://demos.testco.example/lead-1.html" in rendered.body_text
    assert "lead-1.mp4" in rendered.body_text
    assert rendered.links_public is True
    assert settings.sender_postal_address in rendered.body_text
    assert settings.unsubscribe_mailto in rendered.body_text
    assert "<a href=" in rendered.body_html
    assert rendered.word_count > 0


def test_localhost_and_file_links_are_never_written_into_an_email(settings):
    """A recipient cannot open the operator's laptop (TDD s16)."""
    for unusable in ("http://localhost:8000/demo/lead-1", "file:///tmp/demo/lead-1.html"):
        rendered = render_email(
            sample_lead(), sample_personalization(), demo_url=unusable, settings=settings
        )
        assert unusable not in rendered.body_text
        assert unusable not in rendered.body_html
        assert rendered.links_public is False
        assert rendered.demo_url is None


def test_render_email_requires_a_recipient(settings):
    lead = sample_lead()
    lead.emails = []
    with pytest.raises(EmailValidationError):
        render_email(lead, sample_personalization(), settings=settings)


def test_render_email_requires_a_postal_address(settings, monkeypatch):
    from app.config import Settings

    stripped = Settings(**{**settings.__dict__, "sender_postal_address": ""})
    with pytest.raises(EmailValidationError):
        render_email(sample_lead(), sample_personalization(), settings=stripped)


def test_email_body_html_escapes_generated_copy(settings):
    personalization = sample_personalization()
    personalization.email_body = "Hi <script>alert(1)</script>"
    rendered = render_email(sample_lead(), personalization, settings=settings)
    assert "<script>" not in rendered.body_html
    assert "&lt;script&gt;" in rendered.body_html


def test_console_provider_writes_a_real_message_to_the_outbox(settings):
    provider = ConsoleEmailProvider(settings)
    result = provider.send("info@clinic.example", "Subject", "text body", "<p>html body</p>")
    assert result.ok and result.message_id
    files = list(settings.outbox_dir.glob("*.eml"))
    assert len(files) == 1
    raw = files[0].read_text()
    assert "To: info@clinic.example" in raw
    assert "List-Unsubscribe:" in raw


def test_default_email_provider_is_the_console_one(settings):
    assert get_email_provider(settings=settings).name == "console"
