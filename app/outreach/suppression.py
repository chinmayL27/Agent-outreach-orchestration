"""Suppression list - checked before every single send, no exceptions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outreach import SUPPRESSION_REASONS, Suppression


class SuppressedRecipient(RuntimeError):
    """Raised when a send is attempted against a suppressed address."""


def normalize(email: str) -> str:
    return (email or "").strip().lower()


def add(session: Session, email: str, reason: str = "MANUAL_BLOCK", note: str | None = None) -> Suppression:
    if reason not in SUPPRESSION_REASONS:
        raise ValueError(f"reason must be one of {SUPPRESSION_REASONS}")
    address = normalize(email)
    existing = session.get(Suppression, address)
    if existing:
        existing.reason = reason
        existing.note = note or existing.note
        return existing
    record = Suppression(email=address, reason=reason, note=note)
    session.add(record)
    return record


def is_suppressed(session: Session, email: str) -> bool:
    return session.get(Suppression, normalize(email)) is not None


def assert_not_suppressed(session: Session, email: str) -> None:
    if is_suppressed(session, email):
        raise SuppressedRecipient(f"{email} is on the suppression list")


def list_all(session: Session) -> list[Suppression]:
    return list(session.execute(select(Suppression).order_by(Suppression.created_at)).scalars())
