"""The orchestrator.

A plain pipeline of typed workers: each stage reads structured state from the
database, does one job, writes structured state back, and records an event.
Stages are idempotent and isolate failures per lead - one bad website never
stops the batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.demo.config_generator import build_demo_config, write_demo_config
from app.demo.renderer import demo_file_url, demo_url, write_demo_html
from app.enrichment.enricher import enrich_website
from app.extraction.nppes import LeadQuery, RawLead, build_source
from app.extraction.normalize import merge_provider_names
from app.extraction.website import resolve_website
from app.llm.base import LLMError, LLMProvider
from app.llm.factory import get_provider
from app.models.campaign import CampaignConfig
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.outreach import DemoArtifact, EmailDraft, Personalization, SendRecord, VideoArtifact
from app.models.schemas import DemoConfig
from app.observability.events import log_event, stage_timer
from app.outreach import suppression
from app.outreach.approval import pending as pending_reviews  # re-exported for the CLI
from app.outreach.email_generator import render_email
from app.outreach.sender import EmailProvider, get_email_provider
from app.personalization.generator import generate_personalization
from app.personalization.lead_packet import build_packet
from app.scoring.lead_score import score_lead
from app.util.http import Fetcher
from app.video.recorder import record_demo
from app.video.script import build_script
from app.orchestration.state import STAGE_INPUT_STATES, LeadStatus

__all__ = [
    "StageSummary",
    "discover",
    "enrich",
    "score",
    "personalize",
    "build_demos",
    "render_videos",
    "build_emails",
    "send_approved",
    "run_campaign",
    "pending_reviews",
]


@dataclass
class StageSummary:
    stage: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def __str__(self) -> str:  # pragma: no cover - CLI formatting
        return (
            f"{self.stage}: {self.succeeded} ok, {self.failed} failed, "
            f"{self.skipped} skipped (of {self.processed})"
        )


def _eligible(lead: Lead, stage: str, force: bool) -> bool:
    if force:
        return True
    return lead.lead_status in STAGE_INPUT_STATES[stage]


def _leads_for(session: Session, campaign_id: str | None, statuses: Iterable[LeadStatus]) -> list[Lead]:
    query = select(Lead).where(Lead.status.in_([s.value for s in statuses]))
    if campaign_id:
        query = query.where(Lead.campaign_id == campaign_id)
    return list(session.execute(query.order_by(Lead.score.desc(), Lead.created_at)).scalars())


def _upsert_evidence(session: Session, lead: Lead, items: Iterable[Any]) -> int:
    existing = {
        (row.attribute, row.value)
        for row in session.execute(
            select(Evidence).where(Evidence.lead_id == lead.id)
        ).scalars()
    }
    added = 0
    for item in items:
        key = (item.attribute, item.value)
        if key in existing:
            continue
        session.add(
            Evidence(
                lead_id=lead.id,
                attribute=item.attribute,
                value=item.value,
                source_url=item.source_url,
                page_title=item.page_title,
            )
        )
        existing.add(key)
        added += 1
    return added


# ---------------------------------------------------------------------------
# Stage 1 - discovery
# ---------------------------------------------------------------------------
def raw_to_lead(raw: RawLead, campaign: CampaignConfig) -> Lead:
    website = resolve_website(raw.organization_name, campaign, raw.website, raw.emails)
    return Lead(
        campaign_id=campaign.id,
        organization_name=raw.organization_name,
        dedupe_key=raw.key,
        website=website,
        provider_names=list(raw.provider_names),
        specialty=list(raw.specialty),
        city=raw.city,
        state=raw.state,
        postal_code=raw.postal_code,
        address=raw.address,
        phone=raw.phone,
        emails=list(raw.emails),
        npi_numbers=list(raw.npi_numbers),
        source=raw.source,
        status=LeadStatus.DISCOVERED.value,
    )


def discover(session: Session, campaign: CampaignConfig) -> StageSummary:
    summary = StageSummary(stage="DISCOVERY")
    with stage_timer(session, stage="DISCOVERY", campaign_id=campaign.id) as event:
        source = build_source(campaign)
        raw_leads = source.search(LeadQuery.from_campaign(campaign))
        summary.processed = len(raw_leads)
        for raw in raw_leads:
            existing = session.execute(
                select(Lead).where(Lead.dedupe_key == raw.key)
            ).scalar_one_or_none()
            if existing is not None:
                summary.skipped += 1
                # Backfill a website that only became known later.
                if not existing.website:
                    website = resolve_website(
                        existing.organization_name, campaign, raw.website, existing.emails
                    )
                    if website:
                        existing.website = website
                continue
            session.add(raw_to_lead(raw, campaign))
            summary.succeeded += 1
        event["extra"] = {
            "discovered": summary.succeeded,
            "duplicates": summary.skipped,
            "source": source.name,
        }
    return summary


# ---------------------------------------------------------------------------
# Stage 2 - enrichment
# ---------------------------------------------------------------------------
def enrich(
    session: Session,
    campaign: CampaignConfig,
    *,
    limit: int | None = None,
    force: bool = False,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    summary = StageSummary(stage="ENRICHMENT")
    leads = _leads_for(
        session,
        campaign.id,
        [LeadStatus.DISCOVERED, LeadStatus.ENRICHED, LeadStatus.ENRICHMENT_PARTIAL, LeadStatus.FAILED]
        if force
        else [LeadStatus.DISCOVERED],
    )[: limit or None]

    with Fetcher(settings) as fetcher:
        for lead in leads:
            if not _eligible(lead, "ENRICHMENT", force):
                summary.skipped += 1
                continue
            summary.processed += 1
            if not lead.website:
                lead.set_status(LeadStatus.ENRICHMENT_PARTIAL, detail="no website known")
                summary.skipped += 1
                log_event(
                    session, stage="ENRICHMENT", status="SKIPPED", lead_id=lead.id,
                    detail="no website known",
                )
                continue
            try:
                with stage_timer(session, stage="ENRICHMENT", lead_id=lead.id, campaign_id=campaign.id) as event:
                    outcome = enrich_website(
                        lead.website, lead.organization_name, settings=settings, fetcher=fetcher
                    )
                    if not outcome.ok:
                        # One retry is built into the fetcher's error handling; keep the lead.
                        lead.set_status(
                            LeadStatus.ENRICHMENT_PARTIAL,
                            detail="; ".join(outcome.errors[:2]) or "no pages fetched",
                        )
                        event["status"] = "PARTIAL"
                        event["detail"] = "no pages fetched"
                        summary.failed += 1
                        continue
                    for key, value in outcome.fields.items():
                        if key == "emails":
                            merged = list(dict.fromkeys(list(lead.emails or []) + list(value)))
                            lead.emails = merged
                        elif key == "provider_names":
                            lead.provider_names = merge_provider_names(
                                list(lead.provider_names or []), list(value)
                            )
                        elif value not in (None, [], ""):
                            setattr(lead, key, value)
                    added = _upsert_evidence(session, lead, outcome.evidence)
                    lead.set_status(
                        LeadStatus.ENRICHMENT_PARTIAL if outcome.partial else LeadStatus.ENRICHED,
                        detail="; ".join(outcome.errors[:2]) or None,
                    )
                    event["status"] = "PARTIAL" if outcome.partial else "SUCCESS"
                    event["extra"] = {"pages": outcome.pages_crawled, "evidence_added": added}
                    summary.succeeded += 1
            except Exception as exc:  # noqa: BLE001 - one lead must not stop the batch
                lead.set_status(LeadStatus.FAILED, detail=f"enrichment: {exc}")
                summary.failed += 1
                summary.note(f"{lead.organization_name}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Stage 3 - scoring / qualification
# ---------------------------------------------------------------------------
def score(
    session: Session,
    campaign: CampaignConfig,
    *,
    force: bool = False,
) -> StageSummary:
    summary = StageSummary(stage="SCORING")
    leads = _leads_for(
        session,
        campaign.id,
        [LeadStatus.ENRICHED, LeadStatus.ENRICHMENT_PARTIAL, LeadStatus.SCORED, LeadStatus.BACKLOG]
        if force
        else [LeadStatus.ENRICHED, LeadStatus.ENRICHMENT_PARTIAL],
    )
    for lead in leads:
        if not _eligible(lead, "SCORING", force):
            summary.skipped += 1
            continue
        summary.processed += 1
        result = score_lead(lead, campaign)
        lead.score = result.score
        lead.score_breakdown = result.breakdown
        lead.tier = result.tier
        if lead.lead_status is not LeadStatus.SCORED:
            lead.set_status(LeadStatus.SCORED)
        lead.set_status(LeadStatus.QUALIFIED if result.qualified else LeadStatus.BACKLOG)
        summary.succeeded += 1
        log_event(
            session,
            stage="SCORING",
            status="SUCCESS",
            lead_id=lead.id,
            campaign_id=campaign.id,
            detail=f"{result.score} ({result.tier})",
            score=result.score,
            tier=result.tier,
        )
    return summary


# ---------------------------------------------------------------------------
# Stage 4 - personalization
# ---------------------------------------------------------------------------
def personalize(
    session: Session,
    campaign: CampaignConfig,
    *,
    provider: LLMProvider | None = None,
    limit: int | None = None,
    force: bool = False,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    provider = provider or get_provider(settings=settings)
    summary = StageSummary(stage="PERSONALIZATION")
    leads = _leads_for(
        session,
        campaign.id,
        [LeadStatus.QUALIFIED, LeadStatus.PERSONALIZED] if force else [LeadStatus.QUALIFIED],
    )[: limit or None]

    for lead in leads:
        if not _eligible(lead, "PERSONALIZATION", force):
            summary.skipped += 1
            continue
        summary.processed += 1
        try:
            with stage_timer(session, stage="PERSONALIZATION", lead_id=lead.id, campaign_id=campaign.id) as event:
                evidence = list(
                    session.execute(select(Evidence).where(Evidence.lead_id == lead.id)).scalars()
                )
                packet = build_packet(lead, evidence, campaign, settings)
                output, report = generate_personalization(packet, provider, settings)

                existing = session.execute(
                    select(Personalization).where(Personalization.lead_id == lead.id)
                ).scalar_one_or_none()
                record = existing or Personalization(lead_id=lead.id)
                record.sales_angle = output.sales_angle
                record.pain_point = output.pain_point
                record.relevant_use_cases = list(output.relevant_use_cases)
                record.demo_questions = list(output.demo_questions)
                record.email_subject = output.email_subject
                record.email_body = output.email_body
                record.video_intro = output.video_intro
                record.video_outro = output.video_outro
                record.claims_used = list(output.claims_used)
                record.evidence_ids = [fact.id for fact in packet.facts if fact.id in set(output.claims_used)]
                record.provider = provider.name
                if existing is None:
                    session.add(record)

                lead.set_status(LeadStatus.PERSONALIZED)
                event["extra"] = {"provider": provider.name, "grounded": report.ok}
                summary.succeeded += 1
        except (LLMError, ValueError) as exc:
            lead.set_status(LeadStatus.FAILED, detail=f"personalization: {exc}")
            summary.failed += 1
            summary.note(f"{lead.organization_name}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Stage 5 - demo
# ---------------------------------------------------------------------------
def build_demos(
    session: Session,
    campaign: CampaignConfig,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    summary = StageSummary(stage="DEMO")
    leads = _leads_for(
        session,
        campaign.id,
        [
            LeadStatus.PERSONALIZED,
            LeadStatus.DEMO_READY,
            LeadStatus.VIDEO_READY,
            LeadStatus.VIDEO_FAILED,
            LeadStatus.REVIEW_REQUIRED,
        ]
        if force
        else [LeadStatus.PERSONALIZED],
    )
    for lead in leads:
        if not _eligible(lead, "DEMO", force):
            summary.skipped += 1
            continue
        summary.processed += 1
        try:
            with stage_timer(session, stage="DEMO", lead_id=lead.id, campaign_id=campaign.id) as event:
                personalization = session.execute(
                    select(Personalization).where(Personalization.lead_id == lead.id)
                ).scalar_one_or_none()
                if personalization is None:
                    raise ValueError("no personalization for lead")
                config = build_demo_config(lead, personalization, settings)
                config_path = write_demo_config(config, settings)
                html_path = write_demo_html(config, settings)

                existing = session.execute(
                    select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
                ).scalar_one_or_none()
                artifact = existing or DemoArtifact(lead_id=lead.id)
                artifact.config = config.model_dump()
                artifact.config_path = str(config_path)
                artifact.html_path = str(html_path)
                artifact.url = demo_url(lead.id, settings)
                if existing is None:
                    session.add(artifact)

                # Rebuilding a demo for a lead that already moved on (queued for
                # review, video recorded) refreshes the artifact, not the status.
                if lead.lead_status in {LeadStatus.PERSONALIZED, LeadStatus.DEMO_READY}:
                    lead.set_status(LeadStatus.DEMO_READY)
                event["extra"] = {"html": str(html_path)}
                summary.succeeded += 1
        except Exception as exc:  # noqa: BLE001
            lead.set_status(LeadStatus.FAILED, detail=f"demo: {exc}")
            summary.failed += 1
            summary.note(f"{lead.organization_name}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Stage 6 - video
# ---------------------------------------------------------------------------
def render_videos(
    session: Session,
    campaign: CampaignConfig,
    *,
    limit: int | None = None,
    force: bool = False,
    speed: float = 1.0,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    summary = StageSummary(stage="VIDEO")
    leads = _leads_for(
        session,
        campaign.id,
        [
            LeadStatus.DEMO_READY,
            LeadStatus.VIDEO_READY,
            LeadStatus.VIDEO_FAILED,
            LeadStatus.REVIEW_REQUIRED,
        ]
        if force
        else [LeadStatus.DEMO_READY, LeadStatus.VIDEO_FAILED],
    )[: limit or None]

    for lead in leads:
        if not _eligible(lead, "VIDEO", force):
            summary.skipped += 1
            continue
        summary.processed += 1
        artifact_row = session.execute(
            select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
        ).scalar_one_or_none()
        if artifact_row is None:
            summary.failed += 1
            summary.note(f"{lead.organization_name}: no demo to record")
            continue
        config = DemoConfig.model_validate(artifact_row.config)
        script = build_script(config)
        existing = session.execute(
            select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
        ).scalar_one_or_none()
        video = existing or VideoArtifact(lead_id=lead.id)
        video.script = [segment.model_dump() for segment in script.segments]
        if existing is None:
            session.add(video)
        try:
            with stage_timer(session, stage="VIDEO", lead_id=lead.id, campaign_id=campaign.id) as event:
                result = record_demo(
                    script,
                    page_url=demo_file_url(lead.id, settings),
                    settings=settings,
                    speed=speed,
                )
                video.path = str(result.path)
                video.container = result.container
                video.duration_seconds = result.duration_seconds
                video.error = "; ".join(result.warnings) or None
                # Re-recording a lead that is already queued for review must not
                # pull it back out of the queue.
                if lead.lead_status is not LeadStatus.REVIEW_REQUIRED:
                    lead.set_status(LeadStatus.VIDEO_READY)
                event["extra"] = {
                    "container": result.container,
                    "seconds": result.duration_seconds,
                    "warnings": result.warnings,
                }
                summary.succeeded += 1
        except Exception as exc:  # noqa: BLE001 - the demo survives a failed recording
            video.error = str(exc)
            if lead.lead_status is not LeadStatus.REVIEW_REQUIRED:
                lead.set_status(LeadStatus.VIDEO_FAILED, detail=str(exc)[:300])
            summary.failed += 1
            summary.note(f"{lead.organization_name}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Stage 7 - email draft -> review queue
# ---------------------------------------------------------------------------
def build_emails(
    session: Session,
    campaign: CampaignConfig,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    summary = StageSummary(stage="EMAIL")
    leads = _leads_for(
        session,
        campaign.id,
        [LeadStatus.DEMO_READY, LeadStatus.VIDEO_READY, LeadStatus.VIDEO_FAILED, LeadStatus.REVIEW_REQUIRED]
        if force
        else [LeadStatus.DEMO_READY, LeadStatus.VIDEO_READY, LeadStatus.VIDEO_FAILED],
    )
    for lead in leads:
        if not _eligible(lead, "EMAIL", force):
            summary.skipped += 1
            continue
        summary.processed += 1
        try:
            with stage_timer(session, stage="EMAIL", lead_id=lead.id, campaign_id=campaign.id) as event:
                personalization = session.execute(
                    select(Personalization).where(Personalization.lead_id == lead.id)
                ).scalar_one_or_none()
                if personalization is None:
                    raise ValueError("no personalization for lead")
                if lead.primary_email and suppression.is_suppressed(session, lead.primary_email):
                    lead.set_status(LeadStatus.SUPPRESSED, detail="recipient on suppression list")
                    event["status"] = "SUPPRESSED"
                    summary.skipped += 1
                    continue

                video = session.execute(
                    select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
                ).scalar_one_or_none()
                video_note = None
                if video is not None and video.path:
                    video_note = f"60-second walkthrough attached: {Path(video.path).name}"
                demo = session.execute(
                    select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
                ).scalar_one_or_none()

                rendered = render_email(
                    lead,
                    personalization,
                    demo_url=demo.url if demo else None,
                    video_note=video_note,
                    settings=settings,
                )
                existing = session.execute(
                    select(EmailDraft).where(EmailDraft.lead_id == lead.id)
                ).scalar_one_or_none()
                draft = existing or EmailDraft(lead_id=lead.id, recipient=rendered.recipient)
                if existing is not None and existing.edited_by_human and not force:
                    summary.skipped += 1
                    event["status"] = "SKIPPED"
                    event["detail"] = "human-edited draft preserved"
                    continue
                draft.recipient = rendered.recipient
                draft.subject = rendered.subject
                draft.body_text = rendered.body_text
                draft.body_html = rendered.body_html
                draft.word_count = rendered.word_count
                if existing is None:
                    session.add(draft)

                lead.set_status(LeadStatus.REVIEW_REQUIRED)
                event["extra"] = {"recipient": rendered.recipient, "words": rendered.word_count}
                summary.succeeded += 1
        except Exception as exc:  # noqa: BLE001 - includes EmailValidationError
            lead.set_status(LeadStatus.FAILED, detail=f"email: {exc}")
            summary.failed += 1
            summary.note(f"{lead.organization_name}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Stage 8 - send (approved only)
# ---------------------------------------------------------------------------
def send_approved(
    session: Session,
    campaign: CampaignConfig | None = None,
    *,
    limit: int | None = None,
    provider: EmailProvider | None = None,
    attach_video: bool = True,
    settings: Settings | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    provider = provider or get_email_provider(settings=settings)
    summary = StageSummary(stage="SEND")
    leads = _leads_for(session, campaign.id if campaign else None, [LeadStatus.APPROVED])[: limit or None]

    for lead in leads:
        summary.processed += 1
        draft = session.execute(
            select(EmailDraft).where(EmailDraft.lead_id == lead.id)
        ).scalar_one_or_none()
        if draft is None or draft.approved_at is None:
            summary.skipped += 1
            summary.note(f"{lead.organization_name}: no approved draft")
            continue
        # The suppression check is the last thing before delivery, always.
        if suppression.is_suppressed(session, draft.recipient):
            lead.set_status(LeadStatus.SUPPRESSED, detail="recipient on suppression list")
            log_event(session, stage="SEND", status="SUPPRESSED", lead_id=lead.id)
            summary.skipped += 1
            continue
        already_sent = session.execute(
            select(SendRecord).where(SendRecord.lead_id == lead.id, SendRecord.status == "SENT")
        ).scalar_one_or_none()
        if already_sent is not None:
            summary.skipped += 1
            summary.note(f"{lead.organization_name}: already sent, not resending")
            continue

        attachments: list[Path] = []
        if attach_video:
            video = session.execute(
                select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
            ).scalar_one_or_none()
            if video is not None and video.path and Path(video.path).exists():
                attachments.append(Path(video.path))

        with stage_timer(session, stage="SEND", lead_id=lead.id) as event:
            result = provider.send(
                draft.recipient, draft.subject, draft.body_text, draft.body_html, attachments
            )
            session.add(
                SendRecord(
                    lead_id=lead.id,
                    campaign_id=lead.campaign_id,
                    recipient=draft.recipient,
                    subject=draft.subject,
                    provider=result.provider,
                    message_id=result.message_id,
                    status=result.status,
                    error=result.error,
                )
            )
            if result.ok:
                lead.set_status(LeadStatus.SENT)
                summary.succeeded += 1
            else:
                lead.set_status(LeadStatus.SEND_FAILED, detail=result.error)
                event["status"] = "FAILURE"
                event["detail"] = result.error
                summary.failed += 1
                summary.note(f"{lead.organization_name}: {result.error}")
    return summary


# ---------------------------------------------------------------------------
# End to end (stops at the review queue - never sends)
# ---------------------------------------------------------------------------
def run_campaign(
    session: Session,
    campaign: CampaignConfig,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    demo_limit: int | None = 5,
    skip_video: bool = False,
    video_speed: float = 1.0,
    force: bool = False,
) -> list[StageSummary]:
    """discover -> enrich -> score -> personalize -> demo -> video -> email.

    Returns each stage summary.  Sending is deliberately NOT part of this: a
    human approves first.
    """
    settings = settings or get_settings()
    summaries = [
        discover(session, campaign),
        enrich(session, campaign, force=force, settings=settings),
        score(session, campaign, force=force),
        personalize(
            session, campaign, provider=provider, limit=demo_limit, force=force, settings=settings
        ),
        build_demos(session, campaign, force=force, settings=settings),
    ]
    if skip_video:
        summaries.append(StageSummary(stage="VIDEO", notes=["skipped (--skip-video)"]))
    else:
        summaries.append(
            render_videos(
                session, campaign, limit=demo_limit, force=force, speed=video_speed, settings=settings
            )
        )
    summaries.append(build_emails(session, campaign, force=force, settings=settings))
    return summaries
