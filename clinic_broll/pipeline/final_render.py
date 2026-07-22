from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..core.config import FFMPEG
from ..core.io import read_json
from ..core.paths import run_paths
from ..core.state import append_log
from ..rendering.hyperframes import render, validate


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    output = paths.renders / "final.mp4"
    candidate_output = output if not output.exists() else _next_final_candidate(paths.renders)
    append_log(paths, "Final V2 render: linting the deterministic HyperFrames composition")
    validation = validate(run_id, "final")
    source_meta = read_json(paths.source / "metadata.json", {})
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    duration = float(source_meta.get("duration_seconds") or 0)
    fps = int(source_meta.get("fps") or 30)
    # Every non-rejected scene matters in V2 because talking-head punch-ins,
    # split crops and PIP are rendered by the overlay even without generated media.
    active_slots = [slot for slot in plan.get("slots", []) if slot.get("status") != "rejected"]
    windows = _chunk_windows(duration, fps, active_slots)
    overlay_dir = _next_overlay_dir(paths.renders)
    overlay_dir.mkdir(parents=True)
    append_log(paths, f"Final V2 render: rendering {len(windows)} transparent editorial chunks")
    rendered_chunks = []
    chunk_files = []
    for index, window in enumerate(windows, start=1):
        chunk = overlay_dir / f"part-{index:03d}.webm"
        chunk_files.append(chunk)
        append_log(paths, f"Final V2 render: transparent chunk {index}/{len(windows)}")
        rendered_chunks.append(render(
            run_id, "final", chunk, window=window,
            composition_name=f"final-chunks/{overlay_dir.name}/part-{index:03d}",
        ))
    concat_manifest = overlay_dir / "parts.ffconcat"
    _write_concat_manifest(concat_manifest, chunk_files)
    overlay = overlay_dir / "overlay.webm"
    concat_result = subprocess.run(_concat_command(concat_manifest, overlay), capture_output=True, text=True, timeout=14400)
    if concat_result.returncode != 0:
        raise RuntimeError(f"FFmpeg overlay concat failed: {(concat_result.stderr or concat_result.stdout)[-5000:]}")
    mixed_audio = paths.sound / "final-audio.wav"
    append_log(paths, "Final V2 render: compositing editorial overlay over the clean master")
    command = _composite_command(paths.source / "master.mp4", overlay, candidate_output, mixed_audio if mixed_audio.exists() else None)
    result = subprocess.run(command, capture_output=True, text=True, timeout=14400)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg final composite failed: {(result.stderr or result.stdout)[-5000:]}")
    if not candidate_output.exists():
        raise RuntimeError("FFmpeg completed without producing final.mp4")
    archived_final = _publish_final(candidate_output, output)
    if archived_final is not None:
        append_log(paths, f"Final V2 render: previous MP4 preserved as {archived_final.name}")
    append_log(paths, "Final V2 render: MP4 complete")
    return {
        "artifacts": ["compositions/final/index.html", str(overlay_dir.relative_to(paths.root)), "renders/final.mp4"],
        "summary": {"validation": validation, "render_chunks": rendered_chunks, "windows": windows, "sound_mix": mixed_audio.exists()},
    }


def _next_overlay_dir(renders: Path) -> Path:
    version = 1
    while True:
        candidate = renders / f"final-overlay-v{version:03d}"
        if not candidate.exists():
            return candidate
        version += 1


def _next_final_candidate(renders: Path) -> Path:
    version = 1
    while True:
        candidate = renders / f"final-candidate-v{version:03d}.mp4"
        if not candidate.exists():
            return candidate
        version += 1


def _next_final_archive(renders: Path) -> Path:
    version = 1
    while True:
        candidate = renders / f"final-v{version:03d}.mp4"
        if not candidate.exists():
            return candidate
        version += 1


def _publish_final(candidate: Path, canonical: Path) -> Path | None:
    if candidate == canonical:
        return None
    archive = _next_final_archive(canonical.parent)
    canonical.replace(archive)
    candidate.replace(canonical)
    return archive


def _chunk_windows(duration: float, fps: int, slots: list[dict[str, Any]], max_seconds: float = 10.0) -> list[tuple[float, float]]:
    if duration <= 0 or fps <= 0 or max_seconds <= 0:
        raise ValueError("Duration, fps, and chunk size must be positive")
    intervals = sorted((float(slot["start"]), float(slot["start"]) + float(slot["duration"])) for slot in slots)
    # Use the authored scene boundaries exactly.  Older plans can contain
    # millisecond boundaries that are not frame-aligned; rounding them down
    # creates a chunk boundary inside a scene and makes composition.build fail.
    safe_boundaries = sorted({0.0, duration, *(value for interval in intervals for value in interval)})
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration - 1e-6:
        target = min(start + max_seconds, duration)
        before = [point for point in safe_boundaries if start + 1e-6 < point <= target + 1e-6]
        if before:
            end = max(before)
        else:
            # A single scene exceeds the preferred chunk duration. Preserve its
            # timing as one chunk rather than splitting it.
            after = [point for point in safe_boundaries if point > start + 1e-6]
            end = min(after) if after else duration
        if end <= start + 1e-6:
            raise RuntimeError("Could not find a safe final-render chunk boundary")
        windows.append((round(start, 6), round(end, 6)))
        start = end
    return windows


def _write_concat_manifest(manifest: Path, chunks: list[Path]) -> None:
    lines = ["ffconcat version 1.0", *(f"file '{chunk.name}'" for chunk in chunks)]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_command(manifest: Path, output: Path) -> list[str]:
    return [FFMPEG, "-hide_banner", "-n", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(output)]


def _composite_command(master: Path, overlay: Path, output: Path, audio_override: Path | None = None) -> list[str]:
    command = [
        FFMPEG, "-hide_banner", "-n", "-i", str(master),
        "-c:v", "libvpx-vp9", "-i", str(overlay),
    ]
    if audio_override is not None:
        command.extend(["-i", str(audio_override)])
    command.extend([
        "-filter_complex", "[0:v:0][1:v:0]overlay=0:0:format=auto:eof_action=pass[v]",
        "-map", "[v]",
    ])
    if audio_override is None:
        command.extend(["-map", "0:a?", "-map_metadata", "0", "-c:a", "copy"])
    else:
        command.extend(["-map", "2:a:0", "-map_metadata", "0", "-c:a", "aac", "-b:a", "192k", "-shortest"])
    command.extend([
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ])
    return command
