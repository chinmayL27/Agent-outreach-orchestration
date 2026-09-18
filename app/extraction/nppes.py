"""Lead sources.

The `LeadSource` protocol is the seam that lets you swap NPPES for Google
Places, a purchased list, a CRM export, or a local fixture without touching
any other stage.

NPPES caveat: NPI registration is *not* evidence that a provider is currently
licensed or credentialed, and the registry contains no website field.  Websites
come from a campaign `website_map`, a CSV import, or a later discovery step.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

import httpx

from app.config import get_settings
from app.extraction.normalize import (
    dedupe_key,
    is_excluded,
    normalize_city,
    normalize_phone,
    normalize_state,
)
from app.models.campaign import CampaignConfig
from app.util.text import title_case_name

NPPES_ENDPOINT = "https://npiregistry.cms.hhs.gov/api/"
NPPES_VERSION = "2.1"
NPPES_PAGE_SIZE = 200


@dataclass
class RawLead:
    """Source-agnostic discovery output."""

    organization_name: str
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    specialty: list[str] = field(default_factory=list)
    provider_names: list[str] = field(default_factory=list)
    npi_numbers: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    source: str = "unknown"

    @property
    def key(self) -> str:
        return dedupe_key(self.organization_name, self.city, self.state, self.website)


@dataclass
class LeadQuery:
    specialties: list[str]
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    limit: int = 50
    exclude_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_campaign(cls, campaign: CampaignConfig) -> "LeadQuery":
        return cls(
            specialties=campaign.specialties,
            city=campaign.geography.city,
            state=campaign.geography.state,
            postal_code=campaign.geography.postal_code,
            limit=campaign.maximum_leads,
            exclude_keywords=campaign.exclude_keywords,
        )


class LeadSource(Protocol):
    name: str

    def search(self, query: LeadQuery) -> list[RawLead]: ...


# ---------------------------------------------------------------------------
# NPPES
# ---------------------------------------------------------------------------
def parse_nppes_result(result: dict[str, Any]) -> RawLead | None:
    """Map one NPPES API result into a RawLead (organizations and individuals)."""
    basic = result.get("basic", {}) or {}
    enumeration_type = result.get("enumeration_type", "NPI-2")

    provider_names: list[str] = []
    if enumeration_type == "NPI-2":
        organization_name = basic.get("organization_name") or basic.get("name") or ""
        if basic.get("authorized_official_first_name"):
            provider_names.append(
                " ".join(
                    part
                    for part in (
                        basic.get("authorized_official_first_name"),
                        basic.get("authorized_official_last_name"),
                    )
                    if part
                )
            )
    else:
        person = " ".join(
            part for part in (basic.get("first_name"), basic.get("last_name")) if part
        ).title()
        organization_name = (
            basic.get("organization_name")
            or (result.get("other_names") or [{}])[0].get("organization_name")
            or person
        )
        if person:
            provider_names.append(person)

    if not organization_name:
        return None

    locations = [a for a in result.get("addresses", []) or [] if a.get("address_purpose") == "LOCATION"]
    address = locations[0] if locations else ((result.get("addresses") or [{}])[0])

    taxonomies = result.get("taxonomies") or []
    specialty = [t.get("desc") for t in taxonomies if t.get("desc")]
    primary = [t.get("desc") for t in taxonomies if t.get("primary") and t.get("desc")]
    specialty = (primary + [s for s in specialty if s not in primary]) or []

    return RawLead(
        organization_name=title_case_name(organization_name.strip()),
        city=normalize_city(address.get("city")),
        state=normalize_state(address.get("state")),
        postal_code=(address.get("postal_code") or "")[:5] or None,
        address=", ".join(
            part
            for part in (address.get("address_1"), address.get("address_2"))
            if part
        )
        or None,
        phone=normalize_phone(address.get("telephone_number")),
        specialty=specialty,
        provider_names=[name for name in provider_names if name.strip()],
        npi_numbers=[str(result.get("number"))] if result.get("number") else [],
        source="nppes",
    )


def merge_raw_leads(raw_leads: Iterable[RawLead]) -> list[RawLead]:
    """Collapse individual NPIs at the same practice into one organization lead."""
    merged: dict[str, RawLead] = {}
    for raw in raw_leads:
        existing = merged.get(raw.key)
        if existing is None:
            merged[raw.key] = raw
            continue
        for name in raw.provider_names:
            if name not in existing.provider_names:
                existing.provider_names.append(name)
        for npi in raw.npi_numbers:
            if npi not in existing.npi_numbers:
                existing.npi_numbers.append(npi)
        for spec in raw.specialty:
            if spec not in existing.specialty:
                existing.specialty.append(spec)
        existing.phone = existing.phone or raw.phone
        existing.website = existing.website or raw.website
    return list(merged.values())


def _matches_query(raw: RawLead, query: LeadQuery) -> bool:
    if is_excluded(raw.organization_name, query.exclude_keywords):
        return False
    if query.state and raw.state and raw.state != normalize_state(query.state):
        return False
    if query.city and raw.city and raw.city.lower() != (normalize_city(query.city) or "").lower():
        return False
    if query.specialties:
        haystack = " ".join(raw.specialty).lower()
        if not any(spec.lower() in haystack for spec in query.specialties):
            return False
    return True


class NppesSource:
    """Live NPI Registry lookup (free, rate-limited by CMS)."""

    name = "nppes"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    def search(self, query: LeadQuery) -> list[RawLead]:
        settings = get_settings()
        client = self._client or httpx.Client(
            timeout=settings.request_timeout, headers={"User-Agent": settings.user_agent}
        )
        collected: list[RawLead] = []
        try:
            specialties = query.specialties or [""]
            for specialty in specialties:
                skip = 0
                while len(collected) < query.limit * 3:
                    params: dict[str, Any] = {
                        "version": NPPES_VERSION,
                        "limit": NPPES_PAGE_SIZE,
                        "skip": skip,
                    }
                    if specialty:
                        params["taxonomy_description"] = specialty
                    if query.city:
                        params["city"] = query.city
                    if query.state:
                        params["state"] = normalize_state(query.state)
                    if query.postal_code:
                        params["postal_code"] = query.postal_code
                    response = client.get(NPPES_ENDPOINT, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    results = payload.get("results") or []
                    for result in results:
                        raw = parse_nppes_result(result)
                        if raw and _matches_query(raw, query):
                            collected.append(raw)
                    if len(results) < NPPES_PAGE_SIZE:
                        break
                    skip += NPPES_PAGE_SIZE
                    if skip >= 1000:  # CMS caps deep pagination
                        break
        finally:
            if self._client is None:
                client.close()
        return merge_raw_leads(collected)[: query.limit]


class FixtureSource:
    """Replays a saved NPPES payload - offline discovery for demos and tests."""

    name = "fixture"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def search(self, query: LeadQuery) -> list[RawLead]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        results = payload.get("results", payload if isinstance(payload, list) else [])
        website_hints = payload.get("website_hints", {}) if isinstance(payload, dict) else {}
        raws: list[RawLead] = []
        for result in results:
            raw = parse_nppes_result(result)
            if not raw:
                continue
            raw.source = "fixture"
            hint = website_hints.get(str(result.get("number")))
            if hint:
                raw.website = _resolve_fixture_website(hint, self.path)
            if _matches_query(raw, query):
                raws.append(raw)
        return merge_raw_leads(raws)[: query.limit]


def _resolve_fixture_website(hint: str, fixture_path: Path) -> str:
    """Fixture sites may be given as paths relative to the fixture file."""
    if hint.startswith(("http://", "https://", "file://")):
        return hint
    return (fixture_path.parent / hint).resolve().as_uri()


class CsvSource:
    """Bring-your-own-leads: organization_name,website,city,state,... CSV."""

    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def search(self, query: LeadQuery) -> list[RawLead]:
        raws: list[RawLead] = []
        with self.path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("organization_name") or row.get("name") or "").strip()
                if not name:
                    continue
                raw = RawLead(
                    organization_name=name,
                    website=(row.get("website") or "").strip() or None,
                    city=normalize_city(row.get("city")),
                    state=normalize_state(row.get("state")),
                    phone=normalize_phone(row.get("phone")),
                    specialty=[s.strip() for s in (row.get("specialty") or "").split(";") if s.strip()],
                    emails=[e.strip() for e in (row.get("email") or "").split(";") if e.strip()],
                    source="csv",
                )
                if _matches_query(raw, query):
                    raws.append(raw)
        return merge_raw_leads(raws)[: query.limit]


def build_source(campaign: CampaignConfig) -> LeadSource:
    source = (campaign.source or "nppes").lower()
    if source == "fixture":
        if not campaign.fixture_path:
            raise ValueError("campaign.source=fixture requires fixture_path")
        return FixtureSource(campaign.fixture_path)
    if source == "csv":
        if not campaign.csv_path:
            raise ValueError("campaign.source=csv requires csv_path")
        return CsvSource(campaign.csv_path)
    if source == "nppes":
        return NppesSource()
    raise ValueError(f"unknown lead source: {campaign.source}")
