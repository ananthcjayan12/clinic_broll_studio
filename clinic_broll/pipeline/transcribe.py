from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..providers.sarvam import transcribe_audio


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    result = transcribe_audio(paths.source / "speech.wav", paths.transcript)
    return {"artifacts": ["transcript/transcript.json", "transcript/captions.srt", "transcript/sarvam-responses.json"], "summary": {"phrases": len(result["phrases"]), "words": len(result["words"]), "language_code": result["language_code"]}}
