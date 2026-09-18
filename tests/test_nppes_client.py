"""NPPES HTTP client behaviour, exercised against a mock transport.

The live registry is not called from tests: CMS rate-limits it and CI must be
hermetic.
"""

from __future__ import annotations

import json

import httpx

from app.extraction.nppes import NPPES_PAGE_SIZE, LeadQuery, NppesSource
from tests.conftest import FIXTURES


def _payload(results):
    return {"result_count": len(results), "results": results}


def test_client_sends_the_expected_query_and_parses_results(settings):
    seen: list[httpx.QueryParams] = []
    results = json.loads((FIXTURES / "nppes_sample.json").read_text())["results"]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json=_payload(results))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    leads = NppesSource(client).search(
        LeadQuery(
            specialties=["Dermatology"],
            city="San Jose",
            state="ca",
            limit=10,
            exclude_keywords=["hospital"],
        )
    )

    assert seen[0]["taxonomy_description"] == "Dermatology"
    assert seen[0]["city"] == "San Jose"
    assert seen[0]["state"] == "CA"  # normalized before it leaves the client
    assert seen[0]["version"] == "2.1"

    names = [lead.organization_name for lead in leads]
    assert names == ["ABC Dermatology, P.C."]  # hospital excluded, individual merged
    assert leads[0].npi_numbers == ["1234567893", "1093847561"]


def test_client_stops_paginating_on_a_short_page(settings):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=_payload([]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    leads = NppesSource(client).search(LeadQuery(specialties=["Dermatology"], limit=50))
    assert leads == []
    assert calls["count"] == 1


def test_client_follows_pagination_until_the_limit(settings):
    page = json.loads((FIXTURES / "nppes_sample.json").read_text())["results"][:1]
    pages = [page * NPPES_PAGE_SIZE, page]
    skips: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        skips.append(request.url.params.get("skip"))
        return httpx.Response(200, json=_payload(pages[min(len(skips) - 1, 1)]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # A full page means there may be more; a short page ends the walk.
    NppesSource(client).search(LeadQuery(specialties=["Dermatology"], limit=300))
    assert skips == ["0", str(NPPES_PAGE_SIZE)]
