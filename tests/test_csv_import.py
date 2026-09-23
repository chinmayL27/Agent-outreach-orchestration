"""Importing a bring-your-own-leads CSV, and crawling onward from it.

The cases that matter are the ones a real export produces: capitalised and
spaced headers, rows whose only identifier is a work email, and a campaign
whose specialty/geography filters must not re-filter a hand-picked list.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.extraction.csv_source import CsvSource, build_header_map, normalize_header
from app.extraction.nppes import LeadQuery
from app.models.evidence import Evidence
from app.models.lead import Lead
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus
from tests.conftest import SITES


def write_csv(tmp_path, text: str, name: str = "leads.csv"):
    path = tmp_path / name
    path.write_text(text.lstrip(), encoding="utf-8")
    return path


def query(**kwargs) -> LeadQuery:
    params = {"specialties": [], "limit": 50, "exclude_keywords": []}
    params.update(kwargs)
    return LeadQuery(**params)


# ---------------------------------------------------------------------------
# Header handling
# ---------------------------------------------------------------------------
def test_header_matching_ignores_case_spacing_and_punctuation():
    assert normalize_header(" E-mail_Address ") == "e mail address"
    mapping, unmapped = build_header_map(
        ["Practice Name", "E-mail Address", "Website URL", "ZIP", "Notes"]
    )
    assert mapping == {
        "Practice Name": "organization_name",
        "E-mail Address": "email",
        "Website URL": "website",
        "ZIP": "postal_code",
    }
    assert unmapped == ["Notes"]  # reported rather than silently dropped


def test_a_more_specific_name_column_wins_over_a_generic_one():
    mapping, _ = build_header_map(["Practice Name", "Name"])
    assert mapping == {"Practice Name": "organization_name"}


def test_a_file_with_no_recognizable_columns_fails_loudly(tmp_path):
    path = write_csv(tmp_path, "foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="no recognizable columns"):
        CsvSource(path).search(query())


# ---------------------------------------------------------------------------
# Rows -> leads
# ---------------------------------------------------------------------------
def test_a_row_with_only_an_email_becomes_a_crawlable_lead(tmp_path):
    path = write_csv(tmp_path, """
Email,City,State
frontdesk@cedar-peds-clinic.example,Austin,tx
""")
    source = CsvSource(path)
    (lead,) = source.search(query())

    assert lead.emails == ["frontdesk@cedar-peds-clinic.example"]
    assert lead.website == "cedar-peds-clinic.example"  # the domain is the site to crawl
    assert lead.organization_name == "Cedar Peds Clinic"  # provisional, from the domain
    assert lead.provisional_name is True
    assert (lead.city, lead.state) == ("Austin", "TX")
    assert source.report.website_from_email == 1


def test_a_free_mailbox_domain_is_never_treated_as_the_practice_website(tmp_path):
    path = write_csv(tmp_path, """
Company,Email
Cedar Peds,drsmith@gmail.com
""")
    (lead,) = CsvSource(path).search(query())
    assert lead.website is None  # gmail.com is not this clinic's site
    assert lead.emails == ["drsmith@gmail.com"]


def test_campaign_search_filters_do_not_refilter_a_hand_picked_list(tmp_path):
    """The operator already chose these rows; specialty/geography must not cut them."""
    path = write_csv(tmp_path, """
Practice Name,Email,City,State
Lakeside Dermatology,info@lakesidederm.example,Austin,TX
""")
    leads = CsvSource(path).search(
        query(specialties=["Podiatry"], city="San Jose", state="CA")
    )
    assert [lead.organization_name for lead in leads] == ["Lakeside Dermatology"]


def test_exclusion_policy_still_applies(tmp_path):
    path = write_csv(tmp_path, """
Practice Name,Email
Regional Hospital Group,info@regionalhospital.example
Lakeside Dermatology,info@lakesidederm.example
""")
    source = CsvSource(path)
    leads = source.search(query(exclude_keywords=["hospital"]))
    assert [lead.organization_name for lead in leads] == ["Lakeside Dermatology"]
    assert source.report.skipped["matched an exclude keyword"] == 1


def test_unusable_rows_are_skipped_with_a_counted_reason(tmp_path):
    path = write_csv(tmp_path, """
