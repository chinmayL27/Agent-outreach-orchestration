"""Public contact email extraction, scrubbing and ranking."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: Vendor / tracking / placeholder addresses that are never a real contact.
JUNK_DOMAINS = {
    "example.com", "example.org", "sentry.io", "wixpress.com", "wix.com",
    "squarespace.com", "godaddy.com", "email.com", "domain.com", "yourdomain.com",
    "sentry.wixpress.com",
}
JUNK_LOCALPARTS = {"no-reply", "noreply", "donotreply", "do-not-reply", "postmaster", "abuse"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

#: Higher score = better first-contact address.
PREFERRED_LOCALPARTS = {
    "info": 50, "contact": 48, "hello": 46, "office": 44, "frontdesk": 42,
    "front.desk": 42, "reception": 40, "appointments": 38, "admin": 30, "billing": 5,
    "careers": 1, "jobs": 1,
}


def _looks_like_file(candidate: str) -> bool:
    return candidate.lower().endswith(IMAGE_SUFFIXES)


def is_valid_email(candidate: str) -> bool:
    candidate = candidate.strip().strip(".,;:()<>[]\"'")
    if not candidate or not EMAIL_RE.fullmatch(candidate) or _looks_like_file(candidate):
        return False
    local, _, domain = candidate.lower().partition("@")
    if domain in JUNK_DOMAINS or local in JUNK_LOCALPARTS:
        return False
    return not domain.endswith(".png") and "." in domain


def score_email(candidate: str, site_domain: str | None = None) -> int:
    local, _, domain = candidate.lower().partition("@")
    score = PREFERRED_LOCALPARTS.get(local, 20)
    if site_domain and domain == site_domain.lower().removeprefix("www."):
        score += 25
    if any(part in local for part in ("dr", "dr.")):
        score += 5
    return score


def extract_emails(text: str, mailto_links: list[str] | None = None) -> list[str]:
    """Extract unique, plausible public email addresses (mailto links first)."""
    found: list[str] = []
    for link in mailto_links or []:
        address = link.removeprefix("mailto:").split("?")[0].strip()
        if is_valid_email(address):
            found.append(address.lower())
    for match in EMAIL_RE.findall(text or ""):
        if is_valid_email(match):
            found.append(match.lower())
    seen: set[str] = set()
    unique = []
    for address in found:
        if address not in seen:
            seen.add(address)
            unique.append(address)
    return unique


def rank_emails(candidates: list[str], site_domain: str | None = None) -> list[str]:
    return sorted(set(candidates), key=lambda c: (-score_email(c, site_domain), c))
