from __future__ import annotations

import json
import math
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run, save_run
from ..providers.elevenlabs_stt import _group_captions, _group_words, _write_srt
from ..providers.registry import call_task_json
from .common import ffprobe, load_prompt, load_schema, run_ffmpeg

CLEANUP_MODES = {"off", "conservative", "balanced", "tight"}
RESOLVED_STATUSES = {"approved", "kept"}
FILLERS = {
    "അപ്പോ", "അപ്പൊ", "അതായത്", "പിന്നെ", "എന്നിട്ട്", "actually", "basically",
    "okay", "ok", "so", "like", "ഉം", "ആ",
}
PROTECTED_TERMS = {
    "not", "no", "never", "avoid", "must", "should", "cannot", "don't", "do not",
    "അല്ല", "ഇല്ല", "വേണ്ട", "ചെയ്യരുത്", "പാടില്ല", "ഒഴിവാക്കുക", "നിർബന്ധം",
}


def run_analysis(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    mode = str(meta["settings"].get("dialogue_cleanup_mode") or "balanced").lower()
    if mode not in CLEANUP_MODES:
        mode = "balanced"
    transcript = _source_transcript(paths)
    if not transcript:
        raise RuntimeError("Transcript is required before dialogue cleanup")

    paths.dialogue.mkdir(parents=True, exist_ok=True)
    deterministic = _detect_candidates(transcript, mode)
    payload = {"summary": f"Deterministic {mode} dialogue cleanup", "edits": deterministic}
    if mode != "off":
        selection = meta["settings"]["task_models"]["dialogue_editor"]
        system = load_prompt("dialogue_editor.system.txt")
        user = load_prompt("dialogue_editor.user.txt").format(
            mode=mode,
            transcript=json.dumps(_compact_transcript(transcript), ensure_ascii=False, indent=2),
            candidates=json.dumps(deterministic, ensure_ascii=False, indent=2),
        )
        prompt_dir = paths.prompts / "dialogue"
        response_dir = paths.responses / "dialogue"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        response_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "dialogue-analysis.txt").write_text(
            f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8"
        )
        try:
            append_log(paths, f"Dialogue cleanup: requesting review from {selection['provider']} ({selection['model']})")
            payload = call_task_json(
                task="dialogue_editor",
                selection=selection,
                system=system,
                user=user,
                cwd=paths.root,
                output_schema=load_schema("dialogue_edit_plan.schema.json"),
            )
            write_json(response_dir / "dialogue-analysis.json", payload)
        except Exception as exc:
            append_log(paths, f"Dialogue cleanup: provider unavailable; using deterministic candidates ({exc})")
            write_json(response_dir / "dialogue-analysis-fallback.json", {"error": str(exc), "payload": payload})

    plan = _normalise_plan(payload, transcript, mode)
    write_json(paths.dialogue / "edit-plan.json", plan)
    unresolved = sum(1 for edit in plan["edits"] if edit["status"] == "suggested")
    append_log(paths, f"Dialogue cleanup: saved {len(plan['edits'])} candidates; {unresolved} require review")
    return {
        "artifacts": ["dialogue/edit-plan.json", "prompts/dialogue/", "responses/dialogue/"],
        "summary": {"mode": mode, "candidates": len(plan["edits"]), "unresolved": unresolved},
    }


