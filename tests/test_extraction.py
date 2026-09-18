"""Unit tests: NPPES normalization, dedupe, website discovery."""

from __future__ import annotations

import json

from app.extraction.nppes import (
    FixtureSource,
    LeadQuery,
    merge_raw_leads,
    parse_nppes_result,
)
from app.extraction.normalize import (
    dedupe_key,
    domain_of,
    is_excluded,
    merge_provider_names,
    normalize_name,
    normalize_phone,
)
from app.extraction.website import candidate_links, resolve_website
from tests.conftest import FIXTURES

ORG_RESULT = {
    "enumeration_type": "NPI-2",
    "number": "1234567893",
    "basic": {
        "organization_name": "ABC DERMATOLOGY, P.C.",
        "authorized_official_first_name": "Jane",
        "authorized_official_last_name": "Smith",
    },
    "addresses": [
        {
            "address_purpose": "MAILING",
            "address_1": "PO Box 1",
            "city": "SAN JOSE",
            "state": "CA",
            "postal_code": "95125",
        },
        {
            "address_purpose": "LOCATION",
            "address_1": "1200 Meridian Ave",
            "city": "SAN JOSE",
            "state": "CA",
            "postal_code": "951251234",
            "telephone_number": "408-555-0142",
        },
    ],
    "taxonomies": [
        {"desc": "Dermatology, Procedural Dermatology", "primary": False},
        {"desc": "Dermatology", "primary": True},
    ],
}


def test_parse_nppes_prefers_location_address_and_primary_taxonomy():
    raw = parse_nppes_result(ORG_RESULT)
    assert raw is not None
    assert raw.organization_name == "ABC Dermatology, P.C."
    assert raw.address == "1200 Meridian Ave"
    assert raw.city == "San Jose" and raw.state == "CA"
    assert raw.postal_code == "95125"
    assert raw.phone == "(408) 555-0142"
    assert raw.specialty[0] == "Dermatology"  # primary taxonomy first
    assert raw.provider_names == ["Jane Smith"]


def test_parse_nppes_individual_falls_back_to_person_name():
    raw = parse_nppes_result(
        {
            "enumeration_type": "NPI-1",
            "number": "1093847561",
            "basic": {"first_name": "ROBERT", "last_name": "CHEN"},
            "addresses": [
                {"address_purpose": "LOCATION", "city": "San Jose", "state": "CA"}
            ],
            "taxonomies": [{"desc": "Dermatology", "primary": True}],
        }
    )
    assert raw is not None
    assert raw.provider_names == ["Robert Chen"]


def test_normalization_strips_legal_suffixes_and_formats_phone():
    assert normalize_name("ABC Dermatology, P.C.") == normalize_name("ABC Dermatology PC")
    assert normalize_name("The ABC Dermatology Group LLC") == "abc dermatology"
    assert normalize_phone("+1 (408) 555-0142") == "(408) 555-0142"
    assert normalize_phone("555-0142") is None
    assert domain_of("https://www.abc.example/x") == "abc.example"


def test_dedupe_key_prefers_domain_then_name_and_place():
    assert dedupe_key("ABC Derm", "San Jose", "CA", "https://www.abc.example") == "domain:abc.example"
    assert dedupe_key("ABC Derm PC", "san jose", "ca") == dedupe_key("ABC Derm", "San Jose", "CA")
    assert dedupe_key("ABC Derm", "Oakland", "CA") != dedupe_key("ABC Derm", "San Jose", "CA")


def test_merge_raw_leads_collapses_individuals_into_the_practice():
    org = parse_nppes_result(ORG_RESULT)
    individual = parse_nppes_result(
        {
            "enumeration_type": "NPI-1",
            "number": "1093847561",
            "basic": {"first_name": "Robert", "last_name": "Chen"},
            "addresses": [{"address_purpose": "LOCATION", "city": "San Jose", "state": "CA"}],
            "taxonomies": [{"desc": "Dermatology", "primary": True}],
            "other_names": [{"organization_name": "ABC Dermatology PC"}],
        }
    )
    merged = merge_raw_leads([org, individual])
    assert len(merged) == 1
    assert merged[0].npi_numbers == ["1234567893", "1093847561"]
    assert merged[0].provider_names == ["Jane Smith", "Robert Chen"]


def test_merge_provider_names_keeps_the_richest_spelling():
    merged = merge_provider_names(["Jane Smith"], ["Jane Smith, MD", "Priya Nair, DO"])
    assert merged == ["Jane Smith, MD", "Priya Nair, DO"]


def test_exclusions_filter_hospitals_and_universities():
    keywords = ["hospital", "university"]
    assert is_excluded("Valley Regional Hospital", keywords)
    assert not is_excluded("ABC Dermatology", keywords)


def test_fixture_source_applies_query_filters_and_resolves_local_sites():
    source = FixtureSource(FIXTURES / "nppes_sample.json")
    results = source.search(
        LeadQuery(specialties=["Dermatology"], state="CA", limit=10, exclude_keywords=["hospital"])
    )
    names = [r.organization_name for r in results]

    assert "ABC Dermatology, P.C." in names
    assert all("Dermatology" in " ".join(r.specialty) for r in results)
    assert not any("Hospital" in name for name in names)
    # Pediatrics/dental/urgent-care records are in the payload but off-query.
    assert not any("Pediatrics" in name for name in names)

    abc = next(r for r in results if r.organization_name == "ABC Dermatology, P.C.")
    assert abc.website.startswith("file://")
    assert abc.npi_numbers == ["1234567893", "1093847561"]  # individual merged in


def test_fixture_payload_covers_the_awkward_cases():
    """The offline fixture must exercise more than the happy path."""
    payload = json.loads((FIXTURES / "nppes_sample.json").read_text())
    names = [
        r["basic"].get("organization_name", "") for r in payload["results"]
    ]
    assert len(payload["results"]) >= 20
    assert any("HOSPITAL" in n for n in names)  # excluded by campaign rules
    assert any("UNIVERSITY" in n for n in names)
    assert any(r["enumeration_type"] == "NPI-1" for r in payload["results"])  # merge case
    # Most practices have no resolvable website, exactly like the real registry.
    assert len(payload["website_hints"]) < len(payload["results"]) / 2


def test_resolve_website_uses_campaign_map(campaign):
    campaign.website_map = {"ABC Dermatology PC": "abcderm.example"}
    assert resolve_website("ABC Dermatology, P.C.", campaign) == "https://abcderm.example"
    assert resolve_website("Unknown Clinic", campaign) is None


def test_resolve_website_ignores_free_mailbox_domains(campaign):
    assert resolve_website("Some Clinic", campaign, emails=["clinic@gmail.com"]) is None
    assert resolve_website("Some Clinic", campaign, emails=["info@clinic.example"]) == (
        "https://clinic.example"
    )


def test_candidate_links_prioritizes_clinic_information_pages():
    html = """
    <a href="/privacy">Privacy</a>
    <a href="/services">Services</a>
    <a href="/our-providers">Providers</a>
    <a href="mailto:info@x.example">Email</a>
    """
    links = candidate_links("https://x.example/", html)
    assert links[0].endswith("/services")
    assert any(link.endswith("/our-providers") for link in links)
    assert not any("privacy" in link or "mailto" in link for link in links)
