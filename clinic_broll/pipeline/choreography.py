from __future__ import annotations

import json
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema

CAMERA_MOVES = {"static", "subtle_punch_in", "emphasis_punch", "slow_push", "micro_pull_back", "face_follow", "object_follow"}
TRANSITIONS = {"direct_cut", "soft_crossfade", "clean_push", "vertical_slide", "horizontal_swipe", "mask_reveal", "paper_reveal", "zoom_match", "blur_transition", "dip_to_white"}
EMPHASIS = {"none", "keyword_pop", "card_snap", "underline_draw", "number_count", "icon_bounce", "warning_pulse", "comparison_flip"}


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    editorial = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    reframe = read_json(paths.editorial / "reframe-plan.json", {"scenes": []})
    continuity = read_json(paths.dialogue / "continuity-plan.json", {})
    if not editorial.get("scenes"):
        raise RuntimeError("Editorial plan is missing")
    selection = meta["settings"]["task_models"]["edit_choreographer"]
    system = load_prompt("edit_choreographer.system.txt")
    user = load_prompt("edit_choreographer.user.txt").format(
        editorial_plan=json.dumps(editorial, ensure_ascii=False, indent=2),
        reframe_plan=json.dumps(reframe, ensure_ascii=False, indent=2),
        continuity=json.dumps(continuity, ensure_ascii=False, indent=2),
        settings=json.dumps({
            "profile": meta["settings"].get("editing_profile"),
            "intensity": meta["settings"].get("editing_intensity"),
        }, ensure_ascii=False, indent=2),
    )
    prompt_dir = paths.prompts / "choreography"
    response_dir = paths.responses / "choreography"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "edit-choreography.txt").write_text(f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8")
    try:
        append_log(paths, f"Edit Choreographer: requesting deterministic cues from {selection['provider']}")
        raw = call_task_json(
            task="edit_choreographer",
            selection=selection,
            system=system,
            user=user,
            cwd=paths.root,
            output_schema=load_schema("edit_choreography.schema.json"),
        )
        write_json(response_dir / "edit-choreography.json", raw)
    except Exception as exc:
        raw = _fallback(editorial, continuity, meta["settings"].get("editing_intensity", "medium"))
        write_json(response_dir / "edit-choreography-fallback.json", {"error": str(exc), "payload": raw})
        append_log(paths, f"Edit Choreographer: fallback presets used ({exc})")
    plan = normalize_choreography(raw, editorial)
    write_json(paths.editorial / "edit-choreography.json", plan)
    _merge_into_broll(paths, plan)
    append_log(paths, f"Edit Choreographer: saved {len(plan['cues'])} deterministic cues")
    return {
        "artifacts": ["editorial/edit-choreography.json", "plan/broll_plan.json", "prompts/choreography/", "responses/choreography/"],
        "summary": {"cues": len(plan["cues"])},
    }


def normalize_choreography(payload: dict[str, Any], editorial: dict[str, Any]) -> dict[str, Any]:
    scenes = {item["scene_id"]: item for item in editorial.get("scenes", [])}
    cues: list[dict[str, Any]] = []
    supplied = {str(item.get("scene_id")): item for item in payload.get("cues") or []}
    for scene_id, scene in scenes.items():
        raw = supplied.get(scene_id, {})
        camera = str(raw.get("camera_move") or scene.get("camera_move") or "static")
        transition_in = str(raw.get("transition_in") or scene.get("transition_in") or "direct_cut")
        transition_out = str(raw.get("transition_out") or scene.get("transition_out") or "direct_cut")
        emphasis = str(raw.get("emphasis_preset") or scene.get("emphasis_preset") or "none")
        if camera not in CAMERA_MOVES:
            camera = "static"
        if transition_in not in TRANSITIONS:
            transition_in = "direct_cut"
        if transition_out not in TRANSITIONS:
            transition_out = "direct_cut"
        if emphasis not in EMPHASIS:
            emphasis = "none"
        default_to = 1.0
        if camera == "subtle_punch_in":
            default_to = 1.065
        elif camera == "emphasis_punch":
            default_to = 1.1
        elif camera == "slow_push":
            default_to = 1.055
        elif camera == "micro_pull_back":
            default_to = 1.0
        from_scale = max(1.0, min(float(raw.get("from_scale", 1.0)), 1.15))
        to_scale = max(1.0, min(float(raw.get("to_scale", default_to)), 1.15))
        cues.append({
            "scene_id": scene_id,
            "start": scene["start"],
            "end": scene["end"],
            "camera_move": camera,
            "transition_in": transition_in,
            "transition_out": transition_out,
            "emphasis_preset": emphasis,
            "from_scale": round(from_scale, 4),
            "to_scale": round(to_scale, 4),
            "anchor": str(raw.get("anchor") or "face") if str(raw.get("anchor") or "face") in {"face", "center", "object"} else "face",
            "reason": str(raw.get("reason") or "Editorial scene preset"),
        })
    return {"version": "2.0", "summary": str(payload.get("summary") or "Deterministic V2 choreography"), "cues": cues}


def _merge_into_broll(paths, choreography: dict[str, Any]) -> None:
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    by_scene = {item["scene_id"]: item for item in choreography.get("cues", [])}
    for slot in plan.get("slots", []):
        cue = by_scene.get(slot.get("scene_id"))
        if cue:
            slot["choreography"] = cue
            slot["camera_move"] = cue["camera_move"]
            slot["transition_in"] = cue["transition_in"]
            slot["transition_out"] = cue["transition_out"]
            slot["emphasis_preset"] = cue["emphasis_preset"]
    write_json(paths.plan / "broll_plan.json", plan)


def _fallback(editorial: dict[str, Any], continuity: dict[str, Any], intensity: str) -> dict[str, Any]:
    cut_times = [float(item.get("clean_time") or item.get("time") or -1) for item in continuity.get("cuts", continuity.get("items", []))]
    cues = []
    for index, scene in enumerate(editorial.get("scenes", [])):
        start, end = float(scene["start"]), float(scene["end"])
        bridges_cut = any(start - 0.1 <= time <= end + 0.1 for time in cut_times)
        camera = str(scene.get("camera_move") or "static")
        if index == 0 and camera == "static":
            camera = "emphasis_punch"
        elif intensity != "low" and scene.get("keep_eye_contact") and camera == "static":
            camera = "subtle_punch_in"
        transition = "clean_push" if bridges_cut and scene.get("composition_mode") != "talking_head" else str(scene.get("transition_in") or "direct_cut")
        cues.append({
            "scene_id": scene["scene_id"],
            "start": start,
            "end": end,
            "camera_move": camera,
            "transition_in": transition,
            "transition_out": str(scene.get("transition_out") or "direct_cut"),
            "emphasis_preset": str(scene.get("emphasis_preset") or "none"),
            "from_scale": 1.0,
            "to_scale": 1.1 if camera == "emphasis_punch" else (1.065 if camera == "subtle_punch_in" else 1.0),
            "anchor": "face",
            "reason": "Fallback choreography aligned to scene emphasis and continuity cuts",
        })
    return {"summary": "Deterministic fallback choreography", "cues": cues}
