"""End-to-end: fixture leads -> review queue -> approval -> send."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.lead import Lead
from app.models.outreach import EmailDraft, SendRecord
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus
from app.outreach import approval, suppression
from app.outreach.sender import SendResult


class RecordingProvider:
    """Stand-in for a real mailbox: records instead of delivering."""

    name = "recording"

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str, list[Path]]] = []
        self.fail = fail

    def send(self, recipient, subject, text, html, attachments=None):
        self.sent.append((recipient, subject, list(attachments or [])))
        if self.fail:
            return SendResult(status="SEND_FAILED", provider=self.name, error="mailbox unavailable")
        return SendResult(status="SENT", message_id="<test@local>", provider=self.name)


@pytest.fixture
def run_to_review(session, campaign, settings):
    summaries = pipeline.run_campaign(
        session, campaign, demo_limit=5, skip_video=True, settings=settings
    )
    session.flush()
    return {summary.stage: summary for summary in summaries}


def test_full_run_reaches_the_review_queue_without_sending(run_to_review, session, settings):
    stages = run_to_review
    assert stages["DISCOVERY"].succeeded == 2  # the hospital is excluded by the campaign
    assert stages["ENRICHMENT"].succeeded == 2
    assert stages["SCORING"].succeeded == 2
    assert stages["PERSONALIZATION"].succeeded == 1  # only the qualified lead
    assert stages["DEMO"].succeeded == 1
    assert stages["EMAIL"].succeeded == 1

    leads = {lead.organization_name: lead for lead in session.execute(select(Lead)).scalars()}
    premium = leads["ABC Dermatology, P.C."]
    assert premium.score == 100 and premium.tier == "premium"
    assert premium.lead_status is LeadStatus.REVIEW_REQUIRED

    # The clinic that already runs a chat widget is parked, not contacted.
    assert leads["Bayview Family Medicine"].lead_status is LeadStatus.BACKLOG

    # Artifacts exist on disk.
    assert (settings.demos_dir / f"{premium.id}.html").exists()
    assert (settings.demos_dir / f"{premium.id}.json").exists()

    # Nothing was sent.
    assert session.execute(select(SendRecord)).first() is None


def test_email_draft_is_can_spam_compliant(run_to_review, session, settings):
    draft = session.execute(select(EmailDraft)).scalar_one()
    assert draft.recipient == "info@abcdermatology.example"
    assert settings.sender_postal_address in draft.body_text
    assert settings.unsubscribe_mailto in draft.body_text
    assert 40 <= draft.word_count <= 160
    assert draft.approved_at is None


def test_send_refuses_leads_that_were_never_approved(run_to_review, session, campaign, settings):
    provider = RecordingProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert summary.processed == 0
    assert provider.sent == []


def test_approved_lead_is_sent_once_and_never_resent(run_to_review, session, campaign, settings):
    lead = session.execute(
        select(Lead).where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
    ).scalar_one()
    approval.approve(session, lead, approved_by="tester")
    session.flush()

    provider = RecordingProvider()
    first = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert first.succeeded == 1
    assert provider.sent[0][0] == "info@abcdermatology.example"
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SENT

    # A second pass must not contact the same clinic again.
    second = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert second.processed == 0
    assert len(provider.sent) == 1


def test_rejected_lead_leaves_the_queue(run_to_review, session):
    lead = session.execute(
        select(Lead).where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
    ).scalar_one()
    approval.reject(session, lead, "wrong specialty")
    session.flush()
    assert lead.lead_status is LeadStatus.REJECTED
    assert approval.pending(session) == []


def test_suppressed_recipient_is_never_emailed(run_to_review, session, campaign, settings):
    lead = session.execute(
        select(Lead).where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
    ).scalar_one()
    approval.approve(session, lead)
    suppression.add(session, "info@abcdermatology.example", reason="UNSUBSCRIBED")
    session.flush()

    provider = RecordingProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert provider.sent == []
    assert summary.skipped == 1
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SUPPRESSED


def test_failed_send_is_recorded_and_not_retried_blindly(run_to_review, session, campaign, settings):
    lead = session.execute(
        select(Lead).where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
    ).scalar_one()
    approval.approve(session, lead)
    session.flush()

    summary = pipeline.send_approved(
        session, campaign, provider=RecordingProvider(fail=True), settings=settings
    )
    assert summary.failed == 1
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SEND_FAILED
    record = session.execute(select(SendRecord)).scalar_one()
    assert record.status == "SEND_FAILED" and record.error


def test_pipeline_stages_are_idempotent(run_to_review, session, campaign, settings):
    before = session.execute(select(Lead)).scalars().all()
    again = pipeline.run_campaign(
        session, campaign, demo_limit=5, skip_video=True, settings=settings
    )
    stages = {summary.stage: summary for summary in again}
    assert stages["DISCOVERY"].succeeded == 0 and stages["DISCOVERY"].skipped == 2
    assert stages["PERSONALIZATION"].processed == 0
    assert stages["EMAIL"].processed == 0
    assert len(session.execute(select(Lead)).scalars().all()) == len(before)
    assert len(session.execute(select(EmailDraft)).scalars().all()) == 1


def test_lead_without_a_website_stays_partial_rather_than_being_invented(
    session, campaign, settings
):
    campaign.fixture_path = None
    lead = Lead(organization_name="No Site Clinic", dedupe_key="test:nosite", campaign_id=campaign.id)
    session.add(lead)
    session.flush()
    summary = pipeline.enrich(session, campaign, settings=settings)
    assert summary.skipped == 1
    assert lead.lead_status is LeadStatus.ENRICHMENT_PARTIAL
    assert lead.services == []
