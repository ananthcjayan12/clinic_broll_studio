from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.config import FFMPEG, FFPROBE, PROMPTS_ROOT, SCHEMAS_ROOT
from ..core.io import read_json


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


def run_ffmpeg(arguments: list[str], *, timeout: int = 3600) -> None:
    result = subprocess.run([FFMPEG, "-hide_banner", "-y", *arguments], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr[-5000:]}")
