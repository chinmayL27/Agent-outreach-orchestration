"""Bounded, polite crawl of one clinic website -> cleaned page records."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import Settings, get_settings
from app.extraction.website import candidate_links, sitemap_urls
from app.util.http import Fetcher, normalize_url, same_site
from app.util.text import html_to_text


@dataclass
class PageContent:
    url: str
    title: str
    text: str
    html: str
    mailto_links: list[str] = field(default_factory=list)
    tel_links: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    list_items: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return urlparse(self.url).path.lower()


@dataclass
class CrawlResult:
    base_url: str
    pages: list[PageContent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.pages)

    @property
    def partial(self) -> bool:
        return bool(self.pages) and bool(self.errors)


def parse_page(url: str, html: str) -> PageContent:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text().strip() if soup.title else "") or url
    headings = [
        h.get_text(" ", strip=True)
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if h.get_text(strip=True)
    ]
    list_items = [
        li.get_text(" ", strip=True)
        for li in soup.find_all("li")
        if li.get_text(strip=True)
    ]
    mailto = [a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().startswith("mailto:")]
    tel = [a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().startswith("tel:")]
    return PageContent(
        url=url,
        title=title[:250],
        text=html_to_text(html),
        html=html,
        mailto_links=mailto,
        tel_links=tel,
        headings=headings[:120],
        list_items=list_items[:200],
    )


def crawl_site(
    website: str,
    settings: Settings | None = None,
    fetcher: Fetcher | None = None,
) -> CrawlResult:
    """Fetch the homepage, then the most informative same-site pages."""
    settings = settings or get_settings()
    owns_fetcher = fetcher is None
    fetcher = fetcher or Fetcher(settings)
    base_url = normalize_url(website)
    result = CrawlResult(base_url=base_url)
    try:
        home = fetcher.get(base_url)
        if not home.ok:
            result.errors.append(f"homepage: {home.error}")
            return result
        home_page = parse_page(home.url, home.text)
        result.pages.append(home_page)
        visited = {home.url.rstrip("/")}

        queue = candidate_links(home.url, home.text)
        sitemap = fetcher.get(urljoin(home.url if home.url.endswith("/") else home.url + "/", "sitemap.xml"))
        if sitemap.ok:
            queue += sitemap_urls(home.url, sitemap.text)

        for url in queue:
            if len(result.pages) >= settings.max_pages_per_site:
                break
            key = url.rstrip("/")
            if key in visited or not same_site(base_url, url):
                continue
            visited.add(key)
            page = fetcher.get(url)
            if page.ok:
                result.pages.append(parse_page(page.url, page.text))
            else:
                result.errors.append(f"{url}: {page.error}")
    finally:
        if owns_fetcher:
            fetcher.close()
    return result
