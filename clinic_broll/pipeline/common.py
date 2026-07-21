from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..core.config import FFMPEG, FFPROBE, PROMPTS_ROOT, SCHEMAS_ROOT
from ..core.io import read_json
from ..core.paths import RunPaths
from ..core.state import append_log


def ffprobe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-3000:]}")
    return json.loads(result.stdout)


def media_duration(path: Path) -> float:
    payload = ffprobe(path)
    return float((payload.get("format") or {}).get("duration") or 0)


def load_prompt(name: str) -> str:
    return (PROMPTS_ROOT / name).read_text(encoding="utf-8")


def load_schema(name: str) -> dict[str, Any]:
    return read_json(SCHEMAS_ROOT / name)


def run_ffmpeg(
    arguments: list[str],
    *,
    timeout: int = 3600,
    paths: RunPaths | None = None,
    label: str = "FFmpeg",
    duration: float | None = None,
) -> None:
    """Run FFmpeg and emit throttled, operator-friendly progress to the run log."""
    command = [FFMPEG, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *arguments]
    if paths is not None:
        append_log(paths, f"{label}: started")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output: deque[str] = deque(maxlen=180)
    messages: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            messages.put(line.rstrip())
        messages.put(None)

    threading.Thread(target=read_output, daemon=True, name="cbs-ffmpeg-output").start()
    started = time.monotonic()
    last_logged = 0.0
    progress: dict[str, str] = {}
    reader_done = False
    while not reader_done or process.poll() is None:
        if time.monotonic() - started > timeout:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(command, timeout)
        try:
            line = messages.get(timeout=0.25)
        except queue.Empty:
            continue
        if line is None:
            reader_done = True
            continue
        output.append(line)
        is_end = False
        if "=" in line:
            key, value = line.split("=", 1)
            if key in {"frame", "fps", "out_time", "out_time_ms", "speed", "progress"}:
                progress[key] = value
                is_end = key == "progress" and value == "end"
        now = time.monotonic()
        if paths is not None and progress.get("out_time") and (now - last_logged >= 1.5 or is_end):
            out_seconds = _ffmpeg_time_seconds(progress["out_time"])
            percent = ""
            if duration and duration > 0:
                value = 100.0 if is_end else min(100.0, out_seconds / duration * 100)
                percent = f" · {value:.1f}%"
            detail = f"time {progress['out_time']}{percent}"
            if progress.get("frame"):
                detail += f" · frame {progress['frame']}"
            if progress.get("speed"):
                detail += f" · speed {progress['speed']}"
            append_log(paths, f"{label}: {detail}")
            last_logged = now
    returncode = process.wait()
    if returncode != 0:
        failure_detail = "\n".join(output)[-5000:]
        raise RuntimeError(f"FFmpeg failed: {failure_detail}")
    if paths is not None:
        append_log(paths, f"{label}: complete")


def _ffmpeg_time_seconds(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0
