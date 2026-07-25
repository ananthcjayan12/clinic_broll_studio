from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run, mark_stage, save_run
from ..pipeline.common import load_prompt, load_schema
from ..providers.registry import call_task_json

TREATMENTS = {"original", "omni_presenter_edit", "omni_broll", "omni_transition"}
CREDIT_COST = {
    "original": 0,
    "omni_presenter_edit": 40,
    "omni_broll": 40,
    "omni_transition": 40,
}


def run(run_id: str) -> dict[str, Any]:
    """Create the Omni-first edit plan and the compatibility render plan."""
    paths = run_paths(run_id)
    meta = load_run(run_id)
    transcript = read_json(paths.transcript / "transcript.json", {}) or {}
    visual = read_json(paths.analysis / "visual-analysis.json", {}) or {}
    duration = float(transcript.get("duration_seconds") or visual.get("duration_seconds") or 0)
    if duration <= 0:
        raise RuntimeError("A clean transcript and analysed source video are required")

    settings = _director_settings(meta.get("settings") or {})
    selection = meta["settings"]["task_models"]["editorial_director"]
    system = load_prompt("omni_reel_director.system.txt")
    user = load_prompt("omni_reel_director.user.txt").format(
        settings=json.dumps(settings, ensure_ascii=False, indent=2),
        transcript=json.dumps(_compact_transcript(transcript), ensure_ascii=False, indent=2),
        visual_analysis=json.dumps(_compact_visual(visual), ensure_ascii=False, indent=2),
    )
    prompt_dir = paths.prompts / "omni"
    response_dir = paths.responses / "omni"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "reel-director.txt").write_text(
        f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8"
    )

    fallback_used = False
    try:
        append_log(paths, f"Omni Reel Director: requesting plan from {selection['provider']}")
        raw = call_task_json(
            task="editorial_director",
            selection=selection,
            system=system,
            user=user,
            cwd=paths.root,
            output_schema=load_schema("omni_reel_plan.schema.json"),
        )
        write_json(response_dir / "reel-director.json", raw)
    except Exception as exc:
        fallback_used = True
        raw = _fallback_plan(transcript, duration)
        write_json(response_dir / "reel-director-fallback.json", {"error": str(exc), "payload": raw})
        append_log(paths, f"Omni Reel Director: deterministic fallback used ({exc})")

    plan = normalise_plan(raw, transcript, duration, settings)
    plan["fallback_used"] = fallback_used
    omni_root = paths.root / "omni"
    omni_root.mkdir(parents=True, exist_ok=True)
    write_json(omni_root / "plan.json", plan)

    compatibility = to_broll_plan(plan, fps=int(meta["settings"].get("fps") or 30))
    write_json(paths.plan / "broll_plan.json", compatibility)
    write_json(paths.editorial / "editorial-plan.json", _editorial_plan(plan, compatibility))
    write_json(paths.editorial / "visual-bible.json", _visual_bible())

    meta = load_run(run_id)
    meta.setdefault("approvals", {})["editorial"] = True
    meta["settings"]["production_mode"] = "omni_presenter_reel"
    save_run(meta)
    summary = {
        "scenes": len(plan["scenes"]),
        "omni_jobs": len(plan["jobs"]),
        "estimated_credits": plan["estimated_credits"],
        "fallback_used": fallback_used,
    }
    mark_stage(
        run_id,
        6,
        "complete",
        artifacts=["omni/plan.json", "plan/broll_plan.json", "editorial/editorial-plan.json"],
        summary=summary,
    )
    append_log(
        paths,
        f"Omni Reel Director: {summary['omni_jobs']} jobs · approximately {summary['estimated_credits']} credits",
    )
    return plan


