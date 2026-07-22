from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from ..core.config import PROJECT_ROOT
from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema, run_ffmpeg


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    density = str(meta["settings"].get("sfx_density") or "medium")
    editorial = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    choreography = read_json(paths.editorial / "edit-choreography.json", {"cues": []})
    library_root = _library_root()
    manifest_path = library_root / "manifest.json"
    if density == "off":
        plan = {"version": "2.0", "enabled": False, "density": "off", "library_root": str(library_root), "cues": [], "warnings": []}
        write_json(paths.sound / "sound-plan.json", plan)
        append_log(paths, "Sound Director: disabled by operator")
        return {"artifacts": ["sound/sound-plan.json"], "summary": {"enabled": False, "cues": 0}}
    if not manifest_path.exists():
        plan = {
            "version": "2.0", "enabled": False, "density": density,
            "library_root": str(library_root), "cues": [],
            "warnings": [f"Sound library not found at {manifest_path}. Set SFX_LIBRARY_ROOT."],
        }
        write_json(paths.sound / "sound-plan.json", plan)
        append_log(paths, f"Sound Director: library unavailable at {manifest_path}; continuing without SFX")
        return {"artifacts": ["sound/sound-plan.json"], "summary": {"enabled": False, "cues": 0, "warning": plan["warnings"][0]}}

    manifest = read_json(manifest_path, {})
    sounds = list(manifest.get("sounds") or [])
    validation = _validate_library(library_root, sounds)
    write_json(paths.sound / "library-validation.json", validation)
    if validation["missing_files"]:
        append_log(paths, f"Sound Director: {len(validation['missing_files'])} manifest files are missing")
    intents = _scene_intents(editorial, choreography)
    shortlisted = _shortlist(intents, sounds, top_n=8)
    selection = meta["settings"]["task_models"]["sound_director"]
    system = load_prompt("sound_director.system.txt")
    user = load_prompt("sound_director.user.txt").format(
        density=density,
        editorial_plan=json.dumps(editorial, ensure_ascii=False, indent=2),
        choreography=json.dumps(choreography, ensure_ascii=False, indent=2),
        candidates=json.dumps(shortlisted, ensure_ascii=False, indent=2),
    )
    prompt_dir = paths.prompts / "sound"
    response_dir = paths.responses / "sound"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "sound-director.txt").write_text(f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8")
    try:
        append_log(paths, f"Sound Director: selecting from local CC0 library with {selection['provider']}")
        raw = call_task_json(
            task="sound_director",
            selection=selection,
            system=system,
            user=user,
            cwd=paths.root,
            output_schema=load_schema("sound_plan.schema.json"),
        )
        write_json(response_dir / "sound-director.json", raw)
    except Exception as exc:
        raw = _fallback_plan(intents, shortlisted, density)
        write_json(response_dir / "sound-director-fallback.json", {"error": str(exc), "payload": raw})
        append_log(paths, f"Sound Director: deterministic shortlist fallback used ({exc})")

    plan = _normalise_plan(raw, sounds, library_root, editorial, density)
    write_json(paths.sound / "sound-plan.json", plan)
    if plan["enabled"] and plan["cues"]:
        _mix(paths, plan)
    append_log(paths, f"Sound Director: saved {len(plan['cues'])} restrained cues")
    artifacts = ["sound/sound-plan.json", "sound/library-validation.json", "prompts/sound/", "responses/sound/"]
    if (paths.sound / "final-audio.wav").exists():
        artifacts.append("sound/final-audio.wav")
    return {"artifacts": artifacts, "summary": {"enabled": plan["enabled"], "cues": len(plan["cues"]), "validation": validation}}


def _library_root() -> Path:
    configured = os.getenv("SFX_LIBRARY_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_ROOT.parent / "ai_sound_effects_library").resolve()


def _validate_library(root: Path, sounds: list[dict[str, Any]]) -> dict[str, Any]:
    missing = []
    readable = 0
    for item in sounds:
        relative = str(item.get("path") or "")
        path = root / relative
        if not relative or not path.exists():
            missing.append(relative or str(item.get("id") or "unknown"))
        else:
            readable += 1
    return {"manifest_sounds": len(sounds), "readable_files": readable, "missing_files": missing}


def _scene_intents(editorial: dict[str, Any], choreography: dict[str, Any]) -> list[dict[str, Any]]:
    choreo = {item.get("scene_id"): item for item in choreography.get("cues", [])}
    intents: list[dict[str, Any]] = []
    for scene in editorial.get("scenes", []):
        scene_id = str(scene["scene_id"])
        cue = choreo.get(scene_id, {})
        values = list(scene.get("sound_intent") or [])
        if cue.get("transition_in") not in {None, "direct_cut", "soft_crossfade"}:
            values.append(f"{cue['transition_in']} visual transition")
        if cue.get("emphasis_preset") not in {None, "none"}:
            values.append(f"{cue['emphasis_preset']} concept emphasis")
        if scene.get("composition_mode") == "full_broll":
            values.append("full screen b-roll reveal")
        for offset, intent in enumerate(dict.fromkeys(str(item) for item in values if str(item).strip())):
            intents.append({
                "scene_id": scene_id,
                "time": round(float(scene["start"]) + 0.06 + offset * 0.12, 3),
                "intent": intent,
                "energy": float(scene.get("energy", 0.5)),
            })
    return intents


def _shortlist(intents: list[dict[str, Any]], sounds: list[dict[str, Any]], *, top_n: int) -> list[dict[str, Any]]:
    results = []
    for intent in intents:
        query_tokens = _tokens(intent["intent"])
        ranked = []
        for sound in sounds:
            text = str(sound.get("search_text") or " ".join([
                str(sound.get("title") or ""), str(sound.get("category") or ""),
                " ".join(sound.get("tags") or []), " ".join(sound.get("best_for") or []),
            ])).lower()
            overlap = len(query_tokens & _tokens(text))
            intensity = float(sound.get("intensity") or 0.4)
            intensity_match = 1 - abs(intensity - float(intent.get("energy", 0.5)))
            category_bonus = 2.0 if str(sound.get("category")) in {"transitions", "interface", "documentary", "cinematic", "impacts"} else 0.0
            score = overlap * 3.0 + intensity_match + category_bonus
            ranked.append((score, sound))
        ranked.sort(key=lambda item: item[0], reverse=True)
        candidates = []
        for score, sound in ranked[:top_n]:
            candidates.append({
                "id": sound.get("id"), "path": sound.get("path"), "title": sound.get("title"),
                "category": sound.get("category"), "tags": sound.get("tags"),
                "description": sound.get("description"), "best_for": sound.get("best_for"),
                "avoid_for": sound.get("avoid_for"), "intensity": sound.get("intensity"),
                "duration_seconds": (sound.get("technical") or {}).get("duration_seconds"),
                "score": round(score, 3),
            })
        results.append({**intent, "candidates": candidates})
    return results


def _fallback_plan(intents: list[dict[str, Any]], shortlisted: list[dict[str, Any]], density: str) -> dict[str, Any]:
    limit = {"low": 4, "medium": 8, "high": 12}.get(density, 8)
    cues = []
    for index, item in enumerate(shortlisted[:limit]):
        candidates = item.get("candidates") or []
        if not candidates:
            continue
        sound = candidates[0]
        cues.append({
            "time": item["time"], "scene_id": item["scene_id"], "intent": item["intent"],
            "sound_id": sound["id"], "gain_db": -19 if density != "high" else -17,
            "trim_start": 0, "duration": min(float(sound.get("duration_seconds") or 0.6), 2.0),
            "fade_in_ms": 8, "fade_out_ms": 80, "duck_under_speech_db": 3,
            "enabled": True, "reason": "Best local semantic shortlist match",
        })
    return {"enabled": bool(cues), "density": density, "cues": cues}


def _normalise_plan(raw: dict[str, Any], sounds: list[dict[str, Any]], root: Path, editorial: dict[str, Any], density: str) -> dict[str, Any]:
    by_id = {str(item.get("id")): item for item in sounds}
    duration = float(editorial.get("duration_seconds") or 0)
    max_count = max(1, math.ceil(duration / 30 * {"low": 4, "medium": 8, "high": 12}.get(density, 8)))
    cues = []
    seen_close: list[float] = []
    for index, raw_cue in enumerate(raw.get("cues") or []):
        sound_id = str(raw_cue.get("sound_id") or "")
        sound = by_id.get(sound_id)
        if not sound:
            continue
        time_value = max(0.0, min(float(raw_cue.get("time") or 0), max(duration, 0)))
        if any(abs(time_value - prior) < 0.18 for prior in seen_close):
            continue
        relative = str(sound.get("path") or "")
        resolved = root / relative
        if not resolved.exists():
            continue
        technical_duration = float((sound.get("technical") or {}).get("duration_seconds") or 0.6)
        cues.append({
            "cue_id": f"sfx_{len(cues)+1:03d}",
            "time": round(time_value, 3),
            "scene_id": str(raw_cue.get("scene_id") or ""),
            "intent": str(raw_cue.get("intent") or "editorial emphasis"),
            "sound_id": sound_id,
            "path": relative,
            "resolved_path": str(resolved),
            "gain_db": max(-40.0, min(float(raw_cue.get("gain_db", -18)), -3.0)),
            "trim_start": max(0.0, float(raw_cue.get("trim_start", 0))),
            "duration": max(0.05, min(float(raw_cue.get("duration", technical_duration)), technical_duration, 8.0)),
            "fade_in_ms": max(0, min(int(raw_cue.get("fade_in_ms", 8)), 1000)),
            "fade_out_ms": max(0, min(int(raw_cue.get("fade_out_ms", 80)), 2000)),
            "duck_under_speech_db": max(0.0, min(float(raw_cue.get("duck_under_speech_db", 3)), 12.0)),
            "enabled": bool(raw_cue.get("enabled", True)),
            "reason": str(raw_cue.get("reason") or "Selected by Sound Director"),
        })
        seen_close.append(time_value)
        if len(cues) >= max_count:
            break
    return {
        "version": "2.0", "enabled": bool(raw.get("enabled", True)) and bool(cues),
        "density": density, "library_root": str(root), "cues": cues, "warnings": [],
    }


def _mix(paths, plan: dict[str, Any]) -> None:
    voice = paths.source / "speech.wav"
    if not voice.exists():
        raise RuntimeError("Clean speech WAV is missing")
    enabled = [item for item in plan.get("cues", []) if item.get("enabled")]
    if not enabled:
        return
    args: list[str] = ["-i", str(voice)]
    for cue in enabled:
        args.extend(["-i", str(cue["resolved_path"])])
    filters = ["[0:a]aresample=48000,volume=1.0[voice]"]
    mix_labels = ["[voice]"]
    for index, cue in enumerate(enabled, start=1):
        delay = max(0, round(float(cue["time"]) * 1000))
        duration = float(cue["duration"])
        fade_out_start = max(0.0, duration - float(cue["fade_out_ms"]) / 1000)
        label = f"sfx{index}"
        filters.append(
            f"[{index}:a]aresample=48000,atrim=start={float(cue['trim_start']):.3f}:duration={duration:.3f},"
            f"asetpts=PTS-STARTPTS,volume={float(cue['gain_db']):.2f}dB,"
            f"afade=t=in:st=0:d={float(cue['fade_in_ms'])/1000:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={float(cue['fade_out_ms'])/1000:.3f},"
            f"adelay={delay}|{delay}[{label}]"
        )
        mix_labels.append(f"[{label}]")
    filters.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,alimiter=limit=0.95[outa]")
    output = paths.sound / "final-audio.wav"
    run_ffmpeg([
        *args, "-filter_complex", ";".join(filters), "-map", "[outa]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output),
    ], timeout=3600, paths=paths, label="Sound mix")


def _tokens(value: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9_]+", value.lower()) if len(item) > 1}
