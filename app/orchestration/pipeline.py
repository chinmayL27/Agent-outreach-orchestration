"""The orchestrator.

A plain pipeline of typed workers: each stage reads structured state from the
database, does one job, writes structured state back, and records an event.
Stages are idempotent and isolate failures per lead - one bad website never
stops the batch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.demo.config_generator import build_demo_config, write_demo_config
from app.demo.renderer import demo_file_url, demo_url, write_demo_html
from app.enrichment.enricher import enrich_website
from app.extraction.nppes import LeadQuery, RawLead, build_source
from app.extraction.normalize import (
    domain_of,
    domain_of_email,
    merge_provider_names,
    name_from_domain,
)
from app.extraction.website import resolve_website
from app.llm.base import LLMError, LLMProvider
from app.llm.prompts import PROMPT_VERSION
from app.llm.factory import get_provider
from app.models.campaign import CampaignConfig
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.outreach import (
    CampaignRun,
    LLMCacheEntry,
    DemoArtifact,
    EmailDraft,
    Personalization,
    SendRecord,
    VideoArtifact,
)
from app.models.schemas import DemoConfig, PersonalizationOutput
from app.observability.events import log_event, stage_timer
from app.outreach import suppression
from app.outreach.publisher import ArtifactPublisher, get_publisher, is_publicly_reachable
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


def fingerprint(*parts: Any) -> str:
    """Stable hash of a stage's inputs, recorded on each stage event."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:32]
    except OSError:  # pragma: no cover - defensive
        return None


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
                excerpt=getattr(item, "excerpt", None),
            )
        )
        existing.add(key)
        added += 1
    return added


# ---------------------------------------------------------------------------
# Stage 1 - discovery
# ---------------------------------------------------------------------------
#: Facts a source may assert about a lead, with the Evidence attribute they map
#: to.  Asserted facts are attributed to the source file, never to a crawl.
ASSERTED_FACTS = (("services", "service"), ("provider_names", "provider"), ("emails", "public_email"))


def asserted_evidence(raw: RawLead) -> list[Evidence]:
    """Evidence rows for facts the source stated (e.g. CSV columns).

    Copy supplied by the operator is still copy about the clinic, so it needs a
    provenance trail like anything else - it just cites the file rather than a
    page, so a reviewer can tell an assertion from an observation.
    """
    if not raw.evidence_source:
        return []
    rows: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for field_name, attribute in ASSERTED_FACTS:
        for value in getattr(raw, field_name, []) or []:
            key = (attribute, str(value))
            if not value or key in seen:
                continue
            seen.add(key)
            rows.append(
                Evidence(
                    attribute=attribute,
                    value=str(value),
                    source_url=raw.evidence_source,
                    page_title="operator-supplied import",
                )
            )
    return rows


def _has_provisional_name(lead: Lead) -> bool:
    """True when the lead's name was derived from its domain, not observed.

    Import has no place to record this, so it is re-derived: if the stored name
    is exactly what the domain would produce, nothing better was ever known.
    """
    domain = domain_of(lead.website) or domain_of_email(lead.primary_email)
    derived = name_from_domain(domain)
    return bool(derived) and lead.organization_name.strip().lower() == derived.strip().lower()


