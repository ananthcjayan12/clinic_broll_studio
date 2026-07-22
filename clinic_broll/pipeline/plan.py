from __future__ import annotations

import json
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import load_run
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema

LAYOUTS = {
    "bottom_board", "top_board", "left_panel", "right_panel", "torn_split",
    "floating_cards", "full_frame", "broll_only", "split_top", "split_bottom",
    "split_left", "split_right", "picture_in_picture", "floating_visual",
}
LAYOUT_VARIANTS = {
    "talking_head", "broll_top_speaker_bottom", "speaker_top_broll_bottom",
    "speaker_left_broll_right", "broll_left_speaker_right", "picture_in_picture",
    "floating_visual", "full_broll", "layered_foreground",
}
COMPOSITION_MODES = {
    "talking_head", "split_layout", "full_broll", "layered_foreground",
    "picture_in_picture", "graphic_scene", "comparison", "montage",
}
SUBJECT_MODES = {"original", "cropped_original", "matte_foreground", "picture_in_picture", "hidden"}
CAPTION_POSITIONS = {"auto", "top", "bottom"}
CAMERA_MOVES = {"static", "subtle_punch_in", "emphasis_punch", "slow_push", "micro_pull_back", "face_follow", "object_follow"}
TRANSITIONS = {"direct_cut", "soft_crossfade", "clean_push", "vertical_slide", "horizontal_swipe", "mask_reveal", "paper_reveal", "zoom_match", "blur_transition", "dip_to_white"}

VARIANT_TO_LAYOUT = {
    "talking_head": "full_frame",
    "broll_top_speaker_bottom": "split_top",
    "speaker_top_broll_bottom": "split_bottom",
    "speaker_left_broll_right": "split_right",
    "broll_left_speaker_right": "split_left",
    "picture_in_picture": "picture_in_picture",
    "floating_visual": "floating_visual",
    "full_broll": "broll_only",
    "layered_foreground": "bottom_board",
}
LAYOUT_TO_VARIANT = {value: key for key, value in VARIANT_TO_LAYOUT.items()}
LAYOUT_TO_VARIANT.update({
    "top_board": "layered_foreground", "left_panel": "layered_foreground",
    "right_panel": "layered_foreground", "torn_split": "layered_foreground",
    "floating_cards": "floating_visual", "full_frame": "full_broll",
})


def run(run_id: str) -> dict[str, Any]:
    """Backward-compatible entry point; V2 planning is performed by the Editorial Director."""
    from .editorial import run as run_editorial

    return run_editorial(run_id)


