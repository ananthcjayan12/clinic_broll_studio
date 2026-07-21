from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import requests

from ..core.io import write_json
from ..core.config import FFPROBE
from ..core.usage import UsageTimer, record_usage

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def transcribe_audio(audio: Path, output_dir: Path) -> dict[str, Any]:
    """Transcribe Malayalam with Scribe v2 and retain exact word timings."""
    timer = UsageTimer()
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is required")
    model = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")
    language = os.getenv("ELEVENLABS_LANGUAGE_CODE", "mal")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with audio.open("rb") as handle:
            response = requests.post(
                ELEVENLABS_STT_URL,
                headers={"xi-api-key": key},
                files={"file": (audio.name, handle, "audio/wav")},
                data={
                    "model_id": model,
                    "language_code": language,
                    "timestamps_granularity": "word",
                    "diarize": "false",
                    "tag_audio_events": "false",
                },
                timeout=600,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"ElevenLabs Scribe STT failed: HTTP {response.status_code}: {response.text[-2000:]}"
            )
        raw = response.json()
        result = _normalise_response(raw, duration=_duration(audio))
        if not result["words"]:
            raise RuntimeError("ElevenLabs Scribe returned no word-level timestamps")
    except Exception as exc:
        record_usage(output_dir.parent, "asr_usage.json", {
            "provider": "elevenlabs", "model": model, "language_code": language,
            "status": "failed", "duration_seconds": timer.seconds(), "error": str(exc),
        })
        raise

    write_json(output_dir / "transcript.json", result)
    write_json(output_dir / "elevenlabs-response.json", raw)
    _write_srt(output_dir / "captions.srt", result["captions"])
    record_usage(output_dir.parent, "asr_usage.json", {
        "provider": "elevenlabs", "model": model, "language_code": result["language_code"],
        "status": "complete", "duration_seconds": timer.seconds(),
        "audio_seconds": result["duration_seconds"], "requests": 1,
    })
    return result


def _duration(path: Path) -> float:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return float(result.stdout.strip())


def _normalise_response(payload: dict[str, Any], duration: float | None = None) -> dict[str, Any]:
    words = []
    for item in payload.get("words") or []:
        if item.get("type") != "word" or item.get("start") is None or item.get("end") is None:
            continue
        start, end = float(item["start"]), float(item["end"])
        text = str(item.get("text") or "").strip()
        if text and end > start:
            words.append({
                "id": f"word_{len(words) + 1:05d}",
                "word": text,
                "start": round(start, 3),
                "end": round(end, 3),
            })
    words.sort(key=lambda item: (item["start"], item["end"]))
    phrases = _group_words(words)
    captions = _group_captions(words)
    duration = duration if duration is not None else max((word["end"] for word in words), default=0.0)
    return {
        "version": "1.0",
        "provider": "elevenlabs",
        "model": "scribe_v2",
        "mode": "word_timestamps",
        "language_code": payload.get("language_code") or "mal",
        "language_probability": payload.get("language_probability"),
        "duration_seconds": round(duration, 3),
        "transcript": str(payload.get("text") or "").strip(),
        "words": words,
        "phrases": phrases,
        "captions": captions,
        "raw_response_count": 1,
    }


def _group_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create short, exact-boundary editorial phrases from timed words."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        previous = current[-1] if current else None
        if previous and (word["start"] - previous["end"] >= 0.7 or word["end"] - current[0]["start"] > 6.0):
            groups.append(current)
            current = []
        current.append(word)
        if re.search(r"[.!?।]$", word["word"]) and word["end"] - current[0]["start"] >= 1.0:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    phrases = []
    for group in groups:
        text = " ".join(word["word"] for word in group)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        phrases.append({
            "id": f"phrase_{len(phrases) + 1:04d}",
            "text": text,
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })
    return phrases


def _group_captions(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group exact word timings into compact, readable caption cards."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        previous = current[-1] if current else None
        if previous and (word["start"] - previous["end"] >= 0.7 or len(current) >= 6 or word["end"] - current[0]["start"] > 3.0):
            groups.append(current)
            current = []
        current.append(word)
        if re.search(r"[.!?।]$", word["word"]):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [
        {
            "id": f"caption_{index:04d}",
            "text": re.sub(r"\s+([,.;:!?])", r"\1", " ".join(word["word"] for word in group)),
            "start": group[0]["start"],
            "end": group[-1]["end"],
        }
        for index, group in enumerate(groups, start=1)
    ]


def _write_srt(path: Path, phrases: list[dict[str, Any]]) -> None:
    def stamp(seconds: float) -> str:
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, ms = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    blocks = [
        f"{index}\n{stamp(float(phrase['start']))} --> {stamp(float(phrase['end']))}\n{phrase['text']}\n"
        for index, phrase in enumerate(phrases, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
