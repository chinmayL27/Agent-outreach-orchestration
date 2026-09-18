"""Name/phone/address normalization and dedupe keys."""

from __future__ import annotations

import re
from urllib.parse import urlparse

LEGAL_SUFFIXES = {
    "llc", "l.l.c", "inc", "inc.", "pc", "p.c", "pa", "p.a", "pllc", "plc", "ltd",
    "corp", "corporation", "co", "company", "md", "m.d", "do", "d.o", "dds", "d.d.s",
    "dmd", "np", "pa-c", "group", "the",
}
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and legal suffixes -> a dedupe-friendly key."""
    lowered = _PUNCT.sub(" ", (name or "").lower())
    tokens = [token for token in _SPACES.split(lowered) if token and token not in LEGAL_SUFFIXES]
    # "P.C." survives punctuation stripping as ["p", "c"]; drop trailing initials.
    while tokens and len(tokens[-1]) == 1:
        tokens.pop()
    return " ".join(tokens).strip()


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"


def normalize_state(state: str | None) -> str | None:
    if not state:
        return None
    return state.strip().upper()[:2] or None


def normalize_city(city: str | None) -> str | None:
    if not city:
        return None
    return " ".join(part.capitalize() for part in city.strip().split())


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "//" in url else f"https://{url}")
    host = parsed.netloc.lower().removeprefix("www.")
    return host or None


def dedupe_key(organization_name: str, city: str | None, state: str | None, website: str | None = None) -> str:
    """Prefer the domain (strongest signal), else normalized name + place."""
    domain = domain_of(website)
    if domain:
        return f"domain:{domain}"
    place = f"{normalize_city(city) or ''}|{normalize_state(state) or ''}".lower()
    return f"name:{normalize_name(organization_name)}|{place}"


def is_excluded(name: str, exclude_keywords: list[str]) -> bool:
    lowered = (name or "").lower()
    return any(keyword.lower() in lowered for keyword in exclude_keywords if keyword)


CREDENTIALS = {"md", "do", "dds", "dmd", "np", "pa", "pac", "pa-c", "fnp", "phd", "rn", "dr"}


def person_key(name: str) -> str:
    """Normalized person name with credentials and titles removed."""
    lowered = _PUNCT.sub(" ", (name or "").lower())
    tokens = [t for t in _SPACES.split(lowered) if t and t not in CREDENTIALS and len(t) > 1]
    return " ".join(tokens)


def merge_provider_names(existing: list[str], new: list[str]) -> list[str]:
    """Merge two provider lists, keeping the richest spelling of each person.

    NPPES gives "Jane Smith"; the website gives "Jane Smith, MD".  They are the
    same person and must not be counted twice.
    """
    merged: dict[str, str] = {}
    for name in [*existing, *new]:
        key = person_key(name)
        if not key:
            continue
        current = merged.get(key)
        if current is None or len(name) > len(current):
            merged[key] = name
    return list(merged.values())
