"""Crawler destination restrictions and retry behaviour (TDD s9, s20, s23)."""

from __future__ import annotations

import httpx
import pytest

from app.util.http import BlockedDestination, Fetcher, assert_public_http_url


def fake_resolver(mapping: dict[str, str]):
    def resolve(host, port):
        if host not in mapping:
            raise OSError(f"unknown host {host}")
        return [(2, 1, 6, "", (mapping[host], port))]

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.1.2.3/internal",
        "http://192.168.0.10/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
    ],
)
def test_non_public_destinations_are_refused(url):
    with pytest.raises(BlockedDestination):
        assert_public_http_url(url)


def test_public_destination_is_allowed():
    assert_public_http_url("https://clinic.example/", resolver=fake_resolver({"clinic.example": "93.184.216.34"}))


def test_hostname_pointing_at_a_private_address_is_refused():
    """DNS rebinding: the name looks fine, the address does not."""
    with pytest.raises(BlockedDestination) as excinfo:
        assert_public_http_url(
            "https://sneaky.example/", resolver=fake_resolver({"sneaky.example": "169.254.169.254"})
        )
    assert "non-public address" in str(excinfo.value)


def test_redirect_into_a_private_address_is_not_followed(settings, monkeypatch):
    """Every hop is re-validated, not just the first (TDD s9)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "clinic.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="<html>secrets</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr("app.util.http.assert_public_http_url", _allow_only("clinic.example"))

    with Fetcher(settings, client) as fetcher:
        result = fetcher.get("https://clinic.example/")

    assert not result.ok
    assert "blocked" in (result.error or "")
    assert not any("169.254" in url for url in seen), "the metadata endpoint was never requested"


def _allow_only(allowed_host: str):
    from urllib.parse import urlparse

    def checker(url: str, **_kwargs) -> None:
        if urlparse(url).hostname != allowed_host:
            raise BlockedDestination(f"{url} is not allowed")

    return checker


def test_transport_failure_is_retried_once(settings, monkeypatch):
    """TDD s20: crawler retries once, then preserves the lead."""
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr("app.util.http.assert_public_http_url", lambda url, **kw: None)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with Fetcher(settings, client) as fetcher:
        result = fetcher.get("https://clinic.example/")

    assert not result.ok
    assert len(attempts) == 2, "exactly one retry, not an infinite loop"
    assert result.attempts == 2


def test_server_error_is_retried_but_client_error_is_not(settings, monkeypatch):
    calls = {"500": 0, "404": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/boom":
            calls["500"] += 1
            return httpx.Response(503, text="")
        calls["404"] += 1
        return httpx.Response(404, text="")

    monkeypatch.setattr("app.util.http.assert_public_http_url", lambda url, **kw: None)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with Fetcher(settings, client) as fetcher:
        fetcher.get("https://clinic.example/boom")
        fetcher.get("https://clinic.example/missing")

    assert calls["500"] == 2  # transient: retried
    assert calls["404"] == 1  # definitive: accepted as the answer


def test_blocked_destination_is_never_retried(settings, monkeypatch):
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        attempts.append(str(request.url))
        return httpx.Response(200, text="")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with Fetcher(settings, client) as fetcher:
        result = fetcher.get("http://127.0.0.1/")

    assert not result.ok
    assert attempts == []


def test_file_urls_require_an_explicit_opt_in(settings, monkeypatch, tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html><h1>local</h1></html>", encoding="utf-8")

    with Fetcher(settings) as fetcher:  # conftest enables file URLs for fixtures
        assert fetcher.get(page.as_uri()).ok

    monkeypatch.setenv("ALLOW_FILE_URLS", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with Fetcher(get_settings()) as fetcher:
            result = fetcher.get(page.as_uri())
        assert not result.ok
        assert "disabled" in (result.error or "")
    finally:
        get_settings.cache_clear()
