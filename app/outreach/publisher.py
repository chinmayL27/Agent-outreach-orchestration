"""The artifact publishing boundary (TDD s16).

Generation is local, but a clinic cannot open `file:///...` or
`http://localhost:8000/...`.  A publisher turns local artifacts into URLs a
recipient can actually reach; until one is configured, the pipeline keeps the
package review-only and refuses to send it through a real mail provider.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from app.config import Settings, get_settings

PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


@dataclass
class PublishedArtifacts:
    demo_url: str | None = None
    video_url: str | None = None

    @property
    def any_public(self) -> bool:
        return bool(self.demo_url or self.video_url)


def is_publicly_reachable(url: str | None) -> bool:
    """A link is only useful to a recipient if it leaves the operator's machine."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in PRIVATE_HOSTNAMES or host.endswith(".local"):
        return False
    try:
        return not ip_address(host).is_private
    except ValueError:
        return "." in host  # a hostname, not a bare label


class ArtifactPublisher(Protocol):
    name: str

    def publish(self, lead_id: str, demo_path: Path | None, video_path: Path | None) -> PublishedArtifacts: ...


class NullPublisher:
    """Default: publish nothing. Review works locally; sending links does not."""

    name = "none"

    def publish(self, lead_id: str, demo_path: Path | None, video_path: Path | None) -> PublishedArtifacts:
        return PublishedArtifacts()


class DirectoryPublisher:
    """Copy artifacts into a directory that some web host serves publicly.

    The operator points PUBLIC_ARTIFACT_DIR at (for example) a synced bucket
    mount or a static site folder, and PUBLIC_ARTIFACT_BASE_URL at the URL that
    serves it.
    """

    name = "directory"

    def __init__(self, settings: Settings | None = None, target_dir: Path | None = None):
        self.settings = settings or get_settings()
        self.target_dir = target_dir
        self.base_url = (self.settings.public_artifact_base_url or "").rstrip("/")

    def publish(self, lead_id: str, demo_path: Path | None, video_path: Path | None) -> PublishedArtifacts:
        if not self.base_url:
            return PublishedArtifacts()
        published = PublishedArtifacts()
        for path, attribute in ((demo_path, "demo_url"), (video_path, "video_url")):
            if path is None or not Path(path).exists():
                continue
            name = f"{lead_id}{Path(path).suffix}"
            if self.target_dir is not None:
                self.target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, self.target_dir / name)
            url = f"{self.base_url}/{name}"
            if is_publicly_reachable(url):
                setattr(published, attribute, url)
        return published


def get_publisher(settings: Settings | None = None) -> ArtifactPublisher:
    settings = settings or get_settings()
    if settings.public_artifact_base_url:
        return DirectoryPublisher(settings)
    return NullPublisher()