def normalize_plan(payload: dict[str, Any], duration: float, *, fps: int = 30) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    previous_end = 0.0
    for raw in payload.get("slots") or []:
        if duration <= 0:
            break
        requested_start = float(raw.get("start", 0))
        requested_end = float(raw.get("end", requested_start + 4))
        start = max(previous_end, max(0.0, min(requested_start, duration)))
        end = min(duration, max(start + 0.5, requested_end))
        if end - start < 0.5:
            continue
        layout = str(raw.get("layout_template") or "bottom_board")
        if layout not in LAYOUTS:
            layout = "bottom_board"
        variant = str(raw.get("layout_variant") or LAYOUT_TO_VARIANT.get(layout, "layered_foreground"))
        if variant not in LAYOUT_VARIANTS:
            variant = "layered_foreground"
        composition = str(raw.get("composition_mode") or _composition_from_variant(variant))
        if composition not in COMPOSITION_MODES:
            composition = _composition_from_variant(variant)
        subject = str(raw.get("subject_mode") or _subject_from_variant(variant))
        if subject not in SUBJECT_MODES:
            subject = _subject_from_variant(variant)
        broll_only = layout == "broll_only" or variant == "full_broll"
        caption_position = str(raw.get("caption_position") or "auto")
        if caption_position not in CAPTION_POSITIONS:
            caption_position = "auto"
        slot_id = f"broll_{len(slots)+1:03d}"
        slots.append({
            "slot_id": slot_id,
            "scene_id": str(raw.get("scene_id") or f"scene_{len(slots)+1:03d}"),
            "start": round(start, 3),
            "end": round(end, 3),
            "start_frame": round(start * fps),
            "end_frame": round(end * fps),
            "duration": round(end - start, 3),
            "transcript": str(raw.get("transcript") or "").strip(),
            "purpose": str(raw.get("purpose") or "Visual support").strip(),
            "priority": raw.get("priority") if raw.get("priority") in {"essential", "useful", "optional"} else "useful",
            "composition_mode": composition,
            "layout_variant": variant,
            "subject_mode": subject,
            "layout_template": layout,
            "visual_type": str(raw.get("visual_type") or raw.get("visual_style") or "natural_lifestyle"),
            "visual_style": str(raw.get("visual_style") or raw.get("visual_type") or "natural_lifestyle"),
            "keep_subject_foreground": False if broll_only else bool(raw.get("keep_subject_foreground", subject == "matte_foreground")),
            "panel_region": 1.0 if broll_only else max(0.25, min(float(raw.get("panel_region", 0.44)), 0.72)),
            "show_caption": bool(raw.get("show_caption", False)),
            "caption_position": caption_position,
            "still_brief": str(raw.get("still_brief") or raw.get("purpose") or "").strip(),
            "motion_brief": str(raw.get("motion_brief") or "Subtle stable motion only").strip(),
            "text_overlay": str(raw.get("text_overlay") or "").strip(),
            "camera_move": str(raw.get("camera_move") or "static"),
            "transition_in": str(raw.get("transition_in") or "direct_cut"),
            "transition_out": str(raw.get("transition_out") or "direct_cut"),
            "emphasis_preset": str(raw.get("emphasis_preset") or "none"),
            "sound_intent": list(raw.get("sound_intent") or []),
            "reframe": dict(raw.get("reframe") or {}),
            "visual_bible": dict(raw.get("visual_bible") or {}),
            "safety": list(raw.get("safety") or ["No text in generated media", "No distorted dental anatomy"]),
            "status": "suggested",
            "selected_still": None,
            "selected_motion": None,
            "candidate_review": None,
            "versions": {"stills": [], "motion": []},
            "review": {"plan": None, "still": None, "motion": None},
        })
        previous_end = end
    return {
        "version": "2.0",
        "summary": str(payload.get("summary") or "B-roll compatibility plan"),
        "fps": fps,
        "duration_seconds": duration,
        "slots": slots,
    }


