"""Personalization, demo/video artifacts, email drafts, sends, suppression."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonDict, JsonList, new_id, utcnow


class Personalization(Base):
    """One LLM call per qualified lead produces exactly one of these."""

    __tablename__ = "personalizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True
    )

    sales_angle: Mapped[str] = mapped_column(Text)
    pain_point: Mapped[str | None] = mapped_column(Text, default=None)
    relevant_use_cases: Mapped[list[str]] = mapped_column(JsonList, default=list)
    demo_questions: Mapped[list[str]] = mapped_column(JsonList, default=list)

    email_subject: Mapped[str] = mapped_column(String(255), default="")
    email_body: Mapped[str] = mapped_column(Text, default="")

    video_intro: Mapped[str] = mapped_column(Text, default="")
    video_outro: Mapped[str] = mapped_column(Text, default="")

    evidence_ids: Mapped[list[str]] = mapped_column(JsonList, default=list)
    claims_used: Mapped[list[str]] = mapped_column(JsonList, default=list)

    provider: Mapped[str] = mapped_column(String(32), default="template")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DemoArtifact(Base):
    __tablename__ = "demos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    html_path: Mapped[str | None] = mapped_column(String(512), default=None)
    config_path: Mapped[str | None] = mapped_column(String(512), default=None)
    #: Local review URL; `published_url` is what a recipient can actually open.
    url: Mapped[str | None] = mapped_column(String(512), default=None)
    published_url: Mapped[str | None] = mapped_column(String(512), default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VideoArtifact(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True
    )
    path: Mapped[str | None] = mapped_column(String(512), default=None)
    published_url: Mapped[str | None] = mapped_column(String(512), default=None)
    duration_seconds: Mapped[float | None] = mapped_column(default=None)
    duration_in_range: Mapped[bool | None] = mapped_column(default=None)
    playable: Mapped[bool | None] = mapped_column(default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)
    container: Mapped[str] = mapped_column(String(16), default="mp4")
    script: Mapped[list[dict[str, Any]]] = mapped_column(JsonList, default=list)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True
    )
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    demo_url: Mapped[str | None] = mapped_column(String(512), default=None)
    #: False when the package still points at localhost/file:// artifacts.
    links_public: Mapped[bool] = mapped_column(default=False)

    #: Bumped whenever recipient, copy, demo or video changes.
    package_version: Mapped[int] = mapped_column(Integer, default=1)
    #: The version a human actually approved; a later bump invalidates it.
    approved_package_version: Mapped[int | None] = mapped_column(Integer, default=None)

    edited_by_human: Mapped[bool] = mapped_column(default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    approved_by: Mapped[str | None] = mapped_column(String(128), default=None)
    rejected_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def approval_is_current(self) -> bool:
        """An approval only counts for the exact package version approved."""
        return (
            self.approved_at is not None
            and self.approved_package_version == self.package_version
        )

    def invalidate_approval(self) -> bool:
        """Bump the version and drop any approval. Returns True if one was lost."""
        had_approval = self.approved_at is not None
        self.package_version += 1
        self.approved_at = None
        self.approved_by = None
        self.approved_package_version = None
        return had_approval


class SendRecord(Base):
    __tablename__ = "sends"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(64), default=None)
    recipient: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), default="console")
    message_id: Mapped[str | None] = mapped_column(String(255), default=None)
    package_version: Mapped[int] = mapped_column(Integer, default=1)
    #: SENT | SEND_FAILED | SEND_UNCERTAIN. Provider acceptance and delivery
    #: are different events; an uncertain outcome is never auto-retried.
    status: Mapped[str] = mapped_column(String(32), default="SENT")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Suppression(Base):
    __tablename__ = "suppressions"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32), default="MANUAL_BLOCK")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMCacheEntry(Base):
    """Validated model output, keyed by packet + product + prompt version.

    A downstream stage failing must not force a paid regeneration of work that
    already succeeded (TDD s12/s20).
    """

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    provider: Mapped[str] = mapped_column(String(32), default="")
    schema_name: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CampaignRun(Base):
    """One execution of a campaign: configuration snapshot + timing + status."""

    __tablename__ = "campaign_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    summary: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)


SUPPRESSION_REASONS = ("UNSUBSCRIBED", "BOUNCED", "MANUAL_BLOCK", "INVALID")
SEND_STATUSES = ("SENT", "SEND_FAILED", "SEND_UNCERTAIN")