Practice Name,Email,Website
,,
,not-an-email,
""")
    source = CsvSource(path)
    assert source.search(query()) == []
    assert source.report.rows == 2
    assert source.report.skipped_total == 2
    assert "no name, website or usable email" in source.report.skipped
    assert any("skipped" in note for note in source.report.notes())


def test_multi_value_cells_split_and_rows_for_one_practice_merge(tmp_path):
    path = write_csv(tmp_path, """
Practice Name,Website,Email,Specialties,Services,Provider
Lakeside Dermatology,https://lakesidederm.example,info@lakesidederm.example,"Dermatology, Cosmetic",Mohs surgery;Acne care,"Smith, Jane"
Lakeside Dermatology,https://lakesidederm.example,billing@lakesidederm.example,,Skin checks,
""")
    (lead,) = CsvSource(path).search(query())

    assert lead.emails == ["info@lakesidederm.example", "billing@lakesidederm.example"]
    assert lead.specialty == ["Dermatology", "Cosmetic"]  # comma-split is safe here
    assert lead.services == ["Mohs surgery", "Acne care", "Skin checks"]
    assert lead.provider_names == ["Smith, Jane"]  # a person's name is never comma-split


# ---------------------------------------------------------------------------
# Import -> crawl -> services
# ---------------------------------------------------------------------------
def _site(name: str) -> str:
    return (SITES / name / "index.html").resolve().as_uri()


def test_import_then_enrich_extracts_services_and_names_the_clinic(session, campaign, settings, tmp_path):
    """The whole point: a row with an email and a site becomes an enriched lead."""
    path = write_csv(tmp_path, f"""
Email,Website URL
info@abcdermatology.example,{_site("abc-dermatology")}
""")
    campaign.source = "csv"
    campaign.csv_path = str(path)

    discovered = pipeline.discover(session, campaign)
    session.flush()
    assert discovered.succeeded == 1

    lead = session.execute(select(Lead)).scalars().one()
    assert lead.source == "csv"
    # Named from the email domain until the site says otherwise.
    assert lead.organization_name == "Abcdermatology"

    enriched = pipeline.enrich(session, campaign, settings=settings)
    session.flush()
    assert enriched.succeeded == 1

    lead = session.execute(select(Lead)).scalars().one()
    assert lead.lead_status in {LeadStatus.ENRICHED, LeadStatus.ENRICHMENT_PARTIAL}
    assert lead.services, "services should be extracted from the crawled site"
    assert lead.organization_name == "ABC Dermatology, P.C."  # the site's own name

    # Crawled services cite the page they came from, not the import file.
    service_evidence = [
        row
        for row in session.execute(select(Evidence).where(Evidence.lead_id == lead.id)).scalars()
        if row.attribute == "service"
    ]
    assert service_evidence
    assert all(row.source_url.startswith("file://") for row in service_evidence)


def test_facts_asserted_in_the_file_cite_the_file_not_a_page(session, campaign, tmp_path):
    path = write_csv(tmp_path, """
Practice Name,Email,Services
Lakeside Dermatology,info@lakesidederm.example,Mohs surgery;Acne care
""")
    campaign.source = "csv"
    campaign.csv_path = str(path)
    pipeline.discover(session, campaign)
    session.flush()

    evidence = session.execute(select(Evidence)).scalars().all()
    services = sorted(e.value for e in evidence if e.attribute == "service")
    assert services == ["Acne care", "Mohs surgery"]
    assert {e.source_url for e in evidence} == {"csv:leads.csv"}  # the file, not a URL
    assert all(e.page_title == "operator-supplied import" for e in evidence)


def test_reimporting_the_same_list_adds_no_duplicates_and_backfills_contacts(session, campaign, tmp_path):
    first = write_csv(tmp_path, """
Practice Name,Website
Lakeside Dermatology,https://lakesidederm.example
""", name="leads.csv")
    campaign.source = "csv"
    campaign.csv_path = str(first)
    pipeline.discover(session, campaign)
    session.flush()

    # Same practice, now with an email filled in.
    second = write_csv(tmp_path, """
Practice Name,Website,Email
Lakeside Dermatology,https://lakesidederm.example,info@lakesidederm.example
""", name="leads2.csv")
    campaign.csv_path = str(second)
    again = pipeline.discover(session, campaign)
    session.flush()

    assert again.succeeded == 0 and again.skipped == 1
    lead = session.execute(select(Lead)).scalars().one()
    assert lead.emails == ["info@lakesidederm.example"]
