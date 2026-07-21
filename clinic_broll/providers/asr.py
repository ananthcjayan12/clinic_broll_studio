from __future__ import annotations

from pathlib import Path
from typing import Any

from .elevenlabs_stt import transcribe_audio as transcribe_with_elevenlabs


def transcribe(audio: Path, output_dir: Path, *, provider: str) -> dict[str, Any]:
    """Shared ASR boundary. Provider selection is strict; there is no fallback."""
    if provider != "elevenlabs":
        raise RuntimeError(f"Unsupported ASR provider: {provider}")
    return transcribe_with_elevenlabs(audio, output_dir)
