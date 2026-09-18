"""Structured stage events: one row + one JSON log line per stage execution."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.events import StageEvent

logger = logging.getLogger("outreach")


def log_event(
    session: Session,
    *,
    stage: str,
    status: str,
    lead_id: str | None = None,
    campaign_id: str | None = None,
    duration_ms: int = 0,
    detail: str | None = None,
    **extra: Any,
) -> StageEvent:
    event = StageEvent(
        lead_id=lead_id,
        campaign_id=campaign_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        detail=detail,
        extra=extra or {},
    )
    session.add(event)
    logger.info(
        json.dumps(
            {
                "lead_id": lead_id,
                "stage": stage,
                "status": status,
                "duration_ms": duration_ms,
                "detail": detail,
                **extra,
            }
        )
    )
    return event


@contextmanager
def stage_timer(
    session: Session,
    *,
    stage: str,
    lead_id: str | None = None,
    campaign_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Time a stage and always emit an event, SUCCESS or FAILURE."""
    started = time.perf_counter()
    result: dict[str, Any] = {"status": "SUCCESS", "detail": None, "extra": {}}
    try:
        yield result
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        elapsed = int((time.perf_counter() - started) * 1000)
        log_event(
            session,
            stage=stage,
            status="FAILURE",
            lead_id=lead_id,
            campaign_id=campaign_id,
            duration_ms=elapsed,
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    else:
        elapsed = int((time.perf_counter() - started) * 1000)
        log_event(
            session,
            stage=stage,
            status=result["status"],
            lead_id=lead_id,
            campaign_id=campaign_id,
            duration_ms=elapsed,
            detail=result["detail"],
            **result["extra"],
        )


def stage_counts(session: Session) -> list[tuple[str, str, int]]:
    rows = session.execute(
        select(StageEvent.stage, StageEvent.status, func.count())
        .group_by(StageEvent.stage, StageEvent.status)
        .order_by(StageEvent.stage)
    ).all()
    return [(stage, status, count) for stage, status, count in rows]
