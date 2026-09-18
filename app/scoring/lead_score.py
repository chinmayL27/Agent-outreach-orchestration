"""Deterministic lead scoring - no LLM tokens spent on qualification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.extraction.email import is_valid_email
from app.models.campaign import CampaignConfig, ScoringWeights


@dataclass
class ScoreResult:
    score: int
    tier: str  # premium | basic | backlog
    breakdown: list[dict[str, Any]]

    @property
    def qualified(self) -> bool:
        return self.tier in {"premium", "basic"}


def _matches_specialty(lead_specialties: list[str], targets: list[str]) -> bool:
    if not targets:
        return bool(lead_specialties)
    haystack = " ".join(lead_specialties).lower()
    return any(target.lower() in haystack for target in targets)


def score_lead(lead: Any, campaign: CampaignConfig) -> ScoreResult:
    """Score a lead from evidence-backed facts only.

    `lead` is anything with the Lead attributes (the ORM entity in production,
    a simple stub in tests).
    """
    weights: ScoringWeights = campaign.scoring
    breakdown: list[dict[str, Any]] = []

    def award(signal: str, points: int, hit: bool, detail: str = "") -> None:
        breakdown.append(
            {"signal": signal, "points": points if hit else 0, "hit": hit, "detail": detail}
        )

    specialties = list(getattr(lead, "specialty", []) or [])
    award(
        "target_specialty",
        weights.target_specialty,
        _matches_specialty(specialties, campaign.specialties),
        ", ".join(specialties[:3]),
    )

    provider_count = getattr(lead, "provider_count", None) or len(
        getattr(lead, "provider_names", []) or []
    )
    in_range = (
        provider_count is not None
        and campaign.practice_size.min_providers <= provider_count <= campaign.practice_size.max_providers
        and provider_count > 0
    )
    award("provider_count_in_range", weights.provider_count_in_range, in_range, f"{provider_count} providers")

    locations = list(getattr(lead, "locations", []) or [])
    award("multiple_locations", weights.multiple_locations, len(locations) > 1, f"{len(locations)} locations")

    award(
        "has_online_booking",
        weights.has_online_booking,
        bool(getattr(lead, "has_online_booking", False)),
        getattr(lead, "booking_system", "") or "",
    )

    award(
        "no_chatbot",
        weights.no_chatbot,
        getattr(lead, "has_chatbot", None) is False,
        "no chat widget detected",
    )

    faq_questions = list(getattr(lead, "faq_questions", []) or [])
    award(
        "extensive_faq",
        weights.extensive_faq,
        len(faq_questions) >= weights.faq_threshold,
        f"{len(faq_questions)} FAQ questions",
    )

    emails = [e for e in (getattr(lead, "emails", []) or []) if is_valid_email(e)]
    award("public_email", weights.public_email, bool(emails), emails[0] if emails else "")

    score = sum(item["points"] for item in breakdown)
    score = max(0, min(100, score))
    if score >= weights.premium_threshold:
        tier = "premium"
    elif score >= weights.basic_threshold:
        tier = "basic"
    else:
        tier = "backlog"
    return ScoreResult(score=score, tier=tier, breakdown=breakdown)
