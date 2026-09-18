"""Build the ~60 second video script from the personalization + demo config."""

from __future__ import annotations

from app.models.schemas import DemoConfig, VideoScript, VideoSegment

#: Seconds per beat, matching the 0-7 / 7-15 / 15-35 / 35-48 / 48-55 / 55-60 plan.
DEFAULT_TIMINGS = {
    "intro": 7.0,
    "brand": 8.0,
    "question": 12.0,
    "benefit": 7.0,
    "cta": 6.0,
}


def build_script(config: DemoConfig, target_seconds: float = 60.0) -> VideoScript:
    questions = list(config.suggested_questions)[:2] or ["How do I book an appointment?"]
    segments = [
        VideoSegment(kind="intro", seconds=DEFAULT_TIMINGS["intro"], text=config.intro_text),
        VideoSegment(
            kind="brand",
            seconds=DEFAULT_TIMINGS["brand"],
            text=f"{config.business_name} assistant, built from your public website",
        ),
    ]
    for question in questions:
        segments.append(
            VideoSegment(
                kind="question",
                seconds=DEFAULT_TIMINGS["question"],
                text=question,
                answer=config.answers.get(question, ""),
            )
        )
    segments.append(
        VideoSegment(
            kind="benefit",
            seconds=DEFAULT_TIMINGS["benefit"],
            text="Routes patients to the right service, location or appointment request.",
        )
    )
    segments.append(VideoSegment(kind="cta", seconds=DEFAULT_TIMINGS["cta"], text=config.outro_text))

    script = VideoScript(lead_id=config.lead_id, target_seconds=target_seconds, segments=segments)
    _fit_to_target(script)
    return script


def _fit_to_target(script: VideoScript) -> None:
    """Scale segment durations so the recording lands near the target length."""
    total = script.total_seconds
    if total <= 0:
        return
    factor = script.target_seconds / total
    if 0.95 <= factor <= 1.05:
        return
    for segment in script.segments:
        segment.seconds = round(segment.seconds * factor, 2)
