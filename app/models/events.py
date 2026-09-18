"""Structured per-stage execution events (the observability backbone)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonDict, new_id, utcnow


class StageEvent(Base):
    __tablename__ = "stage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    campaign_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    #: Hash of the stage's inputs, so unchanged work can be recognised.
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    extra: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
