"""Campaign state machine.

Every lead moves through an explicit, auditable sequence of states.  Stages are
idempotent: re-running a stage for a lead that already reached the resulting
state is a no-op unless `force` is used.
"""

from __future__ import annotations

from enum import Enum


class LeadStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    ENRICHED = "ENRICHED"
    ENRICHMENT_PARTIAL = "ENRICHMENT_PARTIAL"
    SCORED = "SCORED"
    QUALIFIED = "QUALIFIED"
    BACKLOG = "BACKLOG"
    PERSONALIZED = "PERSONALIZED"
    DEMO_READY = "DEMO_READY"
    VIDEO_READY = "VIDEO_READY"
    VIDEO_FAILED = "VIDEO_FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    SEND_FAILED = "SEND_FAILED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"
    UNSUBSCRIBED = "UNSUBSCRIBED"


S = LeadStatus

#: Terminal-ish states that any lead may fall into from anywhere.
ESCAPE_HATCHES: frozenset[LeadStatus] = frozenset(
    {S.FAILED, S.REJECTED, S.SUPPRESSED, S.UNSUBSCRIBED}
)

#: The happy path plus the documented alternates.
ALLOWED_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    S.DISCOVERED: frozenset({S.ENRICHED, S.ENRICHMENT_PARTIAL}),
    S.ENRICHED: frozenset({S.ENRICHED, S.ENRICHMENT_PARTIAL, S.SCORED}),
    S.ENRICHMENT_PARTIAL: frozenset({S.ENRICHED, S.ENRICHMENT_PARTIAL, S.SCORED}),
    S.SCORED: frozenset({S.SCORED, S.QUALIFIED, S.BACKLOG, S.ENRICHED}),
    S.QUALIFIED: frozenset({S.PERSONALIZED, S.SCORED}),
    S.BACKLOG: frozenset({S.SCORED, S.QUALIFIED}),
    S.PERSONALIZED: frozenset({S.PERSONALIZED, S.DEMO_READY}),
    S.DEMO_READY: frozenset({S.DEMO_READY, S.VIDEO_READY, S.VIDEO_FAILED, S.REVIEW_REQUIRED}),
    S.VIDEO_READY: frozenset({S.VIDEO_READY, S.REVIEW_REQUIRED}),
    S.VIDEO_FAILED: frozenset({S.VIDEO_READY, S.VIDEO_FAILED, S.REVIEW_REQUIRED}),
    S.REVIEW_REQUIRED: frozenset({S.APPROVED, S.REJECTED, S.REVIEW_REQUIRED}),
    S.APPROVED: frozenset({S.SENT, S.SEND_FAILED, S.REVIEW_REQUIRED}),
    S.SEND_FAILED: frozenset({S.REVIEW_REQUIRED, S.APPROVED}),
    S.SENT: frozenset({S.UNSUBSCRIBED}),
    S.REJECTED: frozenset({S.REVIEW_REQUIRED}),
    S.FAILED: frozenset({S.ENRICHED, S.SCORED, S.QUALIFIED, S.PERSONALIZED, S.DEMO_READY}),
    S.SUPPRESSED: frozenset(),
    S.UNSUBSCRIBED: frozenset(),
}

#: Stage name -> the status a lead must already have to be eligible.
STAGE_INPUT_STATES: dict[str, frozenset[LeadStatus]] = {
    "ENRICHMENT": frozenset({S.DISCOVERED, S.ENRICHED, S.ENRICHMENT_PARTIAL, S.FAILED}),
    "SCORING": frozenset({S.ENRICHED, S.ENRICHMENT_PARTIAL, S.SCORED, S.QUALIFIED, S.BACKLOG}),
    "PERSONALIZATION": frozenset({S.QUALIFIED, S.PERSONALIZED, S.FAILED}),
    "DEMO": frozenset(
        {S.PERSONALIZED, S.DEMO_READY, S.VIDEO_READY, S.VIDEO_FAILED, S.REVIEW_REQUIRED, S.FAILED}
    ),
    "VIDEO": frozenset({S.DEMO_READY, S.VIDEO_READY, S.VIDEO_FAILED, S.REVIEW_REQUIRED}),
    "EMAIL": frozenset({S.DEMO_READY, S.VIDEO_READY, S.VIDEO_FAILED, S.REVIEW_REQUIRED}),
    "SEND": frozenset({S.APPROVED}),
}


class InvalidTransition(RuntimeError):
    """Raised when a stage attempts a transition the state machine forbids."""


def can_transition(current: LeadStatus, new: LeadStatus) -> bool:
    if new in ESCAPE_HATCHES:
        return True
    return new in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: LeadStatus, new: LeadStatus) -> None:
    if not can_transition(current, new):
        raise InvalidTransition(f"{current.value} -> {new.value} is not an allowed transition")