def normalise_plan(
    payload: dict[str, Any],
    transcript: dict[str, Any],
    duration: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    max_jobs = max(1, min(int(settings.get("max_omni_jobs") or 5), 8))
    max_credits = max(40, min(int(settings.get("max_omni_credits") or 280), 800))
    max_segment = max(3.0, min(float(settings.get("omni_segment_max_seconds") or 8.0), 10.0))
    minimum = 1.5

    candidates: list[dict[str, Any]] = []
    for raw in sorted(payload.get("segments") or [], key=lambda item: float(item.get("start") or 0)):
        treatment = str(raw.get("treatment") or "original")
        if treatment not in TREATMENTS:
            treatment = "original"
        start = max(0.0, min(float(raw.get("start") or 0), duration))
        end = max(start, min(float(raw.get("end") or start), duration))
        if treatment != "original":
            end = min(end, start + max_segment)
        if end - start < minimum:
            continue
        candidates.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "treatment": treatment,
            "purpose": str(raw.get("purpose") or "Support the explanation").strip(),
            "visual_concept": str(raw.get("visual_concept") or "Premium modern presenter reel treatment").strip(),
            "prompt": _safe_prompt(str(raw.get("prompt") or ""), treatment),
            "transition_strategy": str(raw.get("transition_strategy") or "direct_cut").strip(),
            "priority": str(raw.get("priority") or "useful"),
        })

    accepted: list[dict[str, Any]] = []
    cursor = 0.0
    credits = 0
    jobs = 0
    for item in candidates:
        item = deepcopy(item)
        item["start"] = max(float(item["start"]), cursor)
        if float(item["end"]) - float(item["start"]) < minimum:
            continue
        cost = CREDIT_COST[item["treatment"]]
        if item["treatment"] != "original":
            if jobs >= max_jobs or credits + cost > max_credits:
                continue
            jobs += 1
            credits += cost
        accepted.append(item)
        cursor = float(item["end"])

    scenes: list[dict[str, Any]] = []
    cursor = 0.0
    for item in accepted:
        start = float(item["start"])
        end = float(item["end"])
        if start > cursor + 0.04:
            scenes.append(_original_scene(cursor, start, transcript))
        scenes.append(item)
        cursor = end
    if cursor < duration - 0.04:
        scenes.append(_original_scene(cursor, duration, transcript))
    if not scenes:
        scenes = [_original_scene(0.0, duration, transcript)]

    jobs_payload: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        scene["scene_id"] = f"scene_{index:03d}"
        scene["duration"] = round(float(scene["end"]) - float(scene["start"]), 3)
        scene["narration"] = _transcript_excerpt(transcript, float(scene["start"]), float(scene["end"]))
        if scene["treatment"] != "original":
            job_id = f"OMNI-{len(jobs_payload) + 1:03d}"
            scene["job_id"] = job_id
            jobs_payload.append({
                "job_id": job_id,
                "scene_id": scene["scene_id"],
                "start": scene["start"],
                "end": scene["end"],
                "duration": scene["duration"],
                "treatment": scene["treatment"],
                "purpose": scene["purpose"],
                "visual_concept": scene["visual_concept"],
                "prompt": scene["prompt"],
                "estimated_credits": CREDIT_COST[scene["treatment"]],
            })

    return {
        "version": "3.0",
        "production_mode": "omni_presenter_reel",
        "summary": str(payload.get("summary") or "Omni-first modern presenter reel"),
        "duration_seconds": round(duration, 3),
        "scenes": scenes,
        "jobs": jobs_payload,
        "estimated_credits": sum(int(item["estimated_credits"]) for item in jobs_payload),
        "limits": {"max_omni_jobs": max_jobs, "max_omni_credits": max_credits, "max_segment_seconds": max_segment},
    }


