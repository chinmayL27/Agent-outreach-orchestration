"""Polite, bounded fetching with destination restrictions.

Website content is untrusted and clinic URLs are attacker-influenced, so every
destination - including each redirect hop - is re-validated against a public
HTTP(S) allowlist before a request is made.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import url2pathname

import httpx

from app.config import Settings, get_settings

MAX_REDIRECTS = 5
#: One retry for transport failures and 5xx; 4xx is a definitive answer.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class BlockedDestination(RuntimeError):
    """The URL resolves somewhere the crawler is not allowed to go."""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    ok: bool
    error: str | None = None
    attempts: int = 1


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    return urlunparse(parsed._replace(fragment=""))


def _ip_is_public(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local  # includes 169.254.169.254 cloud metadata
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def assert_public_http_url(url: str, *, resolver=socket.getaddrinfo) -> None:
    """Allow only http(s) URLs that resolve exclusively to public addresses.

    Raises BlockedDestination for private, loopback, link-local, reserved and
    metadata addresses - checked after DNS resolution, so a hostname that
    points at 127.0.0.1 or 169.254.169.254 is rejected too.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BlockedDestination(f"scheme {parsed.scheme or '(none)'!r} is not allowed")
    host = parsed.hostname
    if not host:
        raise BlockedDestination("URL has no host")
    try:
        infos = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise BlockedDestination(f"could not resolve {host}: {exc}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise BlockedDestination(f"{host} resolved to no addresses")
    for address in addresses:
        if not _ip_is_public(address):
            raise BlockedDestination(f"{host} resolves to non-public address {address}")


def same_site(base: str, candidate: str) -> bool:
    base_parsed, cand_parsed = urlparse(base), urlparse(candidate)
    if base_parsed.scheme == "file" or cand_parsed.scheme == "file":
        base_dir = Path(url2pathname(base_parsed.path)).parent
        candidate_path = Path(url2pathname(cand_parsed.path))
        try:
            return candidate_path.parent == base_dir or base_dir in candidate_path.resolve().parents
        except OSError:  # pragma: no cover - defensive
            return False
    base_host = base_parsed.netloc.lower().removeprefix("www.")
    cand_host = cand_parsed.netloc.lower().removeprefix("www.")
    return bool(cand_host) and cand_host == base_host


class Fetcher:
    """Rate-limited, robots-aware fetcher with a hard page budget."""

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0

    # -- lifecycle ----------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.request_timeout,
                # Redirects are followed manually so each hop can be re-validated.
                follow_redirects=False,
                headers={"User-Agent": self.settings.user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- robots -------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return True
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                response = self.client.get(urljoin(root, "/robots.txt"))
                if response.status_code == 200 and parser is not None:
                    parser.parse(response.text.splitlines())
                else:
                    parser = None
            except httpx.HTTPError:
                parser = None
            self._robots[root] = parser
        parser = self._robots[root]
        return True if parser is None else parser.can_fetch(self.settings.user_agent, url)

    # -- fetching -----------------------------------------------------------
    def get(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            if not self.settings.allow_file_urls:
                return FetchResult(
                    url=url, status=0, text="", ok=False,
                    error="file:// URLs are disabled (set ALLOW_FILE_URLS=true for the offline demo)",
                )
            return self._get_file(url, parsed.path)

        # One retry for transport errors and transient status codes (TDD s20).
        result = self._get_http(url)
        if not result.ok and self._is_retryable(result):
            time.sleep(min(self.settings.crawl_delay, 1.0))
            retried = self._get_http(url)
            retried.attempts = 2
            return retried
        return result

    @staticmethod
    def _is_retryable(result: FetchResult) -> bool:
        if result.status in RETRYABLE_STATUS:
            return True
        # Transport-level failure (no HTTP status) - but never a blocked destination.
        return result.status == 0 and not (result.error or "").startswith("blocked:")

    def _get_http(self, url: str) -> FetchResult:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                assert_public_http_url(current)
            except BlockedDestination as exc:
                return FetchResult(url=current, status=0, text="", ok=False, error=f"blocked: {exc}")
            if not self.allowed(current):
                return FetchResult(
                    url=current, status=0, text="", ok=False, error="blocked: robots.txt"
                )
            self._throttle()
            try:
                response = self.client.get(current)
            except httpx.HTTPError as exc:
                return FetchResult(url=current, status=0, text="", ok=False, error=str(exc))

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return FetchResult(
                        url=current, status=response.status_code, text="", ok=False,
                        error="redirect without a location header",
                    )
                current = normalize_url(urljoin(current, location))
                continue

            content_type = response.headers.get("content-type", "")
            if content_type and "html" not in content_type and "xml" not in content_type:
                return FetchResult(
                    url=current, status=response.status_code, text="", ok=False,
                    error=f"unsupported content-type {content_type}",
                )
            return FetchResult(
                url=current,
                status=response.status_code,
                text=response.text,
                ok=response.status_code == 200,
                error=None if response.status_code == 200 else f"HTTP {response.status_code}",
            )
        return FetchResult(url=current, status=0, text="", ok=False, error="too many redirects")

    def _get_file(self, url: str, path: str) -> FetchResult:
        file_path = Path(url2pathname(path))
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.exists():
            return FetchResult(url=url, status=404, text="", ok=False, error="file not found")
        return FetchResult(
            url=url, status=200, ok=True,
            text=file_path.read_text(encoding="utf-8", errors="replace"),
        )

    def _throttle(self) -> None:
        delay = self.settings.crawl_delay
        if delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()
