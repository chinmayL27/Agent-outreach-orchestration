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


#: Mailbox providers whose domain says nothing about the practice's website.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
    "protonmail.com", "proton.me", "gmx.com", "mail.com", "zoho.com",
}


def is_freemail(domain: str | None) -> bool:
    return (domain or "").lower().removeprefix("www.") in FREEMAIL_DOMAINS


def domain_of_email(email: str | None) -> str | None:
    """The domain half of an address, or None for a free mailbox provider."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower().removeprefix("www.")
    if not domain or "." not in domain or is_freemail(domain):
        return None
    return domain


def name_from_domain(domain: str | None) -> str | None:
    """A provisional display name for a lead we only know by its domain.

    `cedar-peds-clinic.example` -> "Cedar Peds Clinic".  This is a placeholder,
    not a fact: enrichment replaces it with the name the site calls itself.
    """
    host = (domain or "").strip().lower().removeprefix("www.")
    if not host or "." not in host:
        return None
    labels = host.split(".")
    # Drop the public suffix so the registrable label is left: "example.com"
    # and "example.co.uk" both reduce to "example".
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "gov", "ac"} and len(labels[-1]) == 2:
        labels = labels[:-2]
    elif len(labels) >= 2:
        labels = labels[:-1]
    stem = labels[-1] if labels else ""
    words = [word for word in _SPACES.split(_PUNCT.sub(" ", stem)) if word]
    return " ".join(word.capitalize() for word in words) or None
