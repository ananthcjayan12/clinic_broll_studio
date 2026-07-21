from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import requests

from ..core.config import FFMPEG, FFPROBE
from ..core.io import read_json, write_json
from ..core.usage import UsageTimer, record_usage

SARVAM_URL = "https://api.sarvam.ai/speech-to-text"


def _duration(path: Path) -> float:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return float(result.stdout.strip())


def transcribe_audio(audio: Path, output_dir: Path) -> dict[str, Any]:
    """Transcribe Malayalam using Sarvam Saaras V3 only.

    The Batch transport is the production default because it accepts the whole
    talking-head audio and returns sentence/phrase timestamps. The REST transport
    remains available as a Sarvam-only fallback for short tests by setting
    ``CBS_SARVAM_TRANSPORT=rest``.
    """
    timer = UsageTimer()
    key = os.getenv("SARVAM_API_KEY") or os.getenv("SARVAM_API_SUBSCRIPTION_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is required")
    model = os.getenv("SARVAM_STT_MODEL", "saaras:v3")
    mode = os.getenv("SARVAM_STT_MODE", "codemix")
    language = os.getenv("SARVAM_LANGUAGE_CODE", "ml-IN")
    transport = os.getenv("CBS_SARVAM_TRANSPORT", "batch").strip().lower()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if transport == "batch":
            result, raw_responses, request_count = _batch_transcribe(
                audio=audio,
                output_dir=output_dir,
                key=key,
                model=model,
                mode=mode,
                language=language,
            )
        elif transport == "rest":
            result, raw_responses, request_count = _rest_transcribe(
                audio=audio,
                key=key,
                model=model,
                mode=mode,
                language=language,
            )
        else:
            raise RuntimeError("CBS_SARVAM_TRANSPORT must be 'batch' or 'rest'")
    except Exception as exc:
        record_usage(output_dir.parent, "asr_usage.json", {
            "provider": "sarvam", "model": model, "mode": mode, "language_code": language,
            "transport": transport, "status": "failed", "duration_seconds": timer.seconds(),
            "error": str(exc),
        })
        raise

    result.update({
        "version": "1.0",
        "provider": "sarvam",
        "model": model,
        "mode": mode,
        "language_code": result.get("language_code") or language,
        "transport": transport,
        "raw_response_count": len(raw_responses),
    })
    write_json(output_dir / "transcript.json", result)
    write_json(output_dir / "sarvam-responses.json", raw_responses)
    _write_srt(output_dir / "captions.srt", result.get("phrases") or [])
    record_usage(output_dir.parent, "asr_usage.json", {
        "provider": "sarvam", "model": model, "mode": mode,
        "language_code": result["language_code"], "transport": transport,
        "status": "complete", "duration_seconds": timer.seconds(),
        "audio_seconds": result.get("duration_seconds"), "requests": request_count,
    })
    return result


