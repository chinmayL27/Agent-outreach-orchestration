"""Extract services, provider names and office locations from crawled pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.enrichment.crawler import PageContent

_PROVIDER_RE = re.compile(
    r"\b(?:Dr\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)"
    r",?\s*(MD|M\.D\.|DO|D\.O\.|DDS|D\.D\.S\.|DMD|NP|PA-C|FNP|PhD)\b"
)
_DR_RE = re.compile(r"\bDr\.?\s+([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){0,2})")
_CITY_STATE_RE = re.compile(r"\b([A-Z][a-zA-Z .'\-]{2,30}),\s*([A-Z]{2})\b(?:\s+\d{5})?")

SERVICE_STOPWORDS = {
    "home", "about", "about us", "contact", "contact us", "our team", "team",
    "privacy policy", "terms of use", "menu", "search", "read more", "learn more",
    "patient portal", "careers", "blog", "news", "testimonials", "reviews",
    "insurance", "hours", "directions", "faq", "faqs", "services", "our services",
    "our providers", "providers", "patient faq", "our doctors", "doctors",
    "meet the team", "locations", "our locations", "new patients", "welcome",
}
#: Headings that are calls to action or navigation, never a service.
ACTION_PREFIXES = (
    "request", "schedule", "book", "call", "contact", "welcome", "meet",
    "why choose", "our ", "new patient", "patient ", "read ", "learn ",
)
SERVICE_PAGE_HINTS = ("service", "treatment", "procedure", "specialt", "conditions", "what-we-do")
NON_SERVICE_CHARS = re.compile(r"[@|]|\d{3}[-.)]\d{3}")


@dataclass
class ServiceFindings:
    services: list[str] = field(default_factory=list)
    provider_names: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)


def _is_provider_heading(candidate: str) -> bool:
    return bool(_PROVIDER_RE.search(candidate) or _DR_RE.match(candidate))


def _clean_candidate(raw: str) -> str | None:
    candidate = " ".join(raw.split()).strip(" .,:;-–—")
    if not (3 <= len(candidate) <= 60):
        return None
    if candidate.lower() in SERVICE_STOPWORDS or NON_SERVICE_CHARS.search(candidate):
        return None
    if candidate.endswith("?") or candidate.count(" ") > 6:
        return None
    if _is_provider_heading(candidate):
        return None
    if any(candidate.lower().startswith(prefix) for prefix in ACTION_PREFIXES):
        return None
    letters = sum(char.isalpha() for char in candidate)
    if letters < 3:
        return None
    return candidate


def extract(pages: list[PageContent], organization_name: str = "") -> ServiceFindings:
    """Services come from service pages when the site has them.

    Falling back to homepage headings on every site would turn navigation
    labels and provider names into "services", so the fallback only applies
    when no service page was crawled.
    """
    findings = ServiceFindings()
    org_key = " ".join(organization_name.lower().split())
    service_pages = [page for page in pages if any(hint in page.path for hint in SERVICE_PAGE_HINTS)]
    sources = service_pages or pages[:1]

    for page in sources:
        candidates = list(page.headings)
        if page in service_pages:
            candidates += page.list_items
        for raw in candidates:
            cleaned = _clean_candidate(raw)
            if not cleaned or cleaned in findings.services:
                continue
            if org_key and org_key in cleaned.lower():
                continue
            findings.services.append(cleaned)
            findings.sources.setdefault("services", page.url)

    for page in pages:
        for match in _PROVIDER_RE.finditer(page.text):
            name = f"{match.group(1)}, {match.group(2).replace('.', '')}"
            if name not in findings.provider_names:
                findings.provider_names.append(name)
                findings.sources.setdefault("providers", page.url)
        for match in _DR_RE.finditer(page.text):
            name = f"Dr. {match.group(1)}"
            if all(match.group(1) not in existing for existing in findings.provider_names):
                findings.provider_names.append(name)
                findings.sources.setdefault("providers", page.url)

        if "contact" in page.path or "location" in page.path or page.path in {"", "/"}:
            for match in _CITY_STATE_RE.finditer(page.text):
                label = f"{match.group(1).strip()}, {match.group(2)}"
                if label not in findings.locations and len(findings.locations) < 10:
                    findings.locations.append(label)
                    findings.sources.setdefault("locations", page.url)

    findings.services = findings.services[:30]
    findings.provider_names = findings.provider_names[:25]
    return findings
