from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema


LAYOUTS = {
    "bottom_board",
    "top_board",
    "left_panel",
    "right_panel",
    "torn_split",
    "floating_cards",
    "full_frame",
    "broll_only",
}
CAPTION_POSITIONS = {"auto", "top", "bottom"}


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    transcript = read_json(paths.transcript / "transcript.json")
    visual_analysis = read_json(paths.analysis / "visual-analysis.json")
    if not transcript or not visual_analysis:
        raise RuntimeError("Transcription and visual analysis are required before B-roll planning")
    selection = meta["settings"]["task_models"]["broll_analysis"]
    system = load_prompt("broll_analysis.system.txt")
    user = load_prompt("broll_analysis.user.txt").format(
        settings=json.dumps(_compact_settings(meta["settings"]), ensure_ascii=False, indent=2),
        transcript=json.dumps(_compact_transcript(transcript), ensure_ascii=False, indent=2),
        visual_analysis=json.dumps(_compact_visual(visual_analysis), ensure_ascii=False, indent=2),
    )
    prompt_dir = paths.prompts / "planning"
    response_dir = paths.responses / "planning"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "broll-analysis.txt").write_text(f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8")
    schema = load_schema("broll_plan.schema.json")
    try:
        append_log(paths, f"B-roll planning: requesting plan from {selection['provider']} ({selection['model']})")
        payload = call_task_json(task="broll_analysis", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
        append_log(paths, "B-roll planning: provider response received; validating timeline slots")
        write_json(response_dir / "broll-analysis.json", payload)
    except Exception as exc:
        payload = _fallback_plan(transcript, visual_analysis)
        append_log(paths, f"B-roll planning: provider unavailable; using deterministic fallback ({exc})")
        write_json(response_dir / "broll-analysis-fallback.json", {"error": str(exc), "payload": payload})
    plan = normalize_plan(payload, float(transcript.get("duration_seconds") or 0), fps=int(meta["settings"].get("fps", 30)))
    write_json(paths.plan / "broll_plan.json", plan)
    append_log(paths, f"B-roll planning: saved {len(plan['slots'])} proposed slots for human review")
    return {"artifacts": ["plan/broll_plan.json", "prompts/planning/", "responses/planning/"], "summary": {"slots": len(plan["slots"]), "summary": plan.get("summary")}}


def normalize_plan(payload: dict[str, Any], duration: float, *, fps: int = 30) -> dict[str, Any]:
    slots = []
    previous_end = 0.0
    for index, raw in enumerate(payload.get("slots") or [], start=1):
        if duration <= 0:
            break
        requested_start = float(raw.get("start", 0))
        requested_end = float(raw.get("end", requested_start + 4))
        start = max(0.0, min(requested_start, duration))
        if start < previous_end:
            start = previous_end
        end = min(duration, max(start + 0.5, requested_end))
        if end - start < 0.5:
            continue
        slot_id = f"broll_{len(slots)+1:03d}"
        layout = str(raw.get("layout_template") or "bottom_board")
        if layout not in LAYOUTS:
            layout = "bottom_board"
        broll_only = layout == "broll_only"
        caption_position = str(raw.get("caption_position") or "auto")
        if caption_position not in CAPTION_POSITIONS:
            caption_position = "auto"
        slot = {
            "slot_id": slot_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_frame": round(start * fps),
            "end_frame": round(end * fps),
            "duration": round(end - start, 3),
            "transcript": str(raw.get("transcript") or "").strip(),
            "purpose": str(raw.get("purpose") or "Visual support").strip(),
            "priority": raw.get("priority") if raw.get("priority") in {"essential", "useful", "optional"} else "useful",
            "layout_template": layout,
            "visual_type": str(raw.get("visual_type") or "medical_illustration"),
            "keep_subject_foreground": False if broll_only else bool(raw.get("keep_subject_foreground", layout != "full_frame")),
            "panel_region": 1.0 if broll_only else max(0.25, min(float(raw.get("panel_region", 0.44)), 0.72)),
            "show_caption": bool(raw.get("show_caption", False)),
            "caption_position": caption_position,
            "still_brief": str(raw.get("still_brief") or raw.get("purpose") or "").strip(),
            "motion_brief": str(raw.get("motion_brief") or "Subtle stable motion only").strip(),
            "text_overlay": str(raw.get("text_overlay") or "").strip(),
            "safety": list(raw.get("safety") or ["No text in generated media", "No distorted dental anatomy"]),
            "status": "suggested",
            "selected_still": None,
            "selected_motion": None,
            "versions": {"stills": [], "motion": []},
            "review": {"plan": None, "still": None, "motion": None},
        }
        slots.append(slot)
        previous_end = end
    return {"version": "1.1", "summary": str(payload.get("summary") or "B-roll plan"), "fps": fps, "duration_seconds": duration, "slots": slots}


def update_slot(run_id: str, slot_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
    if not plan:
        raise FileNotFoundError("B-roll plan is missing")
    slot = next((item for item in plan["slots"] if item["slot_id"] == slot_id), None)
    if not slot:
        raise KeyError(slot_id)
    allowed = {
        "start", "end", "purpose", "layout_template", "visual_type", "keep_subject_foreground",
        "panel_region", "show_caption", "caption_position", "still_brief", "motion_brief", "text_overlay", "safety",
    }
    for key, value in updates.items():
        if key in allowed:
            slot[key] = value
    fps = int(plan.get("fps") or 30)
    total_duration = float(plan.get("duration_seconds") or 0)
    start = max(0.0, float(slot["start"]))
    end = max(start + 0.5, float(slot["end"]))
    if total_duration > 0:
        start = min(start, total_duration)
        end = min(end, total_duration)
    if end - start < 0.5:
        raise ValueError("A B-roll slot must be at least 0.5 seconds")
    if slot.get("layout_template") not in LAYOUTS:
        raise ValueError("Unknown layout template")
    if slot.get("caption_position", "auto") not in CAPTION_POSITIONS:
        raise ValueError("Unknown caption position")
    if slot.get("layout_template") == "broll_only":
        slot["keep_subject_foreground"] = False
        slot["panel_region"] = 1.0
    else:
        slot["keep_subject_foreground"] = bool(slot.get("keep_subject_foreground", True))
        slot["panel_region"] = max(0.25, min(float(slot.get("panel_region", 0.44)), 0.72))
    slot["show_caption"] = bool(slot.get("show_caption", False))
    for other in plan.get("slots", []):
        if other.get("slot_id") == slot_id or other.get("status") in {"rejected", "talking_head"}:
            continue
        if start < float(other["end"]) and end > float(other["start"]):
            raise ValueError(f"Slot timing overlaps {other['slot_id']}")
    slot["start"] = round(start, 3)
    slot["end"] = round(end, 3)
    slot["duration"] = round(end - start, 3)
    slot["start_frame"] = round(start * fps)
    slot["end_frame"] = round(end * fps)
    write_json(paths.plan / "broll_plan.json", plan)
    return slot


def slot_action(run_id: str, slot_id: str, action: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
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
    slot["review"][review_key] = action
    if action in {"reject", "keep_talking_head"}:
        slot["selected_still"] = None
        slot["selected_motion"] = None
    if action == "approve_still":
        versions = (slot.get("versions") or {}).get("stills") or []
        if not versions:
            raise RuntimeError("No still candidate exists for this slot")
        slot["selected_still"] = versions[-1]["path"]
    if action == "approve_motion":
        versions = (slot.get("versions") or {}).get("motion") or []
        if not versions:
            raise RuntimeError("No motion candidate exists for this slot")
        slot["selected_motion"] = versions[-1]["path"]
    if action == "use_still_only":
        slot["selected_motion"] = None
    write_json(paths.plan / "broll_plan.json", plan)
    return slot


def refine_slot(run_id: str, slot_id: str, instruction: str = "") -> dict[str, Any]:
    """Use the run-scoped slot-refinement model without changing other slots."""
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
    nearby_phrases = [
        item for item in transcript.get("phrases", [])
        if float(item.get("end", 0)) >= float(slot["start"]) - 2.0
        and float(item.get("start", 0)) <= float(slot["end"]) + 2.0
    ]
    nearby_windows = [
        item for item in visual.get("windows", [])
        if float(item.get("end", 0)) >= float(slot["start"]) - 1.5
        and float(item.get("start", 0)) <= float(slot["end"]) + 1.5
    ]
    selection = meta["settings"]["task_models"]["slot_refinement"]
    system = load_prompt("slot_refinement.system.txt")
    user = load_prompt("slot_refinement.user.txt").format(
        slot=json.dumps(slot, ensure_ascii=False, indent=2),
        phrases=json.dumps(nearby_phrases, ensure_ascii=False, indent=2),
        visual_windows=json.dumps(nearby_windows, ensure_ascii=False, indent=2),
        instruction=instruction.strip() or "Improve clarity, composition, and generation reliability without changing the meaning.",
    )
    schema = load_schema("slot_refinement.schema.json")
    prompt_dir = paths.prompts / "planning" / "refinements"
    response_dir = paths.responses / "planning" / "refinements"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    stamp = len(list(response_dir.glob(f"{slot_id}-*.json"))) + 1
    version = f"v{stamp:02d}"
    (prompt_dir / f"{slot_id}-{version}.txt").write_text(
        f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8"
    )
    payload = call_task_json(
        task="slot_refinement", selection=selection, system=system, user=user,
        cwd=paths.root, output_schema=schema,
    )
    write_json(response_dir / f"{slot_id}-{version}.json", {"selection": selection, "result": payload})
    refined = update_slot(run_id, slot_id, payload)
    refined.setdefault("review", {})["plan"] = "ai_refined"
    refreshed = read_json(paths.plan / "broll_plan.json")
    target = next(item for item in refreshed["slots"] if item["slot_id"] == slot_id)
    target.setdefault("review", {})["plan"] = "ai_refined"
    write_json(paths.plan / "broll_plan.json", refreshed)
    return target


def _compact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: settings.get(key) for key in ("width", "height", "fps", "aspect_ratio", "captions_mode")}


def _compact_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    return {"duration_seconds": transcript.get("duration_seconds"), "language_code": transcript.get("language_code"), "mode": transcript.get("mode"), "phrases": transcript.get("phrases")}


def _compact_visual(analysis: dict[str, Any]) -> dict[str, Any]:
    return {"duration_seconds": analysis.get("duration_seconds"), "windows": analysis.get("windows")}


def _fallback_plan(transcript: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    phrases = transcript.get("phrases") or []
    slots = []
    last_end = 0.0
    for phrase in phrases:
        start, end = float(phrase.get("start", 0)), float(phrase.get("end", 0))
        if start < 2.0 or start < last_end + 4.0 or end - start < 1.5:
            continue
        slot_start = round(start + min(0.35, (end-start) * 0.1), 3)
        slot_end = round(min(end, slot_start + 4.5), 3)
        if slot_end - slot_start < 2.3:
            continue
        slots.append({
            "slot_id": f"broll_{len(slots)+1:03d}", "start": slot_start, "end": slot_end,
            "transcript": phrase.get("text", ""), "purpose": "Explain this phrase visually",
            "priority": "useful", "layout_template": "bottom_board", "visual_type": "medical_illustration",
            "keep_subject_foreground": True, "panel_region": 0.44,
            "show_caption": False, "caption_position": "auto",
            "still_brief": f"A clean dental visual explaining: {phrase.get('text','')}",
            "motion_brief": "Subtle stable movement only", "text_overlay": "",
            "safety": ["No blood", "No fake patient result", "No text inside image", "Accurate dental anatomy"],
        })
        last_end = slot_end
        if len(slots) >= max(4, round(float(transcript.get("duration_seconds") or 60) / 8)):
            break
    return {"summary": "Deterministic fallback plan; review every slot", "slots": slots}
