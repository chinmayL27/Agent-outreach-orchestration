"""Evidence - the source URL behind every extracted claim.

Nothing may be said about a clinic in generated copy unless an Evidence row
backs it.  This is what keeps the LLM stage honest.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, new_id, utcnow


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("lead_id", "attribute", "value", name="uq_evidence_claim"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)

    attribute: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)

    source_url: Mapped[str] = mapped_column(String(512))
    page_title: Mapped[str | None] = mapped_column(String(255), default=None)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead = relationship("Lead", back_populates="evidence")

    @property
    def fact(self) -> str:
        return f"{self.attribute}: {self.value}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Evidence {self.attribute}={self.value!r} src={self.source_url}>"
