"""Deterministic screen recording of the personalized demo page.

Playwright drives the page through the video script; Chromium records the
session; ffmpeg (optional) converts it to mp4.
"""

from __future__ import annotations

import glob
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.demo.renderer import demo_file_url
from app.models.schemas import VideoScript
from app.video.ffmpeg import FFmpegMissing, probe_duration, transcode_to_mp4


class VideoUnavailable(RuntimeError):
    """Playwright or its browser is not installed."""


@dataclass
class RecordingResult:
    path: Path
    container: str
    duration_seconds: float | None
    warnings: list[str]


def find_chromium(settings: Settings | None = None) -> str | None:
    """Explicit executable beats a version-mismatched browser bundle."""
    settings = settings or get_settings()
    if settings.chromium_executable and Path(settings.chromium_executable).exists():
        return settings.chromium_executable
    for pattern in (
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        str(Path.home() / "Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    ):
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def record_demo(
    script: VideoScript,
    *,
    page_url: str | None = None,
    settings: Settings | None = None,
    speed: float = 1.0,
) -> RecordingResult:
    """Record the demo and return the final artifact path.

    `speed` > 1 shortens every beat proportionally (used by tests so the suite
    does not spend a minute per video).
    """
    settings = settings or get_settings()
    settings.ensure_dirs()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise VideoUnavailable(
            "playwright is not installed; run `pip install playwright` and "
            "`playwright install chromium`"
        ) from exc

    url = page_url or demo_file_url(script.lead_id, settings)
    raw_dir = settings.videos_dir / f"raw-{script.lead_id}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    executable = find_chromium(settings)

    def beat(seconds: float) -> float:
        return max(0.2, seconds / max(speed, 0.01))

    with sync_playwright() as playwright:
        launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if executable:
            launch_kwargs["executable_path"] = executable
        try:
            browser = playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced as a stage failure
            raise VideoUnavailable(f"could not launch Chromium: {exc}") from exc

        # Chromium starts recording the moment the context exists, so the
        # clock for the first beat starts here, not after the page loads.
        setup_started = time.monotonic()
        context = browser.new_context(
            viewport={"width": settings.video_width, "height": settings.video_height},
            record_video_dir=str(raw_dir),
            record_video_size={"width": settings.video_width, "height": settings.video_height},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="load")
            page.wait_for_function("window.demo && window.demo.ready === true", timeout=15000)
            setup_elapsed = time.monotonic() - setup_started

            for index, segment in enumerate(script.segments):
                segment_started = time.monotonic()
                # Recording starts before the page loads, so the first beat
                # absorbs the setup time; scripted actions (typing, thinking)
                # count against their own beat rather than extending the video.
                budget = beat(segment.seconds) - (setup_elapsed if index == 0 else 0.0)

                if segment.kind == "intro":
                    page.evaluate("text => window.demo.showIntro(text)", segment.text)
                elif segment.kind == "question":
                    per_char = max(4, int(28 / max(speed, 1)))
                    page.evaluate(
                        "args => window.demo.ask(args.question, "
                        "{msPerChar: args.perChar, thinkMs: args.think})",
                        {
                            "question": segment.text,
                            "perChar": per_char,
                            "think": int(900 / max(speed, 1)),
                        },
                    )
                elif segment.kind == "cta":
                    page.evaluate("text => window.demo.showOutro(text)", segment.text)

                remaining = budget - (time.monotonic() - segment_started)
                if remaining > 0:
                    page.wait_for_timeout(remaining * 1000)
                if segment.kind == "intro":
                    page.evaluate("window.demo.hideIntro()")
        finally:
            video = page.video
            context.close()
            browser.close()

        if video is None:  # pragma: no cover - only if recording was disabled
            raise RuntimeError("Chromium produced no video for this session")
        source = Path(video.path())

    webm_path = settings.videos_dir / f"{script.lead_id}.webm"
    shutil.move(str(source), webm_path)
    shutil.rmtree(raw_dir, ignore_errors=True)

    final_path, container = webm_path, "webm"
    captured = probe_duration(webm_path, settings)
    target = script.target_seconds / max(speed, 0.01)
    speed_factor = None
    if captured and target > 0 and captured / target > 1.05:
        speed_factor = captured / target
    try:
        final_path = transcode_to_mp4(
            webm_path,
            settings.videos_dir / f"{script.lead_id}.mp4",
            settings,
            speed_factor=speed_factor,
        )
        container = "mp4"
        webm_path.unlink(missing_ok=True)
    except FFmpegMissing as exc:
        warnings.append(f"{exc}; keeping .webm")
    except RuntimeError as exc:
        warnings.append(f"transcode failed ({exc}); keeping .webm")

    return RecordingResult(
        path=final_path,
        container=container,
        duration_seconds=probe_duration(final_path, settings),
        warnings=warnings,
    )
