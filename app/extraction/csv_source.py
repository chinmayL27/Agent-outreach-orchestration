"""Bring-your-own-leads: import a CSV of emails and basic practice information.

This is the "I already have a list" front door.  The minimum a row needs is a
way to reach a website - either a `website` column or a work email whose domain
is the practice's own - because enrichment is what turns a row into a lead
worth contacting.  Everything after import is the normal pipeline: crawl the
site, extract services with their source URLs, score, personalize, review.

Two deliberate differences from a registry search:

* **The file is the target list.**  Campaign *search* filters (specialty, city,
  state) narrow a query to the registry; they do not re-filter a list the
  operator chose by hand.  `exclude_keywords` still applies, because that is a
  do-not-contact policy rather than a search filter.
* **Columns are asserted, not observed.**  Services or emails supplied in the
  file are recorded with the file as their source, never as if a crawl had
  found them, so a reviewer can tell the two apart.

Headers are matched loosely: case, spacing and punctuation are ignored, so
`Email`, `E-mail Address` and `email_address` all land on the same field.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from app.extraction.email import is_valid_email
from app.extraction.normalize import (
    domain_of,
    domain_of_email,
    is_excluded,
    name_from_domain,
    normalize_city,
    normalize_phone,
    normalize_state,
)
from app.extraction.nppes import LeadQuery, RawLead, merge_raw_leads
from app.util.text import title_case_name

#: Canonical field -> header spellings we accept for it.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "organization_name": (
        "organization name", "organisation name", "organization", "organisation",
        "company", "company name", "practice", "practice name", "clinic",
        "clinic name", "business name", "account name", "name",
    ),
    "website": ("website", "website url", "url", "web site", "site", "site url", "homepage", "web address", "domain"),
    "email": ("email", "emails", "email address", "e mail", "e mail address", "contact email", "work email", "public email"),
    "city": ("city", "town", "locality"),
    "state": ("state", "province", "region"),
    "postal_code": ("postal code", "postcode", "post code", "zip", "zip code", "zipcode"),
    "address": ("address", "street", "street address", "address 1", "address line 1", "mailing address"),
    "phone": ("phone", "phone number", "telephone", "telephone number", "tel", "office phone", "contact number"),
    "specialty": ("specialty", "specialties", "speciality", "specialities", "taxonomy", "category"),
    "services": ("services", "service", "service lines", "offerings", "procedures"),
    "provider_names": (
        "provider", "providers", "provider name", "provider names", "doctor", "doctors",
        "physician", "physicians", "contact", "contact name", "owner",
    ),
    "npi": ("npi", "npi number", "npi numbers"),
}

#: Fields whose value may hold several entries in one cell.
LIST_FIELDS = frozenset({"email", "specialty", "services", "provider_names", "npi"})
#: Splitting on a comma is safe for machine-ish values, not for people's names
#: ("Smith, John") or free-text service descriptions.
COMMA_SPLIT_FIELDS = frozenset({"email", "npi", "specialty"})

SKIP_NO_CONTACT = "no name, website or usable email"
SKIP_EXCLUDED = "matched an exclude keyword"
SKIP_EMPTY_ROW = "blank row"


def normalize_header(header: str | None) -> str:
    """`"E-mail Address "` -> `"e mail address"`."""
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in (header or ""))
    return " ".join(cleaned.split())


def build_header_map(fieldnames: Iterable[str | None]) -> tuple[dict[str, str], list[str]]:
    """Map each CSV header onto a canonical field; report the ones we ignore."""
    lookup = {alias: field_name for field_name, aliases in HEADER_ALIASES.items() for alias in aliases}
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for header in fieldnames or []:
        if header is None:
            continue
        canonical = lookup.get(normalize_header(header))
        # First header wins, so "Practice Name" is not clobbered by a later "Name".
        if canonical and canonical not in mapping.values():
            mapping[header] = canonical
        elif canonical is None:
            unmapped.append(header)
    return mapping, unmapped


def split_values(value: str, *, allow_comma: bool) -> list[str]:
    separators = [";", "|", "\n", "\r"] + ([","] if allow_comma else [])
    parts = [value]
    for separator in separators:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


@dataclass
class CsvImportReport:
    """What the file contained and what happened to each row."""

    path: str = ""
    rows: int = 0
    imported: int = 0
    merged: int = 0
    with_website: int = 0
    website_from_email: int = 0
    provisional_names: int = 0
    skipped: Counter = field(default_factory=Counter)
    unmapped_headers: list[str] = field(default_factory=list)
    mapped_fields: list[str] = field(default_factory=list)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    def notes(self) -> list[str]:
        """Short lines for the CLI stage summary."""
        lines = [
            f"{self.rows} row(s) read, {self.imported} lead(s) after merge, "
            f"{self.with_website} with a crawlable website"
        ]
        if self.website_from_email:
            lines.append(f"{self.website_from_email} website(s) derived from an email domain")
        if self.provisional_names:
            lines.append(
                f"{self.provisional_names} lead(s) named from their domain until enrichment "
                "reads the site's own name"
            )
        for reason, count in self.skipped.most_common():
            lines.append(f"{count} row(s) skipped: {reason}")
        if self.unmapped_headers:
            lines.append("ignored column(s): " + ", ".join(self.unmapped_headers[:8]))
        return lines


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read the file, tolerating an Excel BOM and non-UTF-8 exports."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    reader = csv.DictReader(text.splitlines())
    return list(reader), list(reader.fieldnames or [])


class CsvSource:
    """A CSV of emails and basic practice information, as a `LeadSource`."""

    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.report = CsvImportReport(path=self.path.name)

    # -- parsing ------------------------------------------------------------
    def _row_values(self, row: dict[str, str], header_map: dict[str, str]) -> dict[str, list[str]]:
        values: dict[str, list[str]] = {}
        for header, canonical in header_map.items():
            raw = (row.get(header) or "").strip()
            if not raw:
                continue
            if canonical in LIST_FIELDS:
                parts = split_values(raw, allow_comma=canonical in COMMA_SPLIT_FIELDS)
            else:
                parts = [raw]
            if parts:
                values.setdefault(canonical, []).extend(parts)
        return values

    def _to_raw_lead(self, values: dict[str, list[str]], query: LeadQuery) -> tuple[RawLead | None, str | None]:
        """One parsed row -> a RawLead, or a reason it was skipped."""
        if not values:
            return None, SKIP_EMPTY_ROW

        emails = [address.lower() for address in values.get("email", []) if is_valid_email(address)]
        website = next(iter(values.get("website", [])), None)
        name = next(iter(values.get("organization_name", [])), "")

        # An email on its own is enough: its domain is the site to crawl.
        email_domain = next((domain_of_email(address) for address in emails if domain_of_email(address)), None)
        website_from_email = False
        if not website and email_domain:
            website = email_domain
            website_from_email = True

        provisional = False
        if not name:
            name = name_from_domain(domain_of(website) or email_domain) or ""
            provisional = bool(name)
        if not name:
            return None, SKIP_NO_CONTACT

        display_name = name if not provisional else title_case_name(name)
        if is_excluded(display_name, query.exclude_keywords):
            return None, SKIP_EXCLUDED

        raw = RawLead(
            organization_name=display_name,
            website=website,
            city=normalize_city(next(iter(values.get("city", [])), None)),
            state=normalize_state(next(iter(values.get("state", [])), None)),
            postal_code=(next(iter(values.get("postal_code", [])), "") or "")[:5] or None,
            address=next(iter(values.get("address", [])), None),
            phone=normalize_phone(next(iter(values.get("phone", [])), None)),
            specialty=values.get("specialty", []),
            services=values.get("services", []),
            provider_names=values.get("provider_names", []),
            npi_numbers=values.get("npi", []),
            emails=emails,
            source="csv",
            evidence_source=f"csv:{self.path.name}",
            provisional_name=provisional,
        )
        self.report.website_from_email += int(website_from_email)
        self.report.provisional_names += int(provisional)
        return raw, None

    # -- LeadSource ---------------------------------------------------------
    def search(self, query: LeadQuery) -> list[RawLead]:
        if not self.path.exists():
            raise FileNotFoundError(f"csv_path does not exist: {self.path}")
        rows, fieldnames = _read_rows(self.path)
        header_map, unmapped = build_header_map(fieldnames)

        self.report = CsvImportReport(
            path=self.path.name,
            rows=len(rows),
            unmapped_headers=unmapped,
            mapped_fields=sorted(set(header_map.values())),
        )
        if not header_map:
            raise ValueError(
                f"{self.path.name}: no recognizable columns in "
                f"{', '.join(fieldnames) or '(no header row)'}. "
                "Expected at least one of: organization_name, website, email."
            )

        raws: list[RawLead] = []
        for row in rows:
            raw, reason = self._to_raw_lead(self._row_values(row, header_map), query)
            if raw is None:
                self.report.skipped[reason or SKIP_EMPTY_ROW] += 1
                continue
            raws.append(raw)

        merged = merge_raw_leads(raws)[: query.limit]
        self.report.imported = len(merged)
        self.report.merged = max(0, len(raws) - len(merge_raw_leads(raws)))
        self.report.with_website = sum(1 for raw in merged if raw.website)
        return merged


def preview(path: str | Path, query: LeadQuery) -> tuple[list[RawLead], CsvImportReport]:
    """Parse a file without touching the database (used by `--dry-run`)."""
    source = CsvSource(path)
    leads = source.search(query)
    return leads, source.report


def iter_leads(path: str | Path, query: LeadQuery) -> Iterator[RawLead]:
    yield from CsvSource(path).search(query)
