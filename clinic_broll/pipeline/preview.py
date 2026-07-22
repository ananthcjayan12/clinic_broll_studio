from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..core.state import append_log
from ..rendering.composition import build
from ..rendering.hyperframes import render
from .common import run_ffmpeg


def run_still(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Visual preview: building local HyperFrames composition")
    build(run_id, "still")
    output = paths.previews / "still-preview.mp4"
    result = render(run_id, "still", output)
    append_log(paths, "Visual preview: render complete and ready for review")
    return {"artifacts": ["compositions/still/index.html", "previews/still-preview.mp4"], "summary": result}


def run_motion(run_id: str) -> dict[str, Any]:
    """Compatibility helper retained for slot-level inspection."""
    paths = run_paths(run_id)
    append_log(paths, "Motion preview: building local HyperFrames composition")
    build(run_id, "motion")
    output = paths.previews / "motion-preview.mp4"
    result = render(run_id, "motion", output)
    append_log(paths, "Motion preview: render complete")
    return {"artifacts": ["compositions/motion/index.html", "previews/motion-preview.mp4"], "summary": result}


def run_complete(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Complete preview: rendering layouts, source reframing, zooms, transitions, captions and B-roll")
    build(run_id, "complete")
    silent_or_source_audio = paths.previews / "complete-preview-base.mp4"
    result = render(run_id, "complete", silent_or_source_audio)
    output = paths.previews / "complete-preview.mp4"
    mixed = paths.sound / "final-audio.wav"
    if mixed.exists():
        run_ffmpeg([
            "-i", str(silent_or_source_audio), "-i", str(mixed),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(output),
        ], timeout=3600, paths=paths, label="Complete preview audio")
        silent_or_source_audio.unlink(missing_ok=True)
    else:
        silent_or_source_audio.replace(output)
    append_log(paths, "Complete preview: ready for final editorial review")
    return {
        "artifacts": ["compositions/complete/index.html", "previews/complete-preview.mp4"],
        "summary": {**result, "sound_mix": mixed.exists()},
    }