def to_broll_plan(plan: dict[str, Any], *, fps: int = 30) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    for scene in plan.get("scenes") or []:
        original = scene["treatment"] == "original"
        slots.append({
            "slot_id": f"broll_{len(slots) + 1:03d}",
            "scene_id": scene["scene_id"],
            "job_id": scene.get("job_id"),
            "start": scene["start"],
            "end": scene["end"],
            "start_frame": round(float(scene["start"]) * fps),
            "end_frame": round(float(scene["end"]) * fps),
            "duration": scene["duration"],
            "transcript": scene.get("narration", ""),
            "purpose": scene.get("purpose", "Preserve the original delivery"),
            "priority": scene.get("priority", "useful"),
            "composition_mode": "talking_head" if original else "full_broll",
            "layout_variant": "talking_head" if original else "full_broll",
            "subject_mode": "original" if original else "hidden",
            "layout_template": "full_frame" if original else "broll_only",
            "visual_type": "omni_video_edit",
            "visual_style": "modern_presenter_reel",
            "visual_strategy": "none" if original else "generated_photo",
            "provider_usage": not original,
            "operator_visible": True,
            "keep_subject_foreground": False,
            "panel_region": 1.0,
            "show_caption": True,
            "caption_position": "bottom",
            "still_brief": "",
            "motion_brief": scene.get("visual_concept", ""),
            "omni_prompt": scene.get("prompt", ""),
            "text_overlay": "",
            "camera_move": "static",
            "transition_in": "direct_cut",
            "transition_out": "direct_cut",
            "emphasis_preset": "none",
            "sound_intent": [],
            "safety": ["Preserve the original narration", "No generated text", "Human identity review required"],
            "status": "talking_head" if original else "plan_approved",
            "selected_still": None,
            "selected_motion": None,
            "selected_omni": None,
            "candidate_review": None,
            "versions": {"stills": [], "motion": [], "omni": []},
            "review": {"plan": "keep_talking_head" if original else "approve_plan", "still": None, "motion": None},
        })
    jobs = plan.get("jobs") or []
    return {
        "version": "3.0",
        "production_mode": "omni_presenter_reel",
        "summary": plan.get("summary"),
        "fps": fps,
        "duration_seconds": plan.get("duration_seconds", 0),
        "budget_report": {
            "editorial_scenes": len(slots),
            "visual_scenes": len(jobs),
            "expected_image_generations": 0,
            "expected_omni_edits": len(jobs),
            "estimated_omni_credits": plan.get("estimated_credits", 0),
            "visual_coverage_seconds": round(sum(float(item["duration"]) for item in jobs), 3),
            "approved_for_generation": True,
            "violations": [],
        },
        "slots": slots,
    }


def _editorial_plan(plan: dict[str, Any], compatibility: dict[str, Any]) -> dict[str, Any]:
    scenes = []
    by_id = {item["scene_id"]: item for item in compatibility["slots"]}
    for item in plan["scenes"]:
        slot = by_id[item["scene_id"]]
        scenes.append({
            "scene_id": item["scene_id"],
            "start": item["start"],
            "end": item["end"],
            "duration": item["duration"],
            "narration": item.get("narration", ""),
            "editorial_purpose": item.get("purpose", ""),
            "composition_mode": slot["composition_mode"],
            "layout_variant": slot["layout_variant"],
            "subject_mode": slot["subject_mode"],
            "visual_style": "mixed",
            "visual_strategy": slot["visual_strategy"],
            "visual_brief": item.get("visual_concept", ""),
            "motion_brief": item.get("visual_concept", ""),
            "camera_move": "static",
            "transition_in": "direct_cut",
            "transition_out": "direct_cut",
            "emphasis_preset": "none",
            "caption_mode": "on",
            "sound_intent": [],
            "priority": item.get("priority", "useful"),
            "operator_visible": True,
        })
    return {
        "version": "3.0",
        "production_mode": "omni_presenter_reel",
        "summary": plan.get("summary"),
        "duration_seconds": plan.get("duration_seconds"),
        "continuous_coverage": True,
        "budget_report": compatibility["budget_report"],
        "scenes": scenes,
    }


