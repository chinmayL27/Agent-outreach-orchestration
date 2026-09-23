"""End-to-end: fixture campaign -> review queue -> approval -> send.

These assert the PRD's MVP success criteria directly: 20 leads processed, at
least five complete packages, one command reaching REVIEW_REQUIRED, no send
without approval, and resumability without duplicates.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.lead import Lead
from app.models.outreach import CampaignRun, DemoArtifact, EmailDraft, SendRecord, VideoArtifact
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus
from app.outreach import approval, suppression
from app.outreach.sender import SendResult


class RecordingProvider:
    """Stand-in for a real mailbox: records instead of delivering."""

    name = "recording"
    requires_public_links = False

    def __init__(self, status: str = "SENT", error: str | None = None):
        self.sent: list[tuple[str, str, list]] = []
        self.status = status
        self.error = error

    def send(self, recipient, subject, text, html, attachments=None):
        self.sent.append((recipient, subject, list(attachments or [])))
        if self.status == "SENT":
            return SendResult(status="SENT", message_id="<test@local>", provider=self.name)
        return SendResult(status=self.status, provider=self.name, error=self.error or "failure")


class StrictProvider(RecordingProvider):
    """A provider that really delivers, so links must be publicly reachable."""

    name = "strict"
    requires_public_links = True


@pytest.fixture
def run_to_review(session, campaign, settings):
    summaries = pipeline.run_campaign(
        session, campaign, demo_limit=10, skip_video=True, settings=settings
    )
    session.flush()
    return {summary.stage: summary for summary in summaries}


def _review_leads(session) -> list[Lead]:
    return list(
        session.execute(
            select(Lead)
            .where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
            .order_by(Lead.score.desc())
        ).scalars()
    )


def _top_lead(session) -> Lead:
    return _review_leads(session)[0]


# ---------------------------------------------------------------------------
# PRD section 5 - MVP success criteria
# ---------------------------------------------------------------------------
def test_one_command_processes_twenty_leads_into_the_review_queue(run_to_review, session, settings):
    stages = run_to_review
    assert stages["DISCOVERY"].succeeded == 20, "PRD: process at least 20 leads"

    leads = list(session.execute(select(Lead)).scalars())
    assert len(leads) == 20

    review_ready = _review_leads(session)
    premium = [lead for lead in review_ready if lead.tier == "premium"]
    assert len(premium) >= 5, "PRD: complete packages for at least five qualified leads"

    for lead in premium:
        demo = session.execute(
            select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
        ).scalar_one()
        assert (settings.demos_dir / f"{lead.id}.html").exists()
        assert demo.content_hash
        assert session.execute(
            select(EmailDraft).where(EmailDraft.lead_id == lead.id)
        ).scalar_one()

    # Nothing was sent by the generation run.
    assert session.execute(select(SendRecord)).first() is None


def test_unqualified_leads_stay_inspectable_in_the_backlog(run_to_review, session):
    backlog = list(
        session.execute(select(Lead).where(Lead.status == LeadStatus.BACKLOG.value)).scalars()
    )
    assert backlog, "low-scoring leads remain inspectable rather than disappearing"
    for lead in backlog:
        assert lead.score < 60
        assert lead.score_breakdown, "every score explains itself"


def test_leads_without_a_website_are_partial_not_invented(run_to_review, session):
    partial = list(
        session.execute(
            select(Lead).where(Lead.status.in_(["ENRICHMENT_PARTIAL", "BACKLOG"]), Lead.website.is_(None))
        ).scalars()
    )
    assert partial, "the registry has no website field; those leads must survive"
    for lead in partial:
        assert lead.services == []
        assert lead.has_chatbot is None, "unknown must not become False"


def test_basic_tier_skips_demo_and_video(run_to_review, session):
    """60-79 scores get an email only (TDD s7)."""
    basic = [lead for lead in _review_leads(session) if lead.tier == "basic"]
    assert basic, "the fixture must include a basic-tier lead"
    for lead in basic:
        assert session.execute(
            select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
        ).scalar_one_or_none() is None
        assert session.execute(
            select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
        ).scalar_one_or_none() is None
        assert session.execute(
            select(EmailDraft).where(EmailDraft.lead_id == lead.id)
        ).scalar_one()


def test_campaign_run_is_recorded(run_to_review, session, campaign):
    run = session.execute(select(CampaignRun)).scalar_one()
    assert run.campaign_id == campaign.id
    assert run.status == "COMPLETED"
    assert run.finished_at is not None
    assert run.config["maximum_leads"] == campaign.maximum_leads
    assert run.summary["DISCOVERY"]["succeeded"] == 20


def test_email_drafts_are_can_spam_compliant(run_to_review, session, settings):
    drafts = list(session.execute(select(EmailDraft)).scalars())
    assert len(drafts) >= 6
    for draft in drafts:
        assert "@" in draft.recipient
        assert settings.sender_postal_address in draft.body_text
        assert settings.unsubscribe_mailto in draft.body_text
        assert 40 <= draft.word_count <= 160
        assert draft.approved_at is None
        assert draft.package_version == 1


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------
def test_send_refuses_leads_that_were_never_approved(run_to_review, session, campaign, settings):
    provider = RecordingProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert summary.processed == 0
    assert provider.sent == []


def test_approved_lead_is_sent_once_and_never_resent(run_to_review, session, campaign, settings):
    lead = _top_lead(session)
    approval.approve(session, lead, approved_by="tester")
    session.flush()

    provider = RecordingProvider()
    first = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert first.succeeded == 1
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SENT

    second = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert second.processed == 0
    assert len(provider.sent) == 1


def test_editing_after_approval_invalidates_it(run_to_review, session, campaign, settings):
    """A material edit sends the package back to review (TDD s7/s15)."""
    lead = _top_lead(session)
    draft = approval.approve(session, lead, approved_by="tester")
    session.flush()
    assert draft.approval_is_current

    approval.edit(session, lead, subject="A different subject entirely")
    session.flush()

    assert draft.approved_at is None
    assert draft.package_version == 2
    assert not draft.approval_is_current
    assert lead.lead_status is LeadStatus.REVIEW_REQUIRED

    provider = RecordingProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert provider.sent == []
    assert summary.processed == 0


def test_regenerating_a_package_invalidates_approval(run_to_review, session, campaign, settings):
    lead = _top_lead(session)
    draft = approval.approve(session, lead)
    session.flush()

    # A changed email body is a new package version.
    personalization = session.execute(
        select(pipeline.Personalization).where(pipeline.Personalization.lead_id == lead.id)
    ).scalar_one()
    personalization.email_body = "Hi there,\n\n" + "A materially different message. " * 12
    session.flush()

    pipeline.build_emails(session, campaign, force=True, settings=settings)
    session.flush()

    assert draft.package_version == 2
    assert draft.approved_at is None
    provider = RecordingProvider()
    pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert provider.sent == []


def test_rejected_lead_leaves_the_queue(run_to_review, session):
    lead = _top_lead(session)
    approval.reject(session, lead, "wrong specialty")
    session.flush()
    assert lead.lead_status is LeadStatus.REJECTED
    assert lead.id not in {item.lead.id for item in approval.pending(session)}


# ---------------------------------------------------------------------------
# Send-time safety
# ---------------------------------------------------------------------------
def test_suppressed_recipient_is_never_emailed(run_to_review, session, campaign, settings):
    lead = _top_lead(session)
    draft = approval.approve(session, lead)
    suppression.add(session, draft.recipient, reason="UNSUBSCRIBED")
    session.flush()

    provider = RecordingProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert provider.sent == []
    assert summary.skipped >= 1
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SUPPRESSED


def test_real_provider_refuses_packages_with_unreachable_links(
    run_to_review, session, campaign, settings
):
    """localhost/file:// artifacts are useless to a clinic (TDD s16)."""
    lead = _top_lead(session)
    approval.approve(session, lead)
    session.flush()

    provider = StrictProvider()
    summary = pipeline.send_approved(session, campaign, provider=provider, settings=settings)
    assert provider.sent == []
    assert summary.skipped == 1
    assert any("not public" in note for note in summary.notes)


