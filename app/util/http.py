"""Polite fetching.

Supports http(s) through httpx and file:// so the whole pipeline can be
exercised offline against the bundled fixture websites.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import url2pathname

import httpx

from app.config import Settings, get_settings


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    ok: bool
    error: str | None = None


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    cleaned = parsed._replace(fragment="")
    return urlunparse(cleaned)


def same_site(base: str, candidate: str) -> bool:
    base_parsed, cand_parsed = urlparse(base), urlparse(candidate)
    if base_parsed.scheme == "file" or cand_parsed.scheme == "file":
        base_dir = Path(url2pathname(base_parsed.path)).parent
        try:
            return base_dir in Path(url2pathname(cand_parsed.path)).resolve().parents or (
                Path(url2pathname(cand_parsed.path)).parent == base_dir
            )
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
                follow_redirects=True,
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
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self.client.get(urljoin(root, "/robots.txt"))
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser = None  # type: ignore[assignment]
            except httpx.HTTPError:
                parser = None  # type: ignore[assignment]
            self._robots[root] = parser
        parser = self._robots[root]
        if parser is None:
            return True
        return parser.can_fetch(self.settings.user_agent, url)

    # -- fetching -----------------------------------------------------------
    def get(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            return self._get_file(url, parsed.path)
        if not self.allowed(url):
            return FetchResult(url=url, status=0, text="", ok=False, error="blocked by robots.txt")
        self._throttle()
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            return FetchResult(url=url, status=0, text="", ok=False, error=str(exc))
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "xml" not in content_type and content_type:
            return FetchResult(
                url=str(response.url),
                status=response.status_code,
                text="",
                ok=False,
                error=f"unsupported content-type {content_type}",
            )
        return FetchResult(
            url=str(response.url),
            status=response.status_code,
            text=response.text,
            ok=response.status_code == 200,
            error=None if response.status_code == 200 else f"HTTP {response.status_code}",
        )

    def _get_file(self, url: str, path: str) -> FetchResult:
        file_path = Path(url2pathname(path))
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.exists():
            return FetchResult(url=url, status=404, text="", ok=False, error="file not found")
        return FetchResult(
            url=url, status=200, text=file_path.read_text(encoding="utf-8", errors="replace"), ok=True
        )

    def _throttle(self) -> None:
        delay = self.settings.crawl_delay
        if delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()
