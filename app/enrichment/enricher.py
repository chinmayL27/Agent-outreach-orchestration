"""Turn a crawled website into lead fields + evidence rows.

Only facts get stored here.  Interpretation (sales angle, use cases) happens
later, in the personalization stage, and only from these evidence rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.enrichment import services as service_extractor
from app.enrichment import technology as tech_detector
from app.enrichment.crawler import CrawlResult, crawl_site
from app.extraction.email import extract_emails, rank_emails
from app.extraction.normalize import domain_of
from app.util.http import Fetcher
from app.util.text import excerpt_around, truncate


@dataclass
class EvidenceItem:
    attribute: str
    value: str
    source_url: str
    page_title: str | None = None
    excerpt: str | None = None


@dataclass
class EnrichmentOutcome:
    fields: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    partial: bool = False
    pages_crawled: int = 0

    @property
    def ok(self) -> bool:
        return self.pages_crawled > 0


def summarize(fields: dict[str, Any], organization_name: str) -> str:
    """A factual one-liner - counts and observations only, no interpretation."""
    parts: list[str] = [organization_name]
    if fields.get("locations"):
        parts.append(f"{len(fields['locations'])} listed location(s)")
    if fields.get("provider_count"):
        parts.append(f"{fields['provider_count']} named provider(s)")
    if fields.get("services"):
        parts.append(f"{len(fields['services'])} listed service(s)")
    if fields.get("has_online_booking"):
        parts.append(f"online booking via {fields.get('booking_system') or 'website form'}")
    if fields.get("has_chatbot") is False:
        parts.append("no chat widget detected")
    if fields.get("faq_questions"):
        parts.append(f"{len(fields['faq_questions'])} FAQ question(s) published")
    return "; ".join(parts)


def enrich_from_crawl(crawl: CrawlResult, organization_name: str) -> EnrichmentOutcome:
    outcome = EnrichmentOutcome(pages_crawled=len(crawl.pages), errors=list(crawl.errors))
    if not crawl.pages:
        outcome.partial = True
        return outcome

    pages = crawl.pages
    home = pages[0]
    site_domain = domain_of(crawl.base_url)

    tech = tech_detector.detect(pages)
    svc = service_extractor.extract(pages, organization_name)

    emails: list[str] = []
    for page in pages:
        emails += extract_emails(page.text, page.mailto_links)
    emails = rank_emails(emails, site_domain)

    fields: dict[str, Any] = {
        "services": svc.services,
        "locations": svc.locations,
        "provider_count": len(svc.provider_names) or None,
        "has_chatbot": tech.has_chatbot,
        "has_online_booking": tech.has_online_booking,
        "booking_system": tech.booking_system,
        "appointment_url": tech.appointment_url,
        "faq_questions": tech.faq_questions,
        "emails": emails,
        "provider_names": svc.provider_names,
    }
    fields["company_summary"] = summarize(fields, organization_name)

    page_by_url = {page.url: page for page in pages}

    def add(attribute: str, value: str, url: str | None, title: str | None = None) -> None:
        source = url or home.url
        page = page_by_url.get(source, home)
        outcome.evidence.append(
            EvidenceItem(
                attribute=attribute,
                value=truncate(value, 240),
                source_url=source,
                page_title=title or page.title,
                excerpt=excerpt_around(page.text, value),
            )
        )

    for service in svc.services[:12]:
        add("service", service, svc.sources.get("services"))
    for provider in svc.provider_names[:10]:
        add("provider", provider, svc.sources.get("providers"))
    for location in svc.locations[:6]:
        add("location", location, svc.sources.get("locations"))
    for question in tech.faq_questions[:8]:
        add("faq", question, tech.sources.get("faq"))
    if tech.has_online_booking:
        add(
            "online_booking",
            f"Online appointment booking available ({tech.booking_system})",
            tech.sources.get("has_online_booking"),
        )
    if tech.has_chatbot:
        add("chatbot", f"Existing chat widget detected ({tech.chatbot_vendor})",
            tech.sources.get("has_chatbot"))
    else:
        add("no_chatbot", "No chat widget detected on the public site", home.url)
    if tech.appointment_url:
        add("appointment_url", tech.appointment_url, tech.sources.get("appointment_url"))
    for email in emails[:3]:
        add("public_email", email, home.url)

    outcome.fields = fields
    outcome.partial = bool(crawl.errors)
    return outcome


def enrich_website(
    website: str,
    organization_name: str,
    settings: Settings | None = None,
    fetcher: Fetcher | None = None,
) -> EnrichmentOutcome:
    settings = settings or get_settings()
    crawl = crawl_site(website, settings=settings, fetcher=fetcher)
    return enrich_from_crawl(crawl, organization_name)