def run_clean_master(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.dialogue / "edit-plan.json", {"edits": []}) or {"edits": []}
    unresolved = [edit["edit_id"] for edit in plan.get("edits", []) if edit.get("status") not in RESOLVED_STATUSES]
    if unresolved:
        raise RuntimeError("Resolve every dialogue edit before creating the clean master: " + ", ".join(unresolved))

    _backup_source_artifacts(paths)
    source_master = paths.dialogue / "source-master.mp4"
    source_meta = read_json(paths.dialogue / "source-metadata.json", {})
    source_transcript = read_json(paths.dialogue / "source-transcript.json", {})
    duration = float(source_meta.get("duration_seconds") or source_transcript.get("duration_seconds") or 0)
    if duration <= 0:
        raise RuntimeError("Source duration is unavailable")

    removals = _approved_removals(plan.get("edits", []), duration)
    keep_ranges = _complement_ranges(removals, duration)
    if not keep_ranges:
        raise RuntimeError("Dialogue cleanup would remove the entire recording")
    crossfade = max(0.0, min(float(meta["settings"].get("dialogue_crossfade_ms") or 25) / 1000.0, 0.08))
    if len(keep_ranges) > 1:
        crossfade = min(crossfade, min(end - start for start, end in keep_ranges) / 4)
    else:
        crossfade = 0.0

    master = paths.source / "master.mp4"
    if removals:
        append_log(paths, f"Clean master: removing {len(removals)} approved ranges with {crossfade * 1000:.0f} ms continuity fades")
        run_ffmpeg(
            _clean_master_args(
                source_master,
                keep_ranges,
                crossfade,
                master,
                fps=float(source_meta.get("fps") or 30),
            ),
            paths=paths,
            label="Render clean talking-head master",
            duration=duration,
            timeout=14400,
        )
    else:
        shutil.copy2(source_master, master)
        append_log(paths, "Clean master: no approved removals; canonical master copied unchanged")

    clean_probe = ffprobe(master)
    clean_duration = float((clean_probe.get("format") or {}).get("duration") or 0)
    run_ffmpeg(
        [
            "-i", str(master), "-vf", "scale=540:-2:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(paths.source / "proxy.mp4"),
        ],
        paths=paths,
        label="Create clean browser proxy",
        duration=clean_duration,
    )
    run_ffmpeg(
        ["-i", str(master), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(paths.source / "speech.wav")],
        paths=paths,
        label="Extract clean speech audio",
        duration=clean_duration,
    )

    mapping = _timeline_map(keep_ranges, crossfade)
    clean_transcript = _remap_transcript(source_transcript, mapping, removals, clean_duration)
    write_json(paths.transcript / "transcript.json", clean_transcript)
    _write_srt(paths.transcript / "captions.srt", clean_transcript.get("captions") or [])
    metadata = dict(source_meta)
    video_stream = next((item for item in clean_probe.get("streams", []) if item.get("codec_type") == "video"), {})
    metadata.update({
        "duration_seconds": clean_duration,
        "width": int(video_stream.get("width") or metadata.get("width") or 0),
        "height": int(video_stream.get("height") or metadata.get("height") or 0),
        "master_probe": clean_probe,
        "dialogue_cleaned": bool(removals),
        "source_duration_seconds": duration,
        "removed_seconds": round(duration - clean_duration, 3),
    })
    write_json(paths.source / "metadata.json", metadata)
    timeline_payload = {
        "version": "1.0",
        "source_duration": duration,
        "clean_duration": clean_duration,
        "crossfade_seconds": crossfade,
        "removed_ranges": [{"start": start, "end": end} for start, end in removals],
        "ranges": mapping,
    }
    write_json(paths.dialogue / "source-to-clean-map.json", timeline_payload)
    continuity = _continuity_plan(removals, mapping, crossfade)
    write_json(paths.dialogue / "continuity-plan.json", continuity)
    meta.setdefault("approvals", {})["dialogue"] = True
    save_run(meta)
    append_log(paths, f"Clean master: {duration:.2f}s → {clean_duration:.2f}s")
    return {
        "artifacts": [
            "source/master.mp4", "source/proxy.mp4", "source/speech.wav", "source/metadata.json",
            "transcript/transcript.json", "transcript/captions.srt",
            "dialogue/source-to-clean-map.json", "dialogue/continuity-plan.json",
        ],
        "summary": {
            "source_duration": duration,
            "clean_duration": clean_duration,
            "removed_seconds": round(duration - clean_duration, 3),
            "cuts": len(removals),
        },
    }


def update_edit(run_id: str, edit_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.dialogue / "edit-plan.json")
    if not plan:
        raise FileNotFoundError("Dialogue edit plan is missing")
    edit = next((item for item in plan.get("edits", []) if item.get("edit_id") == edit_id), None)
    if not edit:
        raise KeyError(edit_id)
    for key in ("start", "end", "recommended_action", "target_pause_seconds", "reason"):
        if key in updates:
            edit[key] = updates[key]
    edit["start"] = max(0.0, float(edit["start"]))
    edit["end"] = max(edit["start"] + 0.03, float(edit["end"]))
    if edit.get("recommended_action") not in {"remove", "shorten_pause", "keep", "manual_review"}:
        raise ValueError("Unknown dialogue action")
    write_json(paths.dialogue / "edit-plan.json", plan)
    return edit


