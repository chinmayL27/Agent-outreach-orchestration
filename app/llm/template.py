"""Deterministic provider - assembles personalization from the lead packet.

No model call, no tokens, no network.  It exists so the whole pipeline can be
run, tested and demoed end-to-end before an LLM is wired up, and so CI stays
hermetic.  Output shape is identical to the model-backed providers.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from app.llm.base import LLMError, LLMProvider
from app.models.schemas import LeadPacket, PersonalizationOutput
from app.util.text import BEGIN_MARKER, END_MARKER

T = TypeVar("T", bound=BaseModel)

GENERIC_QUESTIONS = [
    "Are you accepting new patients right now?",
    "What are your office hours and where are you located?",
    "How do I book an appointment?",
]


def _facts_by_attribute(packet: LeadPacket) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for fact in packet.facts:
        attribute, _, value = fact.fact.partition(": ")
        grouped.setdefault(attribute, []).append(value or fact.fact)
    return grouped


def _demo_questions(packet: LeadPacket, grouped: dict[str, list[str]]) -> list[str]:
    questions: list[str] = []
    for question in grouped.get("faq", [])[:2]:
        questions.append(question if question.endswith("?") else f"{question}?")
    services = grouped.get("service", []) or packet.clinic.services
    if services:
        questions.append(f"Do you offer {services[0].lower()}?")
    if len(services) > 1:
        questions.append(f"Which location handles {services[1].lower()}?")
    if packet.clinic.has_online_booking:
        questions.append("How do I book an appointment online?")
    for fallback in GENERIC_QUESTIONS:
        if len(questions) >= 3:
            break
        questions.append(fallback)
    deduped: list[str] = []
    for question in questions:
        if question not in deduped:
            deduped.append(question)
    return deduped[:3]


def build_personalization(packet: LeadPacket) -> PersonalizationOutput:
    grouped = _facts_by_attribute(packet)
    clinic = packet.clinic
    services = grouped.get("service", []) or clinic.services
    service_phrase = " and ".join(s.lower() for s in services[:2]) if services else "your services"
    location_phrase = clinic.location or (grouped.get("location", [""])[0])
    faq_count = len(grouped.get("faq", []))

    observations: list[str] = []
    if services:
        observations.append(f"you list {service_phrase} on your site")
    if clinic.locations_count > 1:
        observations.append(f"across {clinic.locations_count} listed locations")
    if clinic.has_online_booking:
        observations.append("and already offer online booking")
    observation = " ".join(observations) if observations else "I had a look at your website"

    if clinic.has_chatbot is False:
        pain_point = (
            "Patients have to search the site (or call the front desk) for answers that are "
            "already published - there is no assistant on the page to answer them."
        )
    else:
        pain_point = "An existing chat widget is in place; the opportunity is deeper routing and coverage."

    sales_angle = (
        f"{clinic.name} publishes {len(services)} service(s)"
        + (f" and {faq_count} FAQ question(s)" if faq_count else "")
        + f" on its public site{' with no chat widget detected' if clinic.has_chatbot is False else ''}. "
        "That published content is exactly what an on-site assistant can answer on the patient's behalf."
    )

    use_cases = packet.product.capabilities[:3] or ["answering published patient questions"]
    questions = _demo_questions(packet, grouped)

    # First name only: "Hi Jane," reads like a person, "Hi Jane Smith, MD," does not.
    greeting_target = (
        clinic.provider_names[0].split(",")[0].split()[0] if clinic.provider_names else "there"
    )
    body = (
        f"Hi {greeting_target},\n\n"
        f"I was looking at {clinic.name}"
        + (f" in {location_phrase}" if location_phrase else "")
        + f" and noticed {observation}.\n\n"
        f"I put together a short example showing how an assistant on your site could answer "
        f"questions like \"{questions[0]}\" and point patients to the right service or location. "
        f"It is built only from what is already public on your website.\n\n"
        "Would it be worth 15 minutes to see whether it fits your patient-support workflow?"
    )

    return PersonalizationOutput(
        sales_angle=sales_angle,
        pain_point=pain_point,
        relevant_use_cases=use_cases,
        demo_questions=questions,
        email_subject=f"A 60-second assistant demo for {clinic.name}",
        email_body=body,
        video_intro=(
            f"Hi {clinic.name} - here is a quick example of how a patient assistant "
            "could work on your website."
        ),
        video_outro=(
            "Happy to build this against your actual patient questions - just reply if "
            "you'd like to see more."
        ),
        claims_used=[fact.id for fact in packet.facts[:8]],
    )


class TemplateProvider(LLMProvider):
    name = "template"

    def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        context: dict[str, Any] | None = None,
    ) -> T:
        if schema is not PersonalizationOutput:
            raise LLMError(f"template provider only supports PersonalizationOutput, got {schema.__name__}")
        packet = LeadPacket.model_validate(context) if context else self._packet_from_prompt(prompt)
        return build_personalization(packet)  # type: ignore[return-value]

    def complete(self, prompt: str) -> str:
        return build_personalization(self._packet_from_prompt(prompt)).model_dump_json()

    @staticmethod
    def _packet_from_prompt(prompt: str) -> LeadPacket:
        start = prompt.find(BEGIN_MARKER)
        end = prompt.find(END_MARKER)
        if start == -1 or end == -1:
            raise LLMError("template provider needs a lead packet in the prompt envelope")
        payload = prompt[start + len(BEGIN_MARKER) : end].strip()
        try:
            return LeadPacket.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"could not read lead packet from prompt: {exc}") from exc
