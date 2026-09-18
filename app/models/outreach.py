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
    url: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VideoArtifact(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, unique=True
    )
    path: Mapped[str | None] = mapped_column(String(512), default=None)
    duration_seconds: Mapped[float | None] = mapped_column(default=None)
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
    edited_by_human: Mapped[bool] = mapped_column(default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    approved_by: Mapped[str | None] = mapped_column(String(128), default=None)
    rejected_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SendRecord(Base):
    __tablename__ = "sends"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(64), default=None)
    recipient: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), default="console")
    message_id: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[str] = mapped_column(String(32), default="SENT")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Suppression(Base):
    __tablename__ = "suppressions"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32), default="MANUAL_BLOCK")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


SUPPRESSION_REASONS = ("UNSUBSCRIBED", "BOUNCED", "MANUAL_BLOCK", "INVALID")
