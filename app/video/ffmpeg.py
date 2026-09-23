"""FFmpeg discovery and webm -> mp4 transcoding.

FFmpeg is optional: when it is missing the recorder keeps the .webm that
Chromium produced and the lead still reaches review.
"""

from __future__ import annotations

import glob
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import Settings, get_settings


class FFmpegMissing(RuntimeError):
    pass


@lru_cache(maxsize=8)
def supports_h264(binary: str) -> bool:
    """Playwright ships a cut-down ffmpeg that can only encode VP8/PNG."""
    result = subprocess.run(
        [binary, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    )
    return "libx264" in result.stdout or " h264 " in result.stdout


def find_ffmpeg(settings: Settings | None = None, *, require_h264: bool = False) -> str | None:
    """Env override, then PATH, then the ffmpeg bundled with Playwright."""
    settings = settings or get_settings()
    candidates: list[str] = []
    if settings.ffmpeg_bin and Path(settings.ffmpeg_bin).exists():
        candidates.append(settings.ffmpeg_bin)
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(on_path)
    for pattern in (
        "/opt/pw-browsers/ffmpeg-*/ffmpeg-linux",
        str(Path.home() / ".cache/ms-playwright/ffmpeg-*/ffmpeg-linux"),
        str(Path.home() / "Library/Caches/ms-playwright/ffmpeg-*/ffmpeg-mac"),
    ):
        matches = sorted(glob.glob(pattern))
        if matches:
            candidates.append(matches[-1])
    for candidate in candidates:
        if not require_h264 or supports_h264(candidate):
            return candidate
    return None


def probe_duration(path: Path, settings: Settings | None = None) -> float | None:
    """Duration in seconds, via ffprobe when available, else ffmpeg's report."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            try:
                return float(json.loads(result.stdout)["format"]["duration"])
            except (KeyError, ValueError, json.JSONDecodeError):
                return None
    binary = find_ffmpeg(settings)
    if not binary:
        return None
    result = subprocess.run([binary, "-i", str(path)], capture_output=True, text=True, check=False)
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            stamp = line.split("Duration:")[1].split(",")[0].strip()
            try:
                hours, minutes, seconds = stamp.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return None
    return None


def transcode_to_mp4(
    source: Path,
    destination: Path,
    settings: Settings | None = None,
    timeout: int = 600,
    speed_factor: float | None = None,
) -> Path:
    """Convert the Chromium .webm capture into a shareable H.264 .mp4.

    `speed_factor` > 1 compresses the timeline: Chromium's screencast stretches
    timestamps slightly while the page animates, so a 60s script can capture as
    ~67s of video.  Re-timing lands the delivered file on its target length.
    """
    binary = find_ffmpeg(settings, require_h264=True)
    if not binary:
        raise FFmpegMissing(
            "no H.264-capable ffmpeg found (the Playwright bundle only encodes VP8); "
            "install ffmpeg or set FFMPEG_BIN"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if speed_factor and speed_factor > 1.0:
        filters.insert(0, f"setpts=PTS/{speed_factor:.4f}")
    command = [
        binary, "-y", "-i", str(source),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-vf", ",".join(filters),
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-400:]}")
    return destination
