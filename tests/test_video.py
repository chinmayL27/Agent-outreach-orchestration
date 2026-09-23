"""Video script timing, plus a real (fast) recording when Chromium is present."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.lead import Lead
from app.models.outreach import VideoArtifact
from app.models.schemas import DemoConfig
from app.orchestration import pipeline
from app.orchestration.state import LeadStatus
from app.video.recorder import find_chromium
from app.video.script import build_script


def demo_config() -> DemoConfig:
    return DemoConfig(
        lead_id="lead-1",
        business_name="ABC Dermatology",
        specialty="Dermatology",
        services=["Acne treatment"],
        suggested_questions=["Do I need a referral?", "How do I book an appointment?"],
        answers={"Do I need a referral?": "Most plans do not require one."},
        intro_text="intro",
        outro_text="outro",
    )


def test_script_follows_the_sixty_second_shape():
    script = build_script(demo_config())
    kinds = [segment.kind for segment in script.segments]
    assert kinds == ["intro", "brand", "question", "question", "benefit", "cta"]
    assert 59 <= script.total_seconds <= 61
    assert script.segments[2].text == "Do I need a referral?"
    assert script.segments[2].answer == "Most plans do not require one."


def test_script_scales_to_a_shorter_target():
    script = build_script(demo_config(), target_seconds=30)
    assert 29 <= script.total_seconds <= 31


def test_script_survives_a_demo_with_no_questions():
    config = demo_config()
    config.suggested_questions = []
    script = build_script(config)
    assert any(segment.kind == "question" for segment in script.segments)


@pytest.mark.skipif(find_chromium() is None, reason="Playwright Chromium is not installed")
def test_demo_page_records_to_a_video_artifact(session, campaign, settings):
    """demo webpage -> video artifact (recorded at speed so the suite stays quick)."""
    pipeline.run_campaign(session, campaign, demo_limit=1, skip_video=True, settings=settings)
    session.flush()
    lead = session.execute(
        select(Lead).where(Lead.status == LeadStatus.REVIEW_REQUIRED.value)
    ).scalar_one()

    summary = pipeline.render_videos(
        session, campaign, limit=1, force=True, speed=20, settings=settings
    )
    assert summary.succeeded == 1, summary.notes

    video = session.execute(
        select(VideoArtifact).where(VideoArtifact.lead_id == lead.id)
    ).scalar_one()
    assert video.path and video.container in {"mp4", "webm"}
    from pathlib import Path

    assert Path(video.path).exists() and Path(video.path).stat().st_size > 10_000
    assert video.duration_seconds and video.duration_seconds > 1
    # Re-recording must not pull the lead back out of the review queue.
    assert lead.lead_status is LeadStatus.REVIEW_REQUIRED