def _batch_transcribe(
    *, audio: Path, output_dir: Path, key: str, model: str, mode: str, language: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    try:
        from sarvamai import SarvamAI
    except ImportError as exc:
        raise RuntimeError("Sarvam Batch STT requires the official `sarvamai` package. Reinstall project dependencies.") from exc

    raw_dir = output_dir / "batch-output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = SarvamAI(api_subscription_key=key)
    job = client.speech_to_text_job.create_job(
        model=model,
        mode=mode,
        language_code=language,
    )
    job.upload_files(file_paths=[str(audio)])
    job.start()
    job.wait_until_complete(poll_interval=5, timeout=1800)
    file_results = job.get_file_results()
    failed = list((file_results or {}).get("failed") or [])
    if failed:
        errors = "; ".join(str(item.get("error_message") or item) for item in failed)
        raise RuntimeError(f"Sarvam Batch STT failed: {errors}")
    job.download_outputs(output_dir=str(raw_dir))
    raw_responses = [read_json(path) for path in sorted(raw_dir.rglob("*.json"))]
    raw_responses = [payload for payload in raw_responses if isinstance(payload, dict)]
    if not raw_responses:
        raise RuntimeError("Sarvam Batch STT completed without a downloaded JSON transcript")
    # One source audio file is submitted per run. Keep flexible parsing in case
    # the SDK nests the provider payload under an output/result key.
    payload = _unwrap_response(raw_responses[0])
    result = _normalise_response(payload, _duration(audio))
    return result, raw_responses, 1


def _rest_transcribe(
    *, audio: Path, key: str, model: str, mode: str, language: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    duration = _duration(audio)
    if duration > 30.0:
        raise RuntimeError(
            "Sarvam REST accepts only short clips. Use the default Batch transport for videos longer than 30 seconds."
        )
    with audio.open("rb") as handle:
        response = requests.post(
            SARVAM_URL,
            headers={"api-subscription-key": key},
            files={"file": (audio.name, handle, "audio/wav")},
            data={
                "model": model,
                "mode": mode,
                "language_code": language,
                "with_timestamps": "true",
                "input_audio_codec": "pcm_s16le",
            },
            timeout=180,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Sarvam STT failed: HTTP {response.status_code}: {response.text[-2000:]}")
    payload = response.json()
    return _normalise_response(payload, duration), [payload], 1


def _unwrap_response(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("result", "output", "response", "data"):
        child = payload.get(key)
        if isinstance(child, dict) and ("transcript" in child or "timestamps" in child):
            return child
    return payload


def _normalise_response(payload: dict[str, Any], duration: float) -> dict[str, Any]:
    transcript = str(payload.get("transcript") or "").strip()
    timestamp_data = payload.get("timestamps") or {}
    phrases = _timed_items(timestamp_data)
    if not phrases:
        nested = timestamp_data.get("timestamps") if isinstance(timestamp_data, dict) else None
        phrases = _timed_items(nested or {})
    if not phrases:
        diarized = (payload.get("diarized_transcript") or {}).get("entries") or []
        for item in diarized:
            text = str(item.get("transcript") or "").strip()
            if text:
                phrases.append({
                    "text": text,
                    "start": float(item.get("start_time_seconds") or 0),
                    "end": float(item.get("end_time_seconds") or duration),
                })
    if not phrases and transcript:
        phrases = [{"text": transcript, "start": 0.0, "end": duration}]

    cleaned_phrases = []
    for item in phrases:
        text = str(item.get("text") or "").strip()
        start = max(0.0, min(float(item.get("start") or 0), duration))
        end = max(start, min(float(item.get("end") or duration), duration))
        if not text or end <= start:
            continue
        cleaned_phrases.append({
            "id": f"phrase_{len(cleaned_phrases)+1:04d}",
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
        })
    cleaned_phrases.sort(key=lambda item: (item["start"], item["end"]))

    words = []
    raw_words = timestamp_data.get("words") if isinstance(timestamp_data, dict) else None
    starts = timestamp_data.get("start_time_seconds") if isinstance(timestamp_data, dict) else None
    ends = timestamp_data.get("end_time_seconds") if isinstance(timestamp_data, dict) else None
    if isinstance(raw_words, list) and isinstance(starts, list) and isinstance(ends, list):
        # REST may return word-level timing; Batch commonly returns chunks here.
        for index, word in enumerate(raw_words):
            if index >= len(starts) or index >= len(ends):
                break
            words.append({
                "id": f"word_{len(words)+1:05d}",
                "word": str(word),
                "start": round(float(starts[index]), 3),
                "end": round(float(ends[index]), 3),
            })

    canonical = transcript or " ".join(item["text"] for item in cleaned_phrases)
    return {
        "duration_seconds": round(duration, 3),
        "transcript": canonical.strip(),
        "words": words,
        "phrases": cleaned_phrases,
        "language_code": payload.get("language_code"),
        "language_probability": payload.get("language_probability"),
    }


def _timed_items(timestamp_data: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(timestamp_data, dict):
        return []
    texts = timestamp_data.get("chunks") or timestamp_data.get("words") or []
    starts = timestamp_data.get("start_time_seconds") or []
    ends = timestamp_data.get("end_time_seconds") or []
    if not all(isinstance(value, list) for value in (texts, starts, ends)):
        return []
    result = []
    for index, text in enumerate(texts):
        if index >= len(starts) or index >= len(ends):
            break
        result.append({"text": str(text), "start": float(starts[index]), "end": float(ends[index])})
    return result


def _write_srt(path: Path, phrases: list[dict[str, Any]]) -> None:
    def stamp(seconds: float) -> str:
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, ms = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    blocks = []
    for index, phrase in enumerate(phrases, start=1):
        blocks.append(
            f"{index}\n{stamp(float(phrase['start']))} --> {stamp(float(phrase['end']))}\n{phrase['text']}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")