def _visual_bible() -> dict[str, Any]:
    return {
        "look": "premium modern presenter reel with photoreal Omni transformations",
        "palette": ["clinic white", "restrained teal", "warm neutral", "high-contrast black typography"],
        "camera_language": ["stable presenter framing", "restrained push-ins", "clean direct cuts"],
        "avoid": ["generated captions", "face alteration", "waxy anatomy", "random scene changes", "extra people"],
        "medical_rules": ["preserve clinical meaning", "review generated anatomy and treatment claims"],
    }


def _safe_prompt(prompt: str, treatment: str) -> str:
    base = prompt.strip() or (
        "Transform the environment into a premium modern dental explainer visual that supports the spoken idea."
    )
    if treatment == "omni_broll":
        preservation = (
            "Replace the visual scene completely while preserving the exact timing of the source clip. "
            "The original narration will be restored during final rendering."
        )
    else:
        preservation = (
            "Preserve the woman's exact facial identity, lip movement, hair, clothing, body position, "
            "hand gestures and timing."
        )
    return (
        f"{base}\n\n{preservation}\n"
        "Keep everything else the same unless explicitly requested. "
        "No generated dialogue. No captions, written text, logos or watermarks. "
        "Vertical 9:16, photoreal, polished modern reel finish."
    )


def _original_scene(start: float, end: float, transcript: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "treatment": "original",
        "purpose": "Preserve Dr Pooja's authentic delivery",
        "visual_concept": "Original cleaned talking-head footage",
        "prompt": "",
        "transition_strategy": "direct_cut",
        "priority": "essential",
        "narration": _transcript_excerpt(transcript, start, end),
    }


def _fallback_plan(transcript: dict[str, Any], duration: float) -> dict[str, Any]:
    centres = [0.24, 0.50, 0.74]
    segments = []
    for index, fraction in enumerate(centres, start=1):
        start = max(1.5, min(duration - 5.0, duration * fraction - 2.5))
        end = min(duration - 2.0, start + 5.0)
        if end - start < 2.0:
            continue
        narration = _transcript_excerpt(transcript, start, end)
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "treatment": "omni_presenter_edit",
            "purpose": "Add a premium visual change around the presenter",
            "visual_concept": narration or f"Designed presenter moment {index}",
            "prompt": (
                "Transform only the background into a premium, relevant dental education environment "
                f"that visually supports this narration: {narration}"
            ),
            "transition_strategy": "direct_cut",
            "priority": "useful",
        })
    return {"summary": "Safe three-moment Omni presenter plan", "segments": segments}


def _transcript_excerpt(transcript: dict[str, Any], start: float, end: float) -> str:
    phrases = transcript.get("phrases") or []
    return " ".join(
        str(item.get("text") or "").strip()
        for item in phrases
        if float(item.get("end") or 0) > start and float(item.get("start") or 0) < end
    ).strip()


def _compact_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": transcript.get("duration_seconds"),
        "language": transcript.get("language_code") or transcript.get("language"),
        "phrases": transcript.get("phrases") or [],
        "words": (transcript.get("words") or [])[:2500],
    }


def _compact_visual(visual: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": visual.get("duration_seconds"),
        "windows": visual.get("windows") or [],
        "reframe_summary": visual.get("reframe_summary") or {},
    }


def _director_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "production_mode": "omni_presenter_reel",
        "aspect_ratio": settings.get("aspect_ratio", "9:16"),
        "editing_profile": settings.get("editing_profile", "modern_tech_explainer"),
        "editing_intensity": settings.get("editing_intensity", "medium"),
        "max_omni_jobs": settings.get("max_omni_jobs", 5),
        "max_omni_credits": settings.get("max_omni_credits", 280),
        "omni_segment_max_seconds": settings.get("omni_segment_max_seconds", 8),
        "omni_handle_seconds": settings.get("omni_handle_seconds", 0.4),
        "style_reference": (
            "Modern presenter-led social reel: presenter remains central, photoreal contextual scene changes, "
            "short full-screen support moments, bold captions added later by the local renderer."
        ),
    }