def test_failed_send_is_recorded_and_not_retried_blindly(run_to_review, session, campaign, settings):
    lead = _top_lead(session)
    approval.approve(session, lead)
    session.flush()

    summary = pipeline.send_approved(
        session, campaign, provider=RecordingProvider("SEND_FAILED", "mailbox unavailable"),
        settings=settings,
    )
    assert summary.failed == 1
    assert session.get(Lead, lead.id).lead_status is LeadStatus.SEND_FAILED
    record = session.execute(select(SendRecord)).scalar_one()
    assert record.status == "SEND_FAILED" and record.error


def test_uncertain_send_is_distinct_and_never_auto_resent(run_to_review, session, campaign, settings):
    """Provider acceptance and delivery are different events (TDD s7/s17)."""
    lead = _top_lead(session)
    approval.approve(session, lead)
    session.flush()

    uncertain = RecordingProvider("SEND_UNCERTAIN", "connection dropped after DATA")
    pipeline.send_approved(session, campaign, provider=uncertain, settings=settings)
    session.flush()

    assert session.get(Lead, lead.id).lead_status is LeadStatus.SEND_UNCERTAIN
    record = session.execute(select(SendRecord)).scalar_one()
    assert record.status == "SEND_UNCERTAIN"

    # A second pass must not gamble on a duplicate delivery.
    retry = RecordingProvider()
    pipeline.send_approved(session, campaign, provider=retry, settings=settings)
    assert retry.sent == []


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------
def test_pipeline_stages_are_idempotent(run_to_review, session, campaign, settings):
    before_leads = session.execute(select(func.count()).select_from(Lead)).scalar_one()
    before_drafts = session.execute(select(func.count()).select_from(EmailDraft)).scalar_one()

    again = pipeline.run_campaign(
        session, campaign, demo_limit=10, skip_video=True, settings=settings
    )
    stages = {summary.stage: summary for summary in again}
    assert stages["DISCOVERY"].succeeded == 0 and stages["DISCOVERY"].skipped == 20
    assert stages["PERSONALIZATION"].processed == 0
    assert stages["EMAIL"].processed == 0

    assert session.execute(select(func.count()).select_from(Lead)).scalar_one() == before_leads
    assert session.execute(select(func.count()).select_from(EmailDraft)).scalar_one() == before_drafts


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
