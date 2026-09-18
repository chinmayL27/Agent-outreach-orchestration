"""Personalization stage: one structured LLM call per qualified lead, then
grounding checks before anything is persisted."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider
from app.llm.prompts import build_personalization_prompt
from app.models.schemas import LeadPacket, PersonalizationOutput
from app.util.text import word_count

#: Claims of a kind the packet can never support.
FABRICATION_PATTERNS = ("% increase", "% more", "roi of", "save $", "saves $", "per month in revenue")


@dataclass
class GroundingReport:
    ok: bool
    problems: list[str] = field(default_factory=list)


def check_grounding(
    output: PersonalizationOutput,
    packet: LeadPacket,
    settings: Settings | None = None,
) -> GroundingReport:
    """Reject output that hypes, fabricates metrics, or cites unknown evidence."""
    settings = settings or get_settings()
    problems: list[str] = []

    known_ids = {fact.id for fact in packet.facts}
    unknown = [claim for claim in output.claims_used if claim not in known_ids]
    if unknown:
        problems.append(f"cites unknown evidence ids: {', '.join(unknown[:5])}")
    if packet.facts and not [claim for claim in output.claims_used if claim in known_ids]:
        problems.append("no evidence cited although evidence exists")

    haystack = " ".join(
        [output.sales_angle, output.pain_point, output.email_subject, output.email_body,
         output.video_intro, output.video_outro]
    ).lower()
    for phrase in settings.banned_marketing_phrases:
        if phrase in haystack:
            problems.append(f"uses banned phrase {phrase!r}")
    for pattern in FABRICATION_PATTERNS:
        if pattern in haystack:
            problems.append(f"contains an unsupported business metric ({pattern!r})")

    words = word_count(output.email_body)
    if not 40 <= words <= 160:
        problems.append(f"email body is {words} words (expected 40-160)")
    if len(output.email_subject) > 90:
        problems.append("email subject is too long")
    if not output.demo_questions:
        problems.append("no demo questions produced")

    return GroundingReport(ok=not problems, problems=problems)


def generate_personalization(
    packet: LeadPacket,
    provider: LLMProvider,
    settings: Settings | None = None,
) -> tuple[PersonalizationOutput, GroundingReport]:
    """Generate once, re-ask once if the grounding check fails, then give up."""
    settings = settings or get_settings()
    schema_json = json.dumps(PersonalizationOutput.model_json_schema(), indent=2)
    prompt = build_personalization_prompt(packet, schema_json)
    context = packet.model_dump()

    output = provider.generate_structured(prompt, PersonalizationOutput, context=context)
    report = check_grounding(output, packet, settings)
    if report.ok:
        return output, report

    corrective = (
        f"{prompt}\n\nYour previous draft was rejected for these reasons:\n"
        + "\n".join(f"- {problem}" for problem in report.problems)
        + "\nFix them. Use only the supplied facts and cite their ids in claims_used."
    )
    retry = provider.generate_structured(corrective, PersonalizationOutput, context=context)
    retry_report = check_grounding(retry, packet, settings)
    if retry_report.ok:
        return retry, retry_report
    raise LLMError("personalization failed grounding check twice: " + "; ".join(retry_report.problems))
