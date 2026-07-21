from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = Path(os.getenv("CBS_RUNS_ROOT") or PROJECT_ROOT / "runs").resolve()
STATIC_ROOT = Path(__file__).resolve().parents[1] / "studio" / "static"
PROMPTS_ROOT = Path(__file__).resolve().parents[1] / "prompts"
SCHEMAS_ROOT = Path(__file__).resolve().parents[1] / "schemas"


def binary(env_name: str, default: str) -> str:
    explicit = os.getenv(env_name, "").strip()
    if explicit:
        return explicit
    found = shutil.which(default)
    return found or default


FFMPEG = binary("CBS_FFMPEG_BIN", "ffmpeg")
FFPROBE = binary("CBS_FFPROBE_BIN", "ffprobe")
HYPERFRAMES = binary("CBS_HYPERFRAMES_BIN", "hyperframes")
PYTHON = os.getenv("CBS_PYTHON_BIN") or os.sys.executable

RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,95}$"
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30
DEFAULT_ASPECT_RATIO = "9:16"