def raw_to_lead(raw: RawLead, campaign: CampaignConfig) -> Lead:
    website = resolve_website(raw.organization_name, campaign, raw.website, raw.emails)
    lead = Lead(
        campaign_id=campaign.id,
        organization_name=raw.organization_name,
        dedupe_key=raw.key,
        website=website,
        provider_names=list(raw.provider_names),
        specialty=list(raw.specialty),
        services=list(raw.services),
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
    for item in asserted_evidence(raw):
        lead.evidence.append(item)
    return lead


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
                # Backfill details that only became known later (a re-import of
                # the same list with an email or website filled in).
                if not existing.website:
                    website = resolve_website(
                        existing.organization_name, campaign, raw.website, existing.emails
                    )
                    if website:
                        existing.website = website
                new_emails = [e for e in raw.emails if e not in (existing.emails or [])]
                if new_emails:
                    existing.emails = list(existing.emails or []) + new_emails
                continue
            session.add(raw_to_lead(raw, campaign))
            summary.succeeded += 1
        event["extra"] = {
            "discovered": summary.succeeded,
            "duplicates": summary.skipped,
            "source": source.name,
        }
        report = getattr(source, "report", None)
        if report is not None:
            for note in report.notes():
                summary.note(note)
            event["extra"]["rows_read"] = report.rows
            event["extra"]["rows_skipped"] = report.skipped_total
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
                with stage_timer(
                    session,
                    stage="ENRICHMENT",
                    lead_id=lead.id,
                    campaign_id=campaign.id,
                    input_fingerprint=fingerprint(lead.website, settings.max_pages_per_site),
                ) as event:
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
                        if key == "site_name":
                            # Only a domain-derived placeholder gets replaced;
                            # a real name from the source always wins.
                            if value and _has_provisional_name(lead):
                                lead.organization_name = value
                        elif key == "emails":
                            merged = list(dict.fromkeys(list(lead.emails or []) + list(value)))
                            lead.emails = merged
                        elif key == "services":
                            # Keep services the operator supplied on import.
                            lead.services = list(dict.fromkeys(list(lead.services or []) + list(value)))
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
            evidence = list(
                session.execute(select(Evidence).where(Evidence.lead_id == lead.id)).scalars()
            )
            packet = build_packet(lead, evidence, campaign, settings)
            cache_key = fingerprint(
                packet.model_dump(), campaign.product.model_dump(), PROMPT_VERSION, provider.name
            )
            with stage_timer(
                session,
                stage="PERSONALIZATION",
                lead_id=lead.id,
                campaign_id=campaign.id,
                input_fingerprint=cache_key,
            ) as event:
                cached = session.get(LLMCacheEntry, cache_key)
                if cached is not None and not force:
                    output = PersonalizationOutput.model_validate(cached.payload)
                    report = None
                else:
                    output, report = generate_personalization(packet, provider, settings)
                    session.merge(
                        LLMCacheEntry(
                            key=cache_key,
                            lead_id=lead.id,
                            provider=provider.name,
                            schema_name=PersonalizationOutput.__name__,
                            payload=output.model_dump(),
                        )
                    )

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
                event["extra"] = {
                    "provider": provider.name,
                    "grounded": report.ok if report is not None else True,
                    "cached": report is None,
                }
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
            # Rebuilding an approved package is allowed, but it costs the approval.
            LeadStatus.APPROVED,
        ]
        if force
        else [LeadStatus.PERSONALIZED],
    )
    for lead in leads:
        if not _eligible(lead, "DEMO", force):
            summary.skipped += 1
            continue
        # Basic-tier leads (60-79) skip demo and video and go straight to the
        # review queue with an email only (TDD s7).
        if lead.tier != "premium":
            summary.skipped += 1
            summary.note(f"{lead.organization_name}: basic tier, email only")
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
                new_hash = file_hash(html_path)
                if existing is not None and existing.content_hash != new_hash:
                    artifact.version += 1
                artifact.config = config.model_dump()
                artifact.config_path = str(config_path)
                artifact.html_path = str(html_path)
                artifact.content_hash = new_hash
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
                    config=config,
                )
                video.path = str(result.path)
                video.container = result.container
                video.duration_seconds = result.duration_seconds
                video.content_hash = file_hash(result.path)
                if existing is not None:
                    video.version += 1

                # Inspect the artifact rather than trusting the recorder.
                playable = result.path.exists() and result.path.stat().st_size > 10_000
                video.playable = playable
                expected = script.target_seconds / max(speed, 0.01)
                tolerance = max(5.0, expected * 0.15)
                video.duration_in_range = (
                    result.duration_seconds is not None
                    and abs(result.duration_seconds - expected) <= tolerance
                )
                warnings = list(result.warnings)
                if not playable:
                    raise RuntimeError("recording produced no playable file")
                if not video.duration_in_range:
                    warnings.append(
                        f"duration {result.duration_seconds}s outside the expected "
                        f"{expected:.0f}s +/- {tolerance:.0f}s window"
                    )
                video.error = "; ".join(warnings) or None
                # Re-recording a lead that is already queued for review must not
                # pull it back out of the queue.
                if lead.lead_status is not LeadStatus.REVIEW_REQUIRED:
                    lead.set_status(LeadStatus.VIDEO_READY)
                event["extra"] = {
                    "container": result.container,
                    "seconds": result.duration_seconds,
                    "in_range": video.duration_in_range,
                    "warnings": warnings,
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
    publisher: ArtifactPublisher | None = None,
) -> StageSummary:
    settings = settings or get_settings()
    publisher = publisher or get_publisher(settings)
    summary = StageSummary(stage="EMAIL")
    leads = _leads_for(
        session,
        campaign.id,
        [
            LeadStatus.PERSONALIZED,
            LeadStatus.DEMO_READY,
            LeadStatus.VIDEO_READY,
            LeadStatus.VIDEO_FAILED,
            LeadStatus.REVIEW_REQUIRED,
            # Rebuilding an approved package is allowed, but it costs the approval.
            LeadStatus.APPROVED,
        ]
        if force
        # PERSONALIZED here is the basic-tier branch: no demo, no video.
        else [
            LeadStatus.PERSONALIZED,
            LeadStatus.DEMO_READY,
            LeadStatus.VIDEO_READY,
            LeadStatus.VIDEO_FAILED,
        ],
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
                demo = session.execute(
                    select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
                ).scalar_one_or_none()

                # Publishing turns local artifacts into links a clinic can open.
                published = publisher.publish(
                    lead.id,
                    Path(demo.html_path) if demo and demo.html_path else None,
                    Path(video.path) if video and video.path else None,
                )
                if demo is not None and published.demo_url:
                    demo.published_url = published.demo_url
                if video is not None and published.video_url:
                    video.published_url = published.video_url

                video_note = None
                if published.video_url:
                    video_note = f"60-second walkthrough: {published.video_url}"
                elif video is not None and video.path:
                    video_note = f"60-second walkthrough attached: {Path(video.path).name}"

                rendered = render_email(
                    lead,
                    personalization,
                    demo_url=published.demo_url or (demo.published_url if demo else None),
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
                material_change = existing is not None and (
                    existing.recipient != rendered.recipient
                    or existing.subject != rendered.subject
                    or existing.body_text != rendered.body_text
                )
                draft.recipient = rendered.recipient
                draft.subject = rendered.subject
                draft.body_text = rendered.body_text
                draft.body_html = rendered.body_html
                draft.word_count = rendered.word_count
                draft.demo_url = rendered.demo_url
                draft.links_public = rendered.links_public
                if material_change:
                    # Regenerating the package invalidates a prior approval.
                    draft.invalidate_approval()
                if existing is None:
                    session.add(draft)

                # An untouched package keeps its approval; a changed one goes back
                # to a human.
                if lead.lead_status is not LeadStatus.APPROVED or material_change:
                    lead.set_status(LeadStatus.REVIEW_REQUIRED)
                event["extra"] = {
                    "recipient": rendered.recipient,
                    "words": rendered.word_count,
                    "links_public": rendered.links_public,
                    "package_version": draft.package_version,
                }
                if not rendered.links_public:
                    summary.note(
                        f"{lead.organization_name}: no public demo link "
                        "(set PUBLIC_ARTIFACT_BASE_URL before sending)"
                    )
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
        # The approval must match the exact package version being sent.
        if not draft.approval_is_current:
            lead.set_status(LeadStatus.REVIEW_REQUIRED, detail="package changed after approval")
            log_event(
                session, stage="SEND", status="STALE_APPROVAL", lead_id=lead.id,
                detail=f"approved v{draft.approved_package_version}, current v{draft.package_version}",
            )
            summary.skipped += 1
            summary.note(f"{lead.organization_name}: approval is stale, back to review")
            continue
        # A real provider must not mail localhost or file:// links.
        if getattr(provider, "requires_public_links", False) and not draft.links_public:
            log_event(
                session, stage="SEND", status="BLOCKED", lead_id=lead.id,
                detail="artifact links are not publicly reachable",
            )
            summary.skipped += 1
            summary.note(
                f"{lead.organization_name}: demo/video links are not public "
                "(configure PUBLIC_ARTIFACT_BASE_URL)"
            )
            continue
        # The suppression check is the last thing before delivery, always.
        if suppression.is_suppressed(session, draft.recipient):
            lead.set_status(LeadStatus.SUPPRESSED, detail="recipient on suppression list")
            log_event(session, stage="SEND", status="SUPPRESSED", lead_id=lead.id)
            summary.skipped += 1
            continue
        # An uncertain outcome counts as "may already be delivered": never
        # resend it automatically (TDD s7/s17).
        prior = session.execute(
            select(SendRecord).where(
                SendRecord.lead_id == lead.id,
                SendRecord.recipient == draft.recipient,
                SendRecord.package_version == draft.package_version,
                SendRecord.status.in_(["SENT", "SEND_UNCERTAIN"]),
            )
        ).first()
        if prior is not None:
            summary.skipped += 1
            summary.note(f"{lead.organization_name}: already attempted, not resending")
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
                    package_version=draft.package_version,
                    status=result.status,
                    error=result.error,
                )
            )
            if result.ok:
                lead.set_status(LeadStatus.SENT)
                summary.succeeded += 1
            elif result.uncertain:
                lead.set_status(LeadStatus.SEND_UNCERTAIN, detail=result.error)
                event["status"] = "UNCERTAIN"
                event["detail"] = result.error
                summary.failed += 1
                summary.note(
                    f"{lead.organization_name}: delivery uncertain, needs an operator "
                    f"decision ({result.error})"
                )
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
    run = CampaignRun(
        campaign_id=campaign.id,
        name=campaign.name,
        config=campaign.model_dump(),
        status="RUNNING",
    )
    session.add(run)
    session.flush()

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

    run.finished_at = datetime.now(timezone.utc)
    run.status = "FAILED" if any(s.failed for s in summaries) else "COMPLETED"
    run.summary = {
        item.stage: {
            "processed": item.processed,
            "succeeded": item.succeeded,
            "failed": item.failed,
            "skipped": item.skipped,
        }
        for item in summaries
    }
    return summaries
