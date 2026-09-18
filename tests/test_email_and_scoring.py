"""Unit tests: email extraction, deterministic scoring, suppression."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.extraction.email import extract_emails, is_valid_email, rank_emails
from app.models.campaign import CampaignConfig
from app.outreach import suppression
from app.scoring.lead_score import score_lead


def test_extract_emails_filters_junk_and_files():
    text = (
        "Contact info@clinic.example or billing@clinic.example. "
        "Sentry key: abc@sentry.io. Logo: logo@2x.png. no-reply@clinic.example"
    )
    found = extract_emails(text, mailto_links=["mailto:front.desk@clinic.example?subject=Hi"])
    assert found[0] == "front.desk@clinic.example"  # mailto links win
    assert "info@clinic.example" in found
    assert "abc@sentry.io" not in found
    assert "no-reply@clinic.example" not in found
    assert not any(candidate.endswith(".png") for candidate in found)


def test_is_valid_email_rejects_placeholders():
    assert is_valid_email("info@clinic.example")
    assert not is_valid_email("info@example.com")
    assert not is_valid_email("not-an-email")


def test_rank_emails_prefers_general_inboxes_on_the_site_domain():
    ranked = rank_emails(
        ["billing@clinic.example", "info@clinic.example", "info@other.example"], "clinic.example"
    )
    assert ranked[0] == "info@clinic.example"
    assert ranked[-1] == "billing@clinic.example"


@dataclass
class LeadStub:
    specialty: list[str] = field(default_factory=lambda: ["Dermatology"])
    provider_names: list[str] = field(default_factory=lambda: ["A B, MD", "C D, MD"])
    provider_count: int | None = 4
    locations: list[str] = field(default_factory=lambda: ["San Jose, CA", "Campbell, CA"])
    has_online_booking: bool | None = True
    has_chatbot: bool | None = False
    booking_system: str | None = "Online request form"
    faq_questions: list[str] = field(default_factory=lambda: [f"Q{i}?" for i in range(6)])
    emails: list[str] = field(default_factory=lambda: ["info@clinic.example"])


def test_perfect_lead_scores_premium(campaign):
    result = score_lead(LeadStub(), campaign)
    assert result.score == 100
    assert result.tier == "premium"
    assert result.qualified


def test_existing_chatbot_and_no_email_drop_the_lead_to_backlog(campaign):
    lead = LeadStub(
        has_chatbot=True,
        has_online_booking=False,
        emails=[],
        locations=["Oakland, CA"],
        faq_questions=[],
        provider_count=0,
        provider_names=[],
    )
    result = score_lead(lead, campaign)
    assert result.score == 25  # specialty only
    assert result.tier == "backlog"
    assert not result.qualified


def test_scoring_weights_come_from_configuration(campaign):
    campaign.scoring.no_chatbot = 0
    assert score_lead(LeadStub(), campaign).score == 80


def test_unknown_chatbot_state_scores_no_points(campaign):
    """`None` means "we could not tell", which must not earn the no-chatbot points."""
    assert score_lead(LeadStub(has_chatbot=None), campaign).score == 80


def test_off_target_specialty_is_not_awarded():
    campaign = CampaignConfig(specialties=["Dermatology"])
    assert score_lead(LeadStub(specialty=["Podiatry"]), campaign).score == 75


def test_suppression_blocks_and_normalizes(session):
    suppression.add(session, "  Info@Clinic.Example ", reason="UNSUBSCRIBED")
    assert suppression.is_suppressed(session, "info@clinic.example")
    assert not suppression.is_suppressed(session, "other@clinic.example")
    try:
        suppression.assert_not_suppressed(session, "INFO@clinic.example")
    except suppression.SuppressedRecipient:
        pass
    else:  # pragma: no cover - the assertion above must raise
        raise AssertionError("suppressed recipient was allowed through")
