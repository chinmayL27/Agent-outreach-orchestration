"""Build the LeadPacket - the only clinic data a prompt is ever shown."""

from __future__ import annotations

from typing import Iterable

from app.config import Settings, get_settings
from app.models.campaign import CampaignConfig
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.models.schemas import ClinicSummary, Fact, LeadPacket, ProductSummary

#: Evidence attributes that are safe and useful to expose to the model.
PACKET_ATTRIBUTES = (
    "service", "provider", "location", "faq", "online_booking", "chatbot",
    "no_chatbot", "appointment_url",
)


def build_packet(
    lead: Lead,
    evidence: Iterable[Evidence],
    campaign: CampaignConfig,
    settings: Settings | None = None,
) -> LeadPacket:
    settings = settings or get_settings()
    facts = [
        Fact(id=item.id, fact=f"{item.attribute}: {item.value}", source=item.source_url)
        for item in evidence
        if item.attribute in PACKET_ATTRIBUTES
    ][:40]

    clinic = ClinicSummary(
        name=lead.organization_name,
        specialty=list(lead.specialty or []),
        location=lead.location_label,
        provider_names=list(lead.provider_names or [])[:6],
        services=list(lead.services or [])[:12],
        locations_count=max(1, len(lead.locations or [])),
        has_online_booking=lead.has_online_booking,
        has_chatbot=lead.has_chatbot,
    )
    return LeadPacket(
        lead_id=lead.id,
        clinic=clinic,
        facts=facts,
        product=ProductSummary(
            name=campaign.product.name, capabilities=list(campaign.product.capabilities)
        ),
        sender_name=settings.sender_name,
        sender_company=settings.sender_company,
    )
