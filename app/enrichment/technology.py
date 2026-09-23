"""Detect visitor-facing technology: existing chat widgets and online booking."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.enrichment.crawler import PageContent

CHATBOT_SIGNATURES = {
    "intercom": ("intercom.io", "widget.intercom", "intercomsettings"),
    "drift": ("drift.com", "js.driftt.com"),
    "tidio": ("tidio.co", "tidiochat"),
    "tawk.to": ("tawk.to",),
    "podium": ("podium.com", "connect.podium.com"),
    "birdeye": ("birdeye.com", "birdeye-webchat"),
    "hubspot chat": ("js.hs-scripts.com", "hubspot conversations"),
    "zendesk chat": ("zdassets.com", "zendesk chat", "zopim"),
    "livechat": ("livechatinc.com",),
    "olark": ("olark.com",),
    "gorgias": ("gorgias.chat",),
    "generic chat widget": ("chat-widget", "chatbot", "chat with us", "live chat"),
}

BOOKING_SIGNATURES = {
    "Zocdoc": ("zocdoc.com",),
    "NexHealth": ("nexhealth.com",),
    "Phreesia": ("phreesia.",),
    "athenahealth": ("athenahealth.com",),
    "Solutionreach": ("solutionreach.com",),
    "Dentrix/Sesame": ("sesamecommunications.com",),
    "LocalMed": ("localmed.com",),
    "Klara": ("klara.com",),
    "Luma Health": ("lumahealth.io",),
    "Patient portal": ("patientportal", "mychart", "follow my health", "followmyhealth"),
    "Online request form": ("request an appointment", "book online", "book an appointment",
                            "schedule online", "request appointment", "schedule an appointment"),
}

APPOINTMENT_LINK_HINTS = (
    "appointment", "book", "schedule", "request-visit", "booking", "reserve",
)

_QUESTION = re.compile(r"\?\s*$")


@dataclass
class TechFindings:
    has_chatbot: bool
    chatbot_vendor: str | None
    has_online_booking: bool
    booking_system: str | None
    appointment_url: str | None
    faq_questions: list[str]
    #: attribute -> source url, for Evidence rows.
    sources: dict[str, str]


def _haystack(page: PageContent) -> str:
    return f"{page.html}\n{page.text}".lower()


def detect(pages: list[PageContent]) -> TechFindings:
    chatbot_vendor: str | None = None
    booking_system: str | None = None
    appointment_url: str | None = None
    faq_questions: list[str] = []
    sources: dict[str, str] = {}

    for page in pages:
        haystack = _haystack(page)
        if chatbot_vendor is None:
            for vendor, needles in CHATBOT_SIGNATURES.items():
                if any(needle in haystack for needle in needles):
                    chatbot_vendor = vendor
                    sources["has_chatbot"] = page.url
                    break
        if booking_system is None:
            for system, needles in BOOKING_SIGNATURES.items():
                if any(needle in haystack for needle in needles):
                    booking_system = system
                    sources["has_online_booking"] = page.url
                    break
        if appointment_url is None:
            if any(hint in page.path for hint in APPOINTMENT_LINK_HINTS):
                appointment_url = page.url
                sources["appointment_url"] = page.url
        for heading in page.headings:
            if _QUESTION.search(heading) and 10 <= len(heading) <= 160:
                if heading not in faq_questions:
                    faq_questions.append(heading)
                    sources.setdefault("faq", page.url)

    return TechFindings(
        has_chatbot=chatbot_vendor is not None,
        chatbot_vendor=chatbot_vendor,
        has_online_booking=booking_system is not None,
        booking_system=booking_system,
        appointment_url=appointment_url,
        faq_questions=faq_questions[:25],
        sources=sources,
    )