def create_manual_removal(
    run_id: str,
    *,
    start: float,
    end: float,
    transcript: str = "",
    reason: str = "Removed manually by the operator",
) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.dialogue / "edit-plan.json")
    if not plan:
        raise FileNotFoundError("Dialogue edit plan is missing")
    duration = float(plan.get("source_duration") or 0)
    start = max(0.0, float(start))
    end = min(duration, float(end)) if duration > 0 else float(end)
    if end - start < 0.03:
        raise ValueError("Manual removal must be at least 0.03 seconds")
    if not transcript.strip():
        transcript = _transcript_excerpt(_source_transcript(paths), start, end)
    existing_ids = {
        int(match.group(1))
        for item in plan.get("edits", [])
        if (match := re.fullmatch(r"edit_(\d+)", str(item.get("edit_id") or "")))
    }
    next_number = max(existing_ids, default=0) + 1
    edit = {
        "edit_id": f"edit_{next_number:04d}",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "category": "manual",
        "transcript": transcript.strip() or "[operator-selected range]",
        "recommended_action": "remove",
        "target_pause_seconds": None,
        "confidence": 1.0,
        "reason": reason.strip() or "Removed manually by the operator",
        "medical_risk": "none",
        "requires_review": False,
        "status": "approved",
        "resolved_action": "remove",
    }
    plan.setdefault("edits", []).append(edit)
    plan["edits"].sort(key=lambda item: (float(item["start"]), float(item["end"])))
    write_json(paths.dialogue / "edit-plan.json", plan)
    return edit


def resolve_edit(run_id: str, edit_id: str, action: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.dialogue / "edit-plan.json")
    if not plan:
        raise FileNotFoundError("Dialogue edit plan is missing")
    edit = next((item for item in plan.get("edits", []) if item.get("edit_id") == edit_id), None)
    if not edit:
        raise KeyError(edit_id)
    if action == "approve_recommendation":
        if edit.get("recommended_action") in {"keep", "manual_review"}:
            edit["status"] = "kept"
            edit["resolved_action"] = "keep"
        else:
            edit["status"] = "approved"
            edit["resolved_action"] = edit.get("recommended_action")
    elif action == "remove":
        edit["status"] = "approved"
        edit["resolved_action"] = "remove"
    elif action == "shorten_pause":
        edit["status"] = "approved"
        edit["resolved_action"] = "shorten_pause"
    elif action == "keep":
        edit["status"] = "kept"
        edit["resolved_action"] = "keep"
    elif action == "reset":
        edit["status"] = "suggested"
        edit["resolved_action"] = None
    else:
        raise ValueError(f"Unknown dialogue edit action: {action}")
    write_json(paths.dialogue / "edit-plan.json", plan)
    return edit


def approve_safe(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.dialogue / "edit-plan.json")
    if not plan:
        raise FileNotFoundError("Dialogue edit plan is missing")
    changed = 0
    for edit in plan.get("edits", []):
        if edit.get("status") != "suggested" or edit.get("requires_review"):
            continue
        if float(edit.get("confidence") or 0) < 0.82:
            continue
        recommended = edit.get("recommended_action")
        if recommended in {"remove", "shorten_pause"}:
            edit["status"] = "approved"
            edit["resolved_action"] = recommended
        else:
            edit["status"] = "kept"
            edit["resolved_action"] = "keep"
        changed += 1
    write_json(paths.dialogue / "edit-plan.json", plan)
    return {"approved": changed, "plan": plan}


def _source_transcript(paths) -> dict[str, Any]:
    backup = read_json(paths.dialogue / "source-transcript.json")
    return backup or read_json(paths.transcript / "transcript.json", {}) or {}


def _compact_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": transcript.get("duration_seconds"),
        "language_code": transcript.get("language_code"),
        "phrases": transcript.get("phrases"),
        "words": transcript.get("words"),
    }


def _transcript_excerpt(transcript: dict[str, Any], start: float, end: float) -> str:
    selected = []
    for word in transcript.get("words") or []:
        word_start = float(word.get("start") or 0)
        word_end = float(word.get("end") or word_start)
        if word_end > start and word_start < end:
            token = str(word.get("word") or "").strip()
            if token:
                selected.append(token)
    return " ".join(selected)


