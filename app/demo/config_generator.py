"""Turn a lead + its personalization into demo configuration JSON.

The product is never rebuilt per customer: one generic demo app consumes this
config.  Customization is configuration, not development.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import Settings, get_settings
from app.models.lead import Lead
from app.models.outreach import Personalization
from app.models.schemas import DemoConfig

PALETTE = ("#0f766e", "#1d4ed8", "#7c3aed", "#b45309", "#be123c", "#0369a1")

CLINICAL_TRIGGERS = (
    "should i", "do i have", "is it serious", "diagnos", "treat my", "what medication",
    "symptom", "prescri", "is this cancer", "how do i cure",
)


def _accent_for(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return PALETTE[int(digest[:8], 16) % len(PALETTE)]


def synthesize_answer(question: str, lead: Lead) -> str:
    """Compose a safe, evidence-backed demo answer.

    Every answer is assembled from what the clinic already publishes.  Clinical
    questions are deflected to the practice - the demo never advises.
    """
    lowered = question.lower()
    services = list(lead.services or [])
    locations = list(lead.locations or [])
    booking = lead.appointment_url or lead.website or ""
    # Local fixture sites are file:// URLs; never show those to a viewer.
    if booking.startswith("file://"):
        booking = ""

    if any(trigger in lowered for trigger in CLINICAL_TRIGGERS):
        return (
            "I can't give medical advice, but I can get you to the right person. "
            + (f"{lead.organization_name} lists {services[0].lower()} among its services - "
               if services else "")
            + "I can help you request an appointment or reach the front desk"
            + (f" at {lead.phone}." if lead.phone else ".")
        )

    if any(word in lowered for word in ("book", "appointment", "schedule", "availability")):
        if lead.has_online_booking:
            where = f" at {booking}" if booking else " through the request form on your website"
            return (
                f"You can request an appointment online{where}. "
                "Tell me which service you need and I'll take you straight to the right form."
            )
        return (
            f"Call the office{f' at {lead.phone}' if lead.phone else ''} to book, or I can "
            "collect your details and pass them to the front desk."
        )

    if any(word in lowered for word in ("where", "location", "address", "parking", "hours")):
        if locations:
            return (
                f"{lead.organization_name} lists {len(locations)} location(s): "
                + "; ".join(locations[:3])
                + ". Which one is most convenient for you?"
            )
        return (
            f"{lead.organization_name} is based in {lead.location_label or 'your area'}"
            + (f", and you can reach the office at {lead.phone}" if lead.phone else "")
            + "."
        )

    if any(word in lowered for word in ("insurance", "cost", "price", "pay", "referral")):
        return (
            "Coverage and referral requirements vary by plan, so the front desk confirms those "
            "directly"
            + (f" - you can reach them at {lead.phone}." if lead.phone else ".")
            + " I can pass along your plan details so they can check before your visit."
        )

    if any(word in lowered for word in ("new patient", "accepting", "register")):
        return (
            f"{lead.organization_name} publishes its services and provider list on the site. "
            "I can start a new-patient request and route it to the front desk."
        )

    matched = next((service for service in services if service.lower() in lowered), None)
    if matched:
        extra = (
            f" It's listed under the services on {lead.website}."
            if lead.website and not lead.website.startswith("file://")
            else " It's listed under the services on your site."
        )
        return (
            f"Yes - {matched} is one of the services {lead.organization_name} lists.{extra} "
            "Would you like me to help you request an appointment for it?"
        )

    if services:
        return (
            f"{lead.organization_name} lists services including "
            + ", ".join(service.lower() for service in services[:3])
            + ". Tell me what you need and I'll point you to the right one."
        )
    return (
        f"I can answer questions from {lead.organization_name}'s website and route you to the "
        "right service or to the front desk. What are you looking for?"
    )


def build_demo_config(
    lead: Lead,
    personalization: Personalization,
    settings: Settings | None = None,
) -> DemoConfig:
    settings = settings or get_settings()
    questions = list(personalization.demo_questions or [])[:3]
    return DemoConfig(
        lead_id=lead.id,
        business_name=lead.organization_name,
        specialty=(lead.specialty or [""])[0],
        location=lead.location_label,
        accent_color=_accent_for(lead.organization_name),
        services=list(lead.services or [])[:6],
        suggested_questions=questions,
        answers={question: synthesize_answer(question, lead) for question in questions},
        intro_text=personalization.video_intro,
        outro_text=personalization.video_outro,
        cta_text=f"Built by {settings.sender_company} from {lead.organization_name}'s public website.",
    )


def write_demo_config(config: DemoConfig, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    settings.ensure_dirs()
    path = settings.demos_dir / f"{config.lead_id}.json"
    path.write_text(json.dumps(config.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