def update_slot(run_id: str, slot_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
    if not plan:
        raise FileNotFoundError("B-roll plan is missing")
    slot = next((item for item in plan["slots"] if item["slot_id"] == slot_id), None)
    if not slot:
        raise KeyError(slot_id)
    allowed = {
        "start", "end", "purpose", "composition_mode", "layout_variant", "subject_mode",
        "layout_template", "visual_type", "visual_style", "keep_subject_foreground",
        "panel_region", "show_caption", "caption_position", "still_brief", "motion_brief",
        "text_overlay", "camera_move", "transition_in", "transition_out", "emphasis_preset",
        "sound_intent", "safety", "reframe",
    }
    for key, value in updates.items():
        if key in allowed:
            slot[key] = value
    fps = int(plan.get("fps") or 30)
    total_duration = float(plan.get("duration_seconds") or 0)
    start = max(0.0, float(slot["start"]))
    end = max(start + 0.5, float(slot["end"]))
    if total_duration > 0:
        start, end = min(start, total_duration), min(end, total_duration)
    if end - start < 0.5:
        raise ValueError("A scene must be at least 0.5 seconds")
    variant = str(slot.get("layout_variant") or LAYOUT_TO_VARIANT.get(str(slot.get("layout_template")), "layered_foreground"))
    if variant not in LAYOUT_VARIANTS:
        raise ValueError("Unknown layout variant")
    layout = str(slot.get("layout_template") or VARIANT_TO_LAYOUT[variant])
    if layout not in LAYOUTS:
        raise ValueError("Unknown layout template")
    composition = str(slot.get("composition_mode") or _composition_from_variant(variant))
    subject = str(slot.get("subject_mode") or _subject_from_variant(variant))
    if composition not in COMPOSITION_MODES or subject not in SUBJECT_MODES:
        raise ValueError("Unknown composition or subject mode")
    if str(slot.get("caption_position", "auto")) not in CAPTION_POSITIONS:
        raise ValueError("Unknown caption position")
    if str(slot.get("camera_move", "static")) not in CAMERA_MOVES:
        raise ValueError("Unknown camera move")
    if str(slot.get("transition_in", "direct_cut")) not in TRANSITIONS or str(slot.get("transition_out", "direct_cut")) not in TRANSITIONS:
        raise ValueError("Unknown transition")
    if variant == "full_broll":
        layout, composition, subject = "broll_only", "full_broll", "hidden"
        slot["panel_region"] = 1.0
        slot["keep_subject_foreground"] = False
    else:
        slot["panel_region"] = max(0.25, min(float(slot.get("panel_region", 0.44)), 1.0))
        slot["keep_subject_foreground"] = subject == "matte_foreground"
    for other in plan.get("slots", []):
        if other.get("slot_id") == slot_id or other.get("status") in {"rejected", "talking_head"}:
            continue
        if start < float(other["end"]) and end > float(other["start"]):
            raise ValueError(f"Scene timing overlaps {other['slot_id']}")
    slot.update({
        "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3),
        "start_frame": round(start * fps), "end_frame": round(end * fps),
        "layout_variant": variant, "layout_template": layout,
        "composition_mode": composition, "subject_mode": subject,
        "show_caption": bool(slot.get("show_caption", False)),
    })
    write_json(paths.plan / "broll_plan.json", plan)
    _sync_editorial_scene(paths, slot)
    return slot


def slot_action(run_id: str, slot_id: str, action: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    slot = next((item for item in plan.get("slots", []) if item["slot_id"] == slot_id), None)
    if not slot:
        raise KeyError(slot_id)
    transitions = {
        "approve_plan": ("plan_approved", "plan"),
        "reject": ("rejected", "plan"),
        "keep_talking_head": ("talking_head", "plan"),
        "approve_still": ("still_approved", "still"),
        "reject_still": ("plan_approved", "still"),
        "approve_motion": ("motion_approved", "motion"),
        "use_still_only": ("still_approved", "motion"),
        "reject_motion": ("still_approved", "motion"),
    }
    if action not in transitions:
        raise ValueError(f"Unknown slot action: {action}")
    status, review_key = transitions[action]
    if action == "reject_still" and slot.get("selected_still"):
        status = "still_approved"
    if action == "reject_motion" and slot.get("selected_motion"):
        status = "motion_approved"
    slot["status"] = status
    slot.setdefault("review", {})[review_key] = action
    if action in {"reject", "keep_talking_head"}:
        slot["selected_still"] = None
        slot["selected_motion"] = None
    if action == "approve_still":
        versions = (slot.get("versions") or {}).get("stills") or []
        if not versions:
            raise RuntimeError("No still candidate exists for this scene")
        slot["selected_still"] = _review_selected(slot, versions[-1]["path"])
    if action == "approve_motion":
        versions = (slot.get("versions") or {}).get("motion") or []
        if not versions:
            raise RuntimeError("No motion candidate exists for this scene")
        slot["selected_motion"] = versions[-1]["path"]
    if action == "use_still_only":
        slot["selected_motion"] = None
    write_json(paths.plan / "broll_plan.json", plan)
    return slot


def refine_slot(run_id: str, slot_id: str, instruction: str = "") -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
    transcript = read_json(paths.transcript / "transcript.json", {"phrases": []})
    visual = read_json(paths.analysis / "visual-analysis.json", {"windows": []})
    if not plan:
        raise FileNotFoundError("B-roll plan is missing")
    slot = next((item for item in plan.get("slots", []) if item.get("slot_id") == slot_id), None)
    if not slot:
        raise KeyError(slot_id)
    nearby_phrases = [item for item in transcript.get("phrases", []) if float(item.get("end", 0)) >= float(slot["start"]) - 2 and float(item.get("start", 0)) <= float(slot["end"]) + 2]
    nearby_windows = [item for item in visual.get("windows", []) if float(item.get("end", 0)) >= float(slot["start"]) - 1.5 and float(item.get("start", 0)) <= float(slot["end"]) + 1.5]
    selection = meta["settings"]["task_models"]["slot_refinement"]
    system = load_prompt("slot_refinement.system.txt")
    user = load_prompt("slot_refinement.user.txt").format(
        slot=json.dumps(slot, ensure_ascii=False, indent=2),
        phrases=json.dumps(nearby_phrases, ensure_ascii=False, indent=2),
        visual_windows=json.dumps(nearby_windows, ensure_ascii=False, indent=2),
        instruction=instruction.strip() or "Improve the modern composition, natural visual direction, and generation reliability without changing clinical meaning.",
    )
    payload = call_task_json(task="slot_refinement", selection=selection, system=system, user=user, cwd=paths.root, output_schema=load_schema("slot_refinement.schema.json"))
    response_dir = paths.responses / "editorial" / "refinements"
    response_dir.mkdir(parents=True, exist_ok=True)
    version = f"v{len(list(response_dir.glob(f'{slot_id}-*.json'))) + 1:02d}"
    write_json(response_dir / f"{slot_id}-{version}.json", {"selection": selection, "result": payload})
    refined = update_slot(run_id, slot_id, payload)
    refined.setdefault("review", {})["plan"] = "ai_refined"
    refreshed = read_json(paths.plan / "broll_plan.json")
    next(item for item in refreshed["slots"] if item["slot_id"] == slot_id).setdefault("review", {})["plan"] = "ai_refined"
    write_json(paths.plan / "broll_plan.json", refreshed)
    return refined


def _sync_editorial_scene(paths, slot: dict[str, Any]) -> None:
    editorial = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    scene = next((item for item in editorial.get("scenes", []) if item.get("scene_id") == slot.get("scene_id")), None)
    if not scene:
        return
    mapping = {
        "start": "start", "end": "end", "duration": "duration", "purpose": "editorial_purpose",
        "composition_mode": "composition_mode", "layout_variant": "layout_variant", "subject_mode": "subject_mode",
        "visual_style": "visual_style", "still_brief": "visual_brief", "motion_brief": "motion_brief",
        "text_overlay": "text_overlay", "camera_move": "camera_move", "transition_in": "transition_in",
        "transition_out": "transition_out", "emphasis_preset": "emphasis_preset", "sound_intent": "sound_intent",
    }
    for source, target in mapping.items():
        if source in slot:
            scene[target] = slot[source]
    write_json(paths.editorial / "editorial-plan.json", editorial)


def _review_selected(slot: dict[str, Any], fallback: str) -> str:
    review = slot.get("candidate_review") or {}
    selected = str(review.get("selected_path") or "")
    return selected or fallback


def _composition_from_variant(variant: str) -> str:
    if variant == "talking_head":
        return "talking_head"
    if variant == "full_broll":
        return "full_broll"
    if variant == "layered_foreground":
        return "layered_foreground"
    if variant == "picture_in_picture":
        return "picture_in_picture"
    return "split_layout"


def _subject_from_variant(variant: str) -> str:
    if variant == "talking_head":
        return "original"
    if variant == "full_broll":
        return "hidden"
    if variant == "layered_foreground":
        return "matte_foreground"
    if variant == "picture_in_picture":
        return "picture_in_picture"
    return "cropped_original"
