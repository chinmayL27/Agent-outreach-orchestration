"""The Lead entity - one prospective clinic / practice."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JsonList, new_id, utcnow
from app.orchestration.state import LeadStatus, assert_transition


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)

    organization_name: Mapped[str] = mapped_column(String(255))
    #: Normalized name + city/state; used to keep discovery idempotent.
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    website: Mapped[str | None] = mapped_column(String(512), default=None)
    provider_names: Mapped[list[str]] = mapped_column(JsonList, default=list)
    specialty: Mapped[list[str]] = mapped_column(JsonList, default=list)

    city: Mapped[str | None] = mapped_column(String(128), default=None)
    state: Mapped[str | None] = mapped_column(String(8), default=None)
    postal_code: Mapped[str | None] = mapped_column(String(16), default=None)
    address: Mapped[str | None] = mapped_column(String(255), default=None)

    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    emails: Mapped[list[str]] = mapped_column(JsonList, default=list)
    npi_numbers: Mapped[list[str]] = mapped_column(JsonList, default=list)

    services: Mapped[list[str]] = mapped_column(JsonList, default=list)
    locations: Mapped[list[str]] = mapped_column(JsonList, default=list)
    faq_questions: Mapped[list[str]] = mapped_column(JsonList, default=list)
    appointment_url: Mapped[str | None] = mapped_column(String(512), default=None)

    provider_count: Mapped[int | None] = mapped_column(Integer, default=None)
    has_online_booking: Mapped[bool | None] = mapped_column(default=None)
    has_chatbot: Mapped[bool | None] = mapped_column(default=None)
    booking_system: Mapped[str | None] = mapped_column(String(128), default=None)
    company_summary: Mapped[str | None] = mapped_column(Text, default=None)

    score: Mapped[int] = mapped_column(Integer, default=0)
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JsonList, default=list)
    tier: Mapped[str | None] = mapped_column(String(32), default=None)

    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.DISCOVERED.value, index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(String(64), default="unknown")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    evidence = relationship(
        "Evidence", back_populates="lead", cascade="all, delete-orphan", lazy="selectin"
    )

    # -- helpers ------------------------------------------------------------
    @property
    def lead_status(self) -> LeadStatus:
        return LeadStatus(self.status)

    def set_status(self, new: LeadStatus, detail: str | None = None) -> None:
        """Move to `new`, refusing transitions the state machine forbids."""
        assert_transition(self.lead_status, new)
        self.status = new.value
        self.status_detail = detail

    @property
    def primary_email(self) -> str | None:
        return self.emails[0] if self.emails else None

    @property
    def location_label(self) -> str:
        return ", ".join(part for part in (self.city, self.state) if part)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Lead {self.organization_name!r} score={self.score} status={self.status}>"
