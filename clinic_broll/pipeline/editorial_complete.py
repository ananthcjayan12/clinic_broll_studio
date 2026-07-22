from __future__ import annotations

from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from . import editorial
from .editorial_policy import budget_report


def run(run_id: str) -> dict[str, Any]:
    result = editorial.run(run_id)
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    transcript = read_json(paths.transcript / "transcript.json", {})
    bible = read_json(paths.editorial / "visual-bible.json", {})
    duration = float(plan.get("duration_seconds") or transcript.get("duration_seconds") or 0)
    fps = int(plan.get("fps") or meta["settings"].get("fps") or 30)
    filled = fill_timeline(plan.get("scenes") or [], transcript.get("phrases") or [], duration, fps)
    hidden = sum(1 for scene in filled if not scene.get("operator_visible", True))
    if len(filled) != len(plan.get("scenes") or []):
        append_log(paths, f"Editorial continuity: inserted {hidden} hidden talking-head continuity ranges")
    plan["scenes"] = filled
    plan["continuous_coverage"] = True
    plan["budget_report"] = budget_report(filled, duration, meta["settings"])
    write_json(paths.editorial / "editorial-plan.json", plan)
    compatibility = editorial.editorial_to_broll_plan(plan, bible)
    write_json(paths.plan / "broll_plan.json", compatibility)
    result["summary"] = {
        **(result.get("summary") or {}),
        "scenes": len([scene for scene in filled if scene.get("operator_visible", True)]),
        "hidden_continuity_ranges": hidden,
        "continuous_coverage": True,
        "budget_report": plan["budget_report"],
    }
    return result


def fill_timeline(
    scenes: list[dict[str, Any]],
    phrases: list[dict[str, Any]],
    duration: float,
    fps: int,
    *,
    tolerance: float = 0.04,
) -> list[dict[str, Any]]:
    if duration <= 0:
        return scenes
    ordered = sorted(scenes, key=lambda item: float(item.get("start") or 0))
    filled: list[dict[str, Any]] = []
    cursor = 0.0
    for scene in ordered:
        start = max(cursor, min(float(scene.get("start") or 0), duration))
        end = min(duration, max(start, float(scene.get("end") or start)))
        if start - cursor > tolerance:
            filled.append(_talking_head_gap(cursor, start, phrases, fps))
        if end - start > tolerance:
            adjusted = dict(scene)
            adjusted["start"] = round(start, 3)
            adjusted["end"] = round(end, 3)
            adjusted["duration"] = round(end - start, 3)
            adjusted["start_frame"] = round(start * fps)
            adjusted["end_frame"] = round(end * fps)
            adjusted.setdefault("operator_visible", True)
            filled.append(adjusted)
            cursor = end
    if duration - cursor > tolerance:
        filled.append(_talking_head_gap(cursor, duration, phrases, fps))
    for index, scene in enumerate(filled, start=1):
        scene["scene_id"] = f"scene_{index:03d}"
    return filled


def _talking_head_gap(start: float, end: float, phrases: list[dict[str, Any]], fps: int) -> dict[str, Any]:
    narration = " ".join(
        str(item.get("text") or "").strip()
        for item in phrases
        if float(item.get("end") or 0) > start and float(item.get("start") or 0) < end
    ).strip()
    return {
        "scene_id": "",
        "start": round(start, 3),
        "end": round(end, 3),
        "start_frame": round(start * fps),
        "end_frame": round(end * fps),
        "duration": round(end - start, 3),
        "narration": narration,
        "editorial_purpose": "Preserve natural delivery between designed editorial moments",
        "energy": 0.4,
        "concept_density": 0.25,
        "keep_eye_contact": True,
        "composition_mode": "talking_head",
        "layout_variant": "talking_head",
        "subject_mode": "original",
        "visual_style": "natural_lifestyle",
        "visual_strategy": "none",
        "visual_brief": "",
        "motion_brief": "",
        "text_overlay": "",
        "camera_move": "static",
        "transition_in": "direct_cut",
        "transition_out": "direct_cut",
        "emphasis_preset": "none",
        "caption_mode": "off",
        "sound_intent": [],
        "priority": "optional",
        "safety": ["Preserve the original talking-head video and narration"],
        "operator_visible": False,
    }
