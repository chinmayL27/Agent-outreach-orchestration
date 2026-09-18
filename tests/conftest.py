"""Shared fixtures: an isolated database, artifacts dir and settings per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db as db_module
from app.config import get_settings
from app.models.campaign import CampaignConfig

FIXTURES = Path(__file__).parent / "fixtures"
SITES = FIXTURES / "sites"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Point every path and provider at a throwaway location."""
    monkeypatch.setenv("OUTREACH_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("OUTREACH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("LLM_PROVIDER", "template")
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("CRAWL_DELAY", "0")
    monkeypatch.setenv("RESPECT_ROBOTS", "false")
    monkeypatch.setenv("SENDER_NAME", "Sam Tester")
    monkeypatch.setenv("SENDER_COMPANY", "Test Co")
    monkeypatch.setenv("SENDER_EMAIL", "sam@testco.example")
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", "1 Test St, San Jose, CA 95113")
    monkeypatch.setenv("UNSUBSCRIBE_MAILTO", "unsubscribe@testco.example")
    get_settings.cache_clear()
    db_module.reset_engine()
    resolved = get_settings()
    resolved.ensure_dirs()
    yield resolved
    db_module.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def session(settings):
    db_module.init_db()
    with db_module.session_scope() as active:
        yield active


@pytest.fixture
def campaign() -> CampaignConfig:
    config = CampaignConfig.load(FIXTURES.parent.parent / "campaign.demo.yaml")
    config.id = "test"
    return config


@pytest.fixture
def site_url():
    def _url(name: str) -> str:
        return (SITES / name / "index.html").resolve().as_uri()

    return _url