def _detect_candidates(transcript: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    if mode == "off":
        return []
    words = transcript.get("words") or []
    phrases = transcript.get("phrases") or []
    candidates: list[dict[str, Any]] = []
    pause_threshold = {"conservative": 1.2, "balanced": 0.85, "tight": 0.62}.get(mode, 0.85)
    target_pause = {"conservative": 0.55, "balanced": 0.38, "tight": 0.26}.get(mode, 0.38)

    for index, word in enumerate(words):
        token = _normalise_token(word.get("word", ""))
        previous = words[index - 1] if index else None
        if previous:
            gap = float(word["start"]) - float(previous["end"])
            if gap >= pause_threshold:
                candidates.append({
                    "start": float(previous["end"]), "end": float(word["start"]),
                    "category": "long_pause", "transcript": "[pause]",
                    "recommended_action": "shorten_pause", "target_pause_seconds": target_pause,
                    "confidence": min(0.99, 0.72 + gap / 5),
                    "reason": f"Pause is {gap:.2f}s; target is {target_pause:.2f}s",
                    "requires_review": False, "medical_risk": "none",
                })
        if token in FILLERS and len(token) > 1:
            protected = _contains_protected(str(word.get("word") or ""))
            candidates.append({
                "start": float(word["start"]), "end": float(word["end"]),
                "category": "filler", "transcript": str(word.get("word") or ""),
                "recommended_action": "remove", "target_pause_seconds": None,
                "confidence": 0.84 if mode != "conservative" else 0.72,
                "reason": "Candidate discourse filler; remove only if the surrounding sentence remains natural",
                "requires_review": protected or mode == "conservative", "medical_risk": "low",
            })
        if previous and token and token == _normalise_token(previous.get("word", "")):
            candidates.append({
                "start": float(previous["start"]), "end": float(previous["end"]),
                "category": "repeated_word", "transcript": str(previous.get("word", "")),
                "recommended_action": "remove", "target_pause_seconds": None,
                "confidence": 0.94, "reason": "Immediate repeated word or restart",
                "requires_review": _contains_protected(token), "medical_risk": "medium" if _contains_protected(token) else "low",
            })

    for index in range(1, len(phrases)):
        left, right = phrases[index - 1], phrases[index]
        left_text = str(left.get("text") or "").strip()
        right_text = str(right.get("text") or "").strip()
        similarity = SequenceMatcher(None, _normalise_text(left_text), _normalise_text(right_text)).ratio()
        if similarity >= (0.82 if mode == "tight" else 0.88):
            candidates.append({
                "start": float(left["start"]), "end": float(left["end"]),
                "category": "repeated_sentence", "transcript": left_text,
                "recommended_action": "remove", "target_pause_seconds": None,
                "confidence": round(similarity, 3),
                "reason": "Adjacent sentence is substantially repeated by the following take",
                "requires_review": True, "medical_risk": "high" if _contains_protected(left_text) else "medium",
            })
        else:
            left_norm, right_norm = _normalise_text(left_text), _normalise_text(right_text)
            left_tokens, right_tokens = left_norm.split(), right_norm.split()
            shared_opening = len(left_tokens) >= 2 and right_tokens[:2] == left_tokens[:2]
            if shared_opening and len(left_tokens) <= max(5, len(right_tokens) // 2):
                candidates.append({
                    "start": float(left["start"]), "end": float(left["end"]),
                    "category": "false_start", "transcript": left_text,
                    "recommended_action": "remove", "target_pause_seconds": None,
                    "confidence": 0.78,
                    "reason": "Short phrase appears to restart as the following, more complete take",
                    "requires_review": True, "medical_risk": "high" if _contains_protected(left_text) else "medium",
                })

    return _dedupe_candidates(candidates)


def _normalise_plan(payload: dict[str, Any], transcript: dict[str, Any], mode: str) -> dict[str, Any]:
    duration = float(transcript.get("duration_seconds") or 0)
    edits = []
    for raw in payload.get("edits") or []:
        try:
            start = max(0.0, min(float(raw.get("start", 0)), duration))
            end = max(start + 0.03, min(float(raw.get("end", start + 0.1)), duration))
        except (TypeError, ValueError):
            continue
        action = str(raw.get("recommended_action") or "manual_review")
        if action not in {"remove", "shorten_pause", "keep", "manual_review"}:
            action = "manual_review"
        text = str(raw.get("transcript") or "").strip()
        protected = _contains_protected(text)
        edits.append({
            "edit_id": f"edit_{len(edits) + 1:04d}",
            "start": round(start, 3), "end": round(end, 3),
            "duration": round(end - start, 3),
            "category": str(raw.get("category") or "other"),
            "transcript": text,
            "recommended_action": action,
            "target_pause_seconds": (
                max(0.08, min(float(raw.get("target_pause_seconds") or 0.35), end - start))
                if action == "shorten_pause" else None
            ),
            "confidence": max(0.0, min(float(raw.get("confidence") or 0.5), 1.0)),
            "reason": str(raw.get("reason") or "Review proposed dialogue cleanup"),
            "medical_risk": str(raw.get("medical_risk") or ("high" if protected else "low")),
            "requires_review": bool(raw.get("requires_review", False) or protected or action == "manual_review"),
            "status": "suggested",
            "resolved_action": None,
        })
    edits.sort(key=lambda item: (item["start"], item["end"]))
    return {
        "version": "1.0", "mode": mode,
        "source_duration": duration,
        "summary": str(payload.get("summary") or f"{mode.title()} dialogue cleanup"),
        "edits": edits,
    }


def _backup_source_artifacts(paths) -> None:
    paths.dialogue.mkdir(parents=True, exist_ok=True)
    pairs = [
        (paths.source / "master.mp4", paths.dialogue / "source-master.mp4"),
        (paths.source / "proxy.mp4", paths.dialogue / "source-proxy.mp4"),
        (paths.source / "speech.wav", paths.dialogue / "source-speech.wav"),
        (paths.source / "metadata.json", paths.dialogue / "source-metadata.json"),
        (paths.transcript / "transcript.json", paths.dialogue / "source-transcript.json"),
        (paths.transcript / "captions.srt", paths.dialogue / "source-captions.srt"),
    ]
    for source, destination in pairs:
        if destination.exists():
            continue
        if not source.exists():
            raise FileNotFoundError(f"Required source artifact is missing: {source}")
        shutil.copy2(source, destination)


def _approved_removals(edits: list[dict[str, Any]], duration: float) -> list[tuple[float, float]]:
    ranges = []
    for edit in edits:
        if edit.get("status") != "approved":
            continue
        action = edit.get("resolved_action") or edit.get("recommended_action")
        start, end = max(0.0, float(edit["start"])), min(duration, float(edit["end"]))
        if action == "shorten_pause":
            target = max(0.08, min(float(edit.get("target_pause_seconds") or 0.35), end - start))
            start = min(end, start + target)
        if action == "remove" or action == "shorten_pause":
            if end - start >= 0.02:
                ranges.append((start, end))
    return _merge_ranges(ranges)


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 0.015:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 6), round(end, 6)) for start, end in merged]


