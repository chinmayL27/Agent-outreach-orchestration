"""Human approval gate.  Nothing is sent unless a person approved it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.outreach import DemoArtifact, EmailDraft, VideoArtifact
from app.observability.events import log_event
from app.orchestration.state import LeadStatus


@dataclass
class ReviewItem:
    lead: Lead
    draft: EmailDraft
    demo: DemoArtifact | None
    video: VideoArtifact | None
    evidence: list[Evidence]


def pending(session: Session, limit: int = 50) -> list[ReviewItem]:
    leads = list(
        session.execute(
            select(Lead)
            .where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
            .order_by(Lead.score.desc())
            .limit(limit)
        ).scalars()
    )
    items: list[ReviewItem] = []
    for lead in leads:
        draft = session.execute(
            select(EmailDraft).where(EmailDraft.lead_id == lead.id)
        ).scalar_one_or_none()
        if draft is None:
            continue
        items.append(
            ReviewItem(
                lead=lead,
                draft=draft,
                demo=session.execute(
                    select(DemoArtifact).where(DemoArtifact.lead_id == lead.id)
                ).scalar_one_or_none(),
                video=session.execute(
                    select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
                ).scalar_one_or_none(),
                evidence=list(
                    session.execute(
                        select(Evidence).where(Evidence.lead_id == lead.id).limit(12)
                    ).scalars()
                ),
            )
        )
    return items


def approve(session: Session, lead: Lead, approved_by: str = "human") -> EmailDraft:
    draft = session.execute(
        select(EmailDraft).where(EmailDraft.lead_id == lead.id)
    ).scalar_one_or_none()
    if draft is None:
        raise ValueError(f"no email draft for {lead.organization_name}")
    draft.approved_at = datetime.now(timezone.utc)
    draft.approved_by = approved_by
    lead.set_status(LeadStatus.APPROVED)
    log_event(session, stage="APPROVAL", status="APPROVED", lead_id=lead.id, detail=approved_by)
    return draft


def reject(session: Session, lead: Lead, reason: str = "") -> None:
    draft = session.execute(
        select(EmailDraft).where(EmailDraft.lead_id == lead.id)
    ).scalar_one_or_none()
    if draft is not None:
        draft.rejected_reason = reason
    lead.set_status(LeadStatus.REJECTED, detail=reason or None)
    log_event(session, stage="APPROVAL", status="REJECTED", lead_id=lead.id, detail=reason)


def edit(session: Session, lead: Lead, *, subject: str | None = None, body: str | None = None) -> EmailDraft:
    draft = session.execute(
        select(EmailDraft).where(EmailDraft.lead_id == lead.id)
    ).scalar_one_or_none()
    if draft is None:
        raise ValueError(f"no email draft for {lead.organization_name}")
    if subject:
        draft.subject = subject.strip()
    if body:
        draft.body_text = body
    draft.edited_by_human = True
    log_event(session, stage="APPROVAL", status="EDITED", lead_id=lead.id)
    return draft
