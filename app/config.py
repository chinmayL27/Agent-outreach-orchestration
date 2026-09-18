"""Runtime settings.

Everything is read from the environment (optionally seeded by a local .env
file) so that no credential ever has to live in source control.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency on python-dotenv)."""
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_url: str = "sqlite:///outreach.db"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"

    llm_provider: str = "template"
    claude_cli_bin: str = "claude"
    claude_cli_timeout: int = 180
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    max_pages_per_site: int = 15
    request_timeout: int = 10
    crawl_delay: float = 0.5
    user_agent: str = "outreach-agent/0.1"
    respect_robots: bool = True
    #: file:// crawling is only for the bundled offline demo fixtures.
    allow_file_urls: bool = False

    ffmpeg_bin: str | None = None
    chromium_executable: str | None = None
    video_width: int = 1280
    video_height: int = 720
    video_min_seconds: float = 45.0
    video_max_seconds: float = 65.0

    demo_base_url: str = "http://localhost:8000"
    #: Public base URL where demos/videos are published for recipients.
    public_artifact_base_url: str | None = None

    email_provider: str = "console"
    sender_name: str = "Your Name"
    sender_email: str = "you@example.com"
    sender_company: str = "Your Company"
    sender_postal_address: str = ""
    unsubscribe_mailto: str = ""
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None

    banned_marketing_phrases: tuple[str, ...] = field(
        default_factory=lambda: (
            "revolutionary",
            "cutting-edge",
            "game-changing",
            "world-class",
            "10x",
            "guaranteed roi",
            "as we discussed",
            "as promised",
            "following up on our call",
        )
    )

    @property
    def demos_dir(self) -> Path:
        return self.artifacts_dir / "demos"

    @property
    def videos_dir(self) -> Path:
        return self.artifacts_dir / "videos"

    @property
    def reports_dir(self) -> Path:
        return self.artifacts_dir / "reports"

    @property
    def outbox_dir(self) -> Path:
        return self.artifacts_dir / "outbox"

    def ensure_dirs(self) -> None:
        for directory in (self.demos_dir, self.videos_dir, self.reports_dir, self.outbox_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    artifacts = Path(os.getenv("OUTREACH_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts")))
    if not artifacts.is_absolute():
        artifacts = PROJECT_ROOT / artifacts
    return Settings(
        db_url=os.getenv("OUTREACH_DB_URL", "sqlite:///outreach.db"),
        artifacts_dir=artifacts,
        llm_provider=os.getenv("LLM_PROVIDER", "template"),
        claude_cli_bin=os.getenv("CLAUDE_CLI_BIN", "claude"),
        claude_cli_timeout=_int("CLAUDE_CLI_TIMEOUT", 180),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_pages_per_site=_int("MAX_PAGES_PER_SITE", 15),
        request_timeout=_int("REQUEST_TIMEOUT", 10),
        crawl_delay=_float("CRAWL_DELAY", 0.5),
        user_agent=os.getenv("USER_AGENT", "outreach-agent/0.1"),
        respect_robots=_bool("RESPECT_ROBOTS", True),
        allow_file_urls=_bool("ALLOW_FILE_URLS", False),
        ffmpeg_bin=os.getenv("FFMPEG_BIN"),
        chromium_executable=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
        video_width=_int("VIDEO_WIDTH", 1280),
        video_height=_int("VIDEO_HEIGHT", 720),
        demo_base_url=os.getenv("DEMO_BASE_URL", "http://localhost:8000").rstrip("/"),
        public_artifact_base_url=(os.getenv("PUBLIC_ARTIFACT_BASE_URL") or "").rstrip("/") or None,
        email_provider=os.getenv("EMAIL_PROVIDER", "console"),
        sender_name=os.getenv("SENDER_NAME", "Your Name"),
        sender_email=os.getenv("SENDER_EMAIL", "you@example.com"),
        sender_company=os.getenv("SENDER_COMPANY", "Your Company"),
        sender_postal_address=os.getenv("SENDER_POSTAL_ADDRESS", ""),
        unsubscribe_mailto=os.getenv("UNSUBSCRIBE_MAILTO", ""),
        smtp_host=os.getenv("SMTP_HOST"),
        smtp_port=_int("SMTP_PORT", 587),
        smtp_user=os.getenv("SMTP_USER"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
    )
