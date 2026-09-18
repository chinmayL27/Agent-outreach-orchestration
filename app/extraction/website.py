"""Website resolution and candidate-page discovery.

NPPES has no website field, so a website arrives from one of:
  1. the lead source itself (CSV import),
  2. a `website_map` in campaign.yaml (normalized clinic name -> URL),
  3. an email domain already known for the lead.
Nothing is guessed from thin air: a lead without a website simply stays
un-enriched rather than being enriched against the wrong site.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.extraction.normalize import normalize_name
from app.models.campaign import CampaignConfig
from app.util.http import normalize_url

#: Pages worth visiting, in priority order.
PRIORITY_PATHS = (
    "/about", "/about-us", "/services", "/our-services", "/providers", "/our-providers",
    "/team", "/our-team", "/doctors", "/staff", "/contact", "/contact-us",
    "/appointments", "/request-appointment", "/book-online", "/patients", "/faq", "/faqs",
    "/locations", "/new-patients",
)
PATH_KEYWORDS = (
    "about", "service", "provider", "team", "doctor", "physician", "contact",
    "appointment", "booking", "faq", "location", "patient", "specialt",
)
SKIP_KEYWORDS = (
    "privacy", "terms", "hipaa", "sitemap.xml", "login", "portal", "blog/",
    "careers", "wp-content", "wp-login", ".pdf", ".jpg", ".png", "javascript:",
    "tel:", "mailto:", "#",
)


def resolve_website(
    organization_name: str,
    campaign: CampaignConfig,
    known_website: str | None = None,
    emails: list[str] | None = None,
) -> str | None:
    if known_website:
        return normalize_url(known_website)
    key = normalize_name(organization_name)
    for mapped_name, url in campaign.website_map.items():
        if normalize_name(mapped_name) == key:
            return normalize_url(url)
    for email in emails or []:
        domain = email.split("@")[-1].lower()
        if domain and "." in domain and domain not in {"gmail.com", "yahoo.com", "hotmail.com", "aol.com"}:
            return normalize_url(domain)
    return None


def _is_useful(url: str) -> bool:
    lowered = url.lower()
    if any(skip in lowered for skip in SKIP_KEYWORDS):
        return False
    return True


def candidate_links(base_url: str, html: str, limit: int = 40) -> list[str]:
    """Links on the page that look like clinic-information pages."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    priority: list[str] = []
    secondary: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or not _is_useful(href):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if absolute in seen:
            continue
        seen.add(absolute)
        path = urlparse(absolute).path.lower().rstrip("/")
        text = (anchor.get_text() or "").strip().lower()
        if path in PRIORITY_PATHS or any(keyword in path for keyword in PATH_KEYWORDS):
            priority.append(absolute)
        elif any(keyword in text for keyword in PATH_KEYWORDS):
            secondary.append(absolute)
    return (priority + secondary)[:limit]


def sitemap_urls(base_url: str, xml: str, limit: int = 40) -> list[str]:
    soup = BeautifulSoup(xml, "html.parser")
    urls = []
    for loc in soup.find_all("loc"):
        url = (loc.get_text() or "").strip()
        if url and _is_useful(url):
            urls.append(normalize_url(urljoin(base_url, url)))
    prioritized = [u for u in urls if any(k in u.lower() for k in PATH_KEYWORDS)]
    return (prioritized + [u for u in urls if u not in prioritized])[:limit]