def _complement_ranges(removals: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    keep = []
    cursor = 0.0
    for start, end in removals:
        if start - cursor >= 0.08:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= 0.08:
        keep.append((cursor, duration))
    return keep


def _clean_master_args(
    source: Path,
    keep_ranges: list[tuple[float, float]],
    crossfade: float,
    output: Path,
    *,
    fps: float = 30,
) -> list[str]:
    filters: list[str] = []
    stable_fps = max(1.0, fps)
    branch_count = len(keep_ranges)
    video_inputs = "".join(f"[vin{index}]" for index in range(branch_count))
    audio_inputs = "".join(f"[ain{index}]" for index in range(branch_count))
    filters.append(f"[0:v]split={branch_count}{video_inputs}")
    filters.append(f"[0:a]asplit={branch_count}{audio_inputs}")
    for index, (start, end) in enumerate(keep_ranges):
        # The audio overlap determines the clean timeline. Drop the equivalent
        # amount from each preceding video branch so its hard cuts stay in sync.
        # A 25 ms video dissolve is shorter than one frame at the studio's 30 fps,
        # while chained xfade outputs lose their frame-rate metadata in FFmpeg 7.
        video_end = end - (crossfade if index < branch_count - 1 else 0.0)
        filters.append(
            f"[vin{index}]trim=start={start:.6f}:end={video_end:.6f},"
            f"fps={stable_fps:g},settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
        )
        filters.append(
            f"[ain{index}]atrim=start={start:.6f}:end={end:.6f},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
    if len(keep_ranges) == 1:
        video_label, audio_label = "[v0]", "[a0]"
    else:
        inputs = "".join(f"[v{index}][a{index}]" for index in range(len(keep_ranges)))
        if crossfade <= 0:
            filters.append(f"{inputs}concat=n={len(keep_ranges)}:v=1:a=1[vcat][acat]")
            video_label, audio_label = "[vcat]", "[acat]"
        else:
            video_only_inputs = "".join(f"[v{index}]" for index in range(len(keep_ranges)))
            filters.append(f"{video_only_inputs}concat=n={len(keep_ranges)}:v=1:a=0[vcat]")
            audio_label = "[a0]"
            for index in range(1, len(keep_ranges)):
                next_audio = f"[ax{index}]"
                filters.append(
                    f"{audio_label}[a{index}]acrossfade=d={crossfade:.6f}:c1=tri:c2=tri{next_audio}"
                )
                audio_label = next_audio
            video_label = "[vcat]"
    return [
        "-i", str(source), "-filter_complex", ";".join(filters),
        "-map", video_label, "-map", audio_label,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(output),
    ]


def _timeline_map(keep_ranges: list[tuple[float, float]], crossfade: float) -> list[dict[str, float]]:
    mapping = []
    clean_cursor = 0.0
    for index, (start, end) in enumerate(keep_ranges):
        duration = end - start
        clean_start = clean_cursor
        clean_end = clean_start + duration
        mapping.append({
            "source_start": round(start, 6), "source_end": round(end, 6),
            "clean_start": round(clean_start, 6), "clean_end": round(clean_end, 6),
        })
        clean_cursor = clean_end - (crossfade if index < len(keep_ranges) - 1 else 0.0)
    return mapping


def _remap_transcript(
    transcript: dict[str, Any], mapping: list[dict[str, float]], removals: list[tuple[float, float]], clean_duration: float
) -> dict[str, Any]:
    words = []
    for source_word in transcript.get("words") or []:
        start, end = float(source_word["start"]), float(source_word["end"])
        midpoint = (start + end) / 2
        if any(remove_start <= midpoint < remove_end for remove_start, remove_end in removals):
            continue
        target = next((item for item in mapping if item["source_start"] <= midpoint <= item["source_end"]), None)
        if not target:
            continue
        mapped_start = target["clean_start"] + max(0.0, start - target["source_start"])
        mapped_end = target["clean_start"] + max(0.0, end - target["source_start"])
        words.append({
            "id": f"word_{len(words) + 1:05d}",
            "source_word_id": source_word.get("id"),
            "word": source_word.get("word", ""),
            "start": round(max(0.0, mapped_start), 3),
            "end": round(min(clean_duration, max(mapped_start + 0.01, mapped_end)), 3),
        })
    phrases = _group_words(words)
    captions = _group_captions(words)
    return {
        **{key: value for key, value in transcript.items() if key not in {"words", "phrases", "captions", "transcript", "duration_seconds"}},
        "version": "1.1",
        "duration_seconds": round(clean_duration, 3),
        "transcript": " ".join(str(word["word"]) for word in words),
        "words": words,
        "phrases": phrases,
        "captions": captions,
        "dialogue_cleaned": True,
    }


def _continuity_plan(
    removals: list[tuple[float, float]], mapping: list[dict[str, float]], crossfade: float
) -> dict[str, Any]:
    cuts = []
    for index, (start, end) in enumerate(removals, start=1):
        left = next((item for item in mapping if math.isclose(item["source_end"], start, abs_tol=0.02)), None)
        clean_time = float(left["clean_end"]) if left else 0.0
        removed = end - start
        concealment = "direct_cut" if removed <= 0.35 else ("emphasis_punch" if removed <= 1.2 else "broll_bridge")
        cuts.append({
            "cut_id": f"cut_{index:04d}",
            "source_left_end": start, "source_right_start": end,
            "clean_time": round(clean_time, 3), "removed_seconds": round(removed, 3),
            "recommended_concealment": concealment,
            "fallback": "subtle_punch_in",
            "crossfade_ms": round(crossfade * 1000),
        })
    return {"version": "1.0", "cuts": cuts}


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for candidate in sorted(candidates, key=lambda item: (item["start"], item["end"], item["category"])):
        if any(
            abs(candidate["start"] - existing["start"]) < 0.04
            and abs(candidate["end"] - existing["end"]) < 0.04
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def _normalise_token(value: str) -> str:
    return re.sub(r"[^\w\u0D00-\u0D7F]+", "", str(value).lower())


def _normalise_text(value: str) -> str:
    return " ".join(_normalise_token(part) for part in str(value).split() if _normalise_token(part))


def _contains_protected(value: str) -> bool:
    lowered = str(value).lower()
    return any(term in lowered for term in PROTECTED_TERMS)
