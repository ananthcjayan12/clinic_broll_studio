from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.asr import transcribe


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    provider = str(meta["settings"].get("asr_provider") or "elevenlabs")
    append_log(paths, "Transcription: sending extracted speech audio to ElevenLabs Scribe v2")
    result = transcribe(paths.source / "speech.wav", paths.transcript, provider=provider)
    append_log(paths, f"Transcription: received {len(result['phrases'])} phrases and {len(result['words'])} words")
    return {"artifacts": ["transcript/transcript.json", "transcript/captions.srt", "transcript/elevenlabs-response.json"], "summary": {"phrases": len(result["phrases"]), "words": len(result["words"]), "language_code": result["language_code"]}}
