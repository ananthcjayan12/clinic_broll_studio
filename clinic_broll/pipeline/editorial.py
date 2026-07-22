from __future__ import annotations

import json
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema
from .editorial_policy import apply_editorial_policy, repair_editorial_payload, visual_strategy_for

COMPOSITION_MODES = {
    "talking_head", "split_layout", "full_broll", "layered_foreground",
    "picture_in_picture", "graphic_scene", "comparison", "montage",
}
LAYOUT_VARIANTS = {
    "talking_head", "broll_top_speaker_bottom", "speaker_top_broll_bottom",
    "speaker_left_broll_right", "broll_left_speaker_right", "picture_in_picture",
    "floating_visual", "full_broll", "layered_foreground",
}
SUBJECT_MODES = {"original", "cropped_original", "matte_foreground", "picture_in_picture", "hidden"}
VISUAL_STYLES = {
    "natural_lifestyle", "colorful_macro", "clean_medical_illustration",
    "clinic_authentic", "playful_explainer", "mixed",
}
STYLE_ALIASES = {
    "natural": "natural_lifestyle",
    "natural_colorful": "mixed",
    "cinematic": "natural_lifestyle",
    "medical_illustration": "clean_medical_illustration",
}
CAMERA_MOVES = {"static", "subtle_punch_in", "emphasis_punch", "slow_push", "micro_pull_back", "face_follow", "object_follow"}
TRANSITIONS = {"direct_cut", "soft_crossfade", "clean_push", "vertical_slide", "horizontal_swipe", "mask_reveal", "paper_reveal", "zoom_match", "blur_transition", "dip_to_white"}
EMPHASIS = {"none", "keyword_pop", "card_snap", "underline_draw", "number_count", "icon_bounce", "warning_pulse", "comparison_flip"}

LAYOUT_COMPATIBILITY = {
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


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    transcript = read_json(paths.transcript / "transcript.json", {})
    visual = read_json(paths.analysis / "visual-analysis.json", {})
    continuity = read_json(paths.dialogue / "continuity-plan.json", {})
    if not transcript or not visual:
        raise RuntimeError("Clean transcript and visual analysis are required")

    settings = _compact_settings(meta["settings"])
    director_selection = meta["settings"]["task_models"]["editorial_director"]
    system = load_prompt("editorial_director.system.txt")
    user = load_prompt("editorial_director.user.txt").format(
        settings=json.dumps(settings, ensure_ascii=False, indent=2),
        transcript=json.dumps(_compact_transcript(transcript), ensure_ascii=False, indent=2),
        visual_analysis=json.dumps(_compact_visual(visual), ensure_ascii=False, indent=2),
        continuity=json.dumps(continuity, ensure_ascii=False, indent=2),
    )
    prompt_dir = paths.prompts / "editorial"
    response_dir = paths.responses / "editorial"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "editorial-director.txt").write_text(f"SYSTEM\n{system}\n\nUSER\n{user}", encoding="utf-8")
    fallback_used = False
    try:
        append_log(paths, f"Editorial Director: requesting scene graph from {director_selection['provider']}")
        raw = call_task_json(
            task="editorial_director",
            selection=director_selection,
            system=system,
            user=user,
            cwd=paths.root,
            output_schema=load_schema("editorial_plan.schema.json"),
        )
        raw = repair_editorial_payload(raw)
        write_json(response_dir / "editorial-director.json", raw)
    except Exception as exc:
        # A provider/schema failure must never trigger a large paid B-roll plan.
        # The safe fallback preserves the complete talking head and asks the
        # operator to rerun/refine the Director instead.
        fallback_used = True
        raw = _fallback_editorial(transcript, visual, settings)
        write_json(response_dir / "editorial-director-fallback.json", {"error": str(exc), "payload": raw})
        append_log(paths, f"Editorial Director: safe talking-head fallback used ({exc})")

    duration = float(transcript.get("duration_seconds") or 0)
    fps = int(meta["settings"].get("fps") or 30)
    normalized = normalize_editorial_plan(raw, duration, fps=fps)
    editorial_plan = apply_editorial_policy(normalized, meta["settings"])
    editorial_plan["fallback_used"] = fallback_used
    write_json(paths.editorial / "editorial-plan.json", editorial_plan)

    bible_selection = meta["settings"]["task_models"]["visual_bible_director"]
    bible_system = load_prompt("visual_bible_director.system.txt")
    bible_user = load_prompt("visual_bible_director.user.txt").format(
        settings=json.dumps(settings, ensure_ascii=False, indent=2),
        editorial_plan=json.dumps(editorial_plan, ensure_ascii=False, indent=2),
    )
    (prompt_dir / "visual-bible.txt").write_text(f"SYSTEM\n{bible_system}\n\nUSER\n{bible_user}", encoding="utf-8")
    try:
        visual_bible = call_task_json(
            task="visual_bible_director",
            selection=bible_selection,
            system=bible_system,
            user=bible_user,
            cwd=paths.root,
            output_schema=load_schema("visual_bible.schema.json"),
        )
        write_json(response_dir / "visual-bible.json", visual_bible)
    except Exception as exc:
        visual_bible = _fallback_visual_bible(settings)
        write_json(response_dir / "visual-bible-fallback.json", {"error": str(exc), "payload": visual_bible})
    write_json(paths.editorial / "visual-bible.json", visual_bible)

    broll_plan = editorial_to_broll_plan(editorial_plan, visual_bible)
    write_json(paths.plan / "broll_plan.json", broll_plan)
    report = editorial_plan.get("budget_report") or {}
    append_log(
        paths,
        "Editorial Director: saved "
        f"{report.get('editorial_scenes', len(editorial_plan['scenes']))} operator scenes, "
        f"{report.get('visual_scenes', 0)} visual moments, "
        f"{report.get('expected_image_generations', 0)} expected image requests, "
        f"{round(float(report.get('visual_coverage_ratio', 0)) * 100, 1)}% visual coverage",
    )
    return {
        "artifacts": [
            "editorial/editorial-plan.json", "editorial/visual-bible.json",
            "plan/broll_plan.json", "prompts/editorial/", "responses/editorial/",
        ],
        "summary": {
            "scenes": len(editorial_plan["scenes"]),
            "visual_scenes": report.get("visual_scenes", 0),
            "expected_image_generations": report.get("expected_image_generations", 0),
            "fallback_used": fallback_used,
        },
    }


def normalize_editorial_plan(payload: dict[str, Any], duration: float, *, fps: int = 30) -> dict[str, Any]:
    payload = repair_editorial_payload(payload)
    scenes: list[dict[str, Any]] = []
    previous_end = 0.0
    for raw in sorted(payload.get("scenes") or [], key=lambda item: float(item.get("start") or 0)):
        if duration <= 0:
            break
        start = max(previous_end, max(0.0, min(float(raw.get("start") or 0), duration)))
        end = min(duration, max(start + 0.45, float(raw.get("end") or start + 3.0)))
        if end - start < 0.45:
            continue
        layout = str(raw.get("layout_variant") or "talking_head")
        if layout not in LAYOUT_VARIANTS:
            layout = "talking_head"
        composition = str(raw.get("composition_mode") or ("talking_head" if layout == "talking_head" else "split_layout"))
        if composition not in COMPOSITION_MODES:
            composition = "talking_head"
        subject = str(raw.get("subject_mode") or _default_subject(layout))
        if subject not in SUBJECT_MODES:
            subject = _default_subject(layout)
        if layout == "full_broll":
            composition, subject = "full_broll", "hidden"
        if layout == "layered_foreground":
            composition, subject = "layered_foreground", "matte_foreground"
        if layout == "picture_in_picture":
            composition, subject = "picture_in_picture", "picture_in_picture"
        style = str(raw.get("visual_style") or "natural_lifestyle")
        style = STYLE_ALIASES.get(style, style)
        if style not in VISUAL_STYLES:
            style = "natural_lifestyle"
        camera = str(raw.get("camera_move") or "static")
        if camera not in CAMERA_MOVES:
            camera = "static"
        transition_in = str(raw.get("transition_in") or "direct_cut")
        transition_out = str(raw.get("transition_out") or "direct_cut")
        if transition_in not in TRANSITIONS:
            transition_in = "direct_cut"
        if transition_out not in TRANSITIONS:
            transition_out = "direct_cut"
        emphasis = str(raw.get("emphasis_preset") or "none")
        if emphasis not in EMPHASIS:
            emphasis = "none"
        scene_id = f"scene_{len(scenes)+1:03d}"
        scene = {
            "scene_id": scene_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_frame": round(start * fps),
            "end_frame": round(end * fps),
            "duration": round(end - start, 3),
            "narration": str(raw.get("narration") or "").strip(),
            "editorial_purpose": str(raw.get("editorial_purpose") or "Support the explanation").strip(),
            "energy": max(0.0, min(float(raw.get("energy", 0.5)), 1.0)),
            "concept_density": max(0.0, min(float(raw.get("concept_density", 0.5)), 1.0)),
            "keep_eye_contact": bool(raw.get("keep_eye_contact", composition == "talking_head")),
            "composition_mode": composition,
            "layout_variant": layout,
            "subject_mode": subject,
            "visual_style": style,
            "visual_brief": str(raw.get("visual_brief") or raw.get("editorial_purpose") or "").strip(),
            "motion_brief": str(raw.get("motion_brief") or "Subtle physically believable motion").strip(),
            "text_overlay": str(raw.get("text_overlay") or "").strip(),
            "camera_move": camera,
            "transition_in": transition_in,
            "transition_out": transition_out,
            "emphasis_preset": emphasis,
            "caption_mode": str(raw.get("caption_mode") or "off") if str(raw.get("caption_mode") or "off") in {"off", "auto", "on"} else "off",
            "sound_intent": [str(item) for item in (raw.get("sound_intent") or [])][:4],
            "priority": str(raw.get("priority") or "useful") if str(raw.get("priority") or "useful") in {"essential", "useful", "optional"} else "useful",
            "safety": list(raw.get("safety") or ["Preserve clinical meaning", "No text inside generated image", "No distorted teeth or anatomy"]),
            "operator_visible": bool(raw.get("operator_visible", True)),
        }
        # Preserve the Director's explicit, schema-valid medium.  Falling back
        # to keyword inference here can misclassify a dental diagram as a
        # timing graphic merely because its brief says "after a meal".
        explicit_strategy = str(raw.get("visual_strategy") or "")
        scene["visual_strategy"] = (
            explicit_strategy
            if explicit_strategy in {"none", "generated_photo", "dental_diagram", "editorial_graphic"}
            else visual_strategy_for(scene)
        )
        scenes.append(scene)
        previous_end = end
    return {
        "version": "2.1",
        "summary": str(payload.get("summary") or "V2.1 editorial scene graph"),
        "fps": fps,
        "duration_seconds": duration,
        "scenes": scenes,
    }


def editorial_to_broll_plan(editorial_plan: dict[str, Any], visual_bible: dict[str, Any]) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    for scene in editorial_plan.get("scenes", []):
        layout = scene["layout_variant"]
        strategy = str(scene.get("visual_strategy") or visual_strategy_for(scene))
        talking_head = scene["composition_mode"] == "talking_head" or strategy == "none"
        slot_id = f"broll_{len(slots)+1:03d}"
        slots.append({
            "slot_id": slot_id,
            "scene_id": scene["scene_id"],
            "start": scene["start"],
            "end": scene["end"],
            "start_frame": scene["start_frame"],
            "end_frame": scene["end_frame"],
            "duration": scene["duration"],
            "transcript": scene["narration"],
            "purpose": scene["editorial_purpose"],
            "priority": scene["priority"],
            "composition_mode": scene["composition_mode"],
            "layout_variant": layout,
            "subject_mode": scene["subject_mode"],
            "layout_template": LAYOUT_COMPATIBILITY[layout],
            "visual_type": scene["visual_style"],
            "visual_style": scene["visual_style"],
            "visual_strategy": strategy,
            "provider_usage": strategy == "generated_photo",
            "operator_visible": bool(scene.get("operator_visible", True)),
            "keep_subject_foreground": scene["subject_mode"] == "matte_foreground",
            "panel_region": _panel_region(layout),
            "show_caption": scene["caption_mode"] == "on",
            "caption_position": "auto",
            "still_brief": scene["visual_brief"],
            "motion_brief": scene["motion_brief"],
            "text_overlay": scene["text_overlay"],
            "camera_move": scene["camera_move"],
            "transition_in": scene["transition_in"],
            "transition_out": scene["transition_out"],
            "emphasis_preset": scene["emphasis_preset"],
            "sound_intent": scene["sound_intent"],
            "visual_bible": visual_bible,
            "safety": scene["safety"],
            "status": "talking_head" if talking_head else "suggested",
            "selected_still": None,
            "selected_motion": None,
            "candidate_review": None,
            "versions": {"stills": [], "motion": []},
            "review": {"plan": "keep_talking_head" if talking_head else None, "still": None, "motion": None},
        })
    return {
        "version": "2.1",
        "summary": editorial_plan.get("summary"),
        "fps": editorial_plan.get("fps", 30),
        "duration_seconds": editorial_plan.get("duration_seconds", 0),
        "visual_bible": visual_bible,
        "budget_report": editorial_plan.get("budget_report", {}),
        "fallback_used": bool(editorial_plan.get("fallback_used", False)),
        "slots": slots,
    }


def _default_subject(layout: str) -> str:
    if layout == "full_broll":
        return "hidden"
    if layout == "layered_foreground":
        return "matte_foreground"
    if layout == "picture_in_picture":
        return "picture_in_picture"
    if layout == "talking_head":
        return "original"
    return "cropped_original"


def _panel_region(layout: str) -> float:
    if layout in {"full_broll", "picture_in_picture"}:
        return 1.0
    if layout in {"broll_top_speaker_bottom", "speaker_top_broll_bottom"}:
        return 0.56
    if layout in {"speaker_left_broll_right", "broll_left_speaker_right"}:
        return 0.52
    return 0.44


def _fallback_editorial(transcript: dict[str, Any], visual: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    duration = float(transcript.get("duration_seconds") or 0)
    narration = " ".join(str(item.get("text") or "").strip() for item in transcript.get("phrases") or []).strip()
    return {
        "summary": "Safe talking-head fallback; rerun the Editorial Director before generating visual media",
        "scenes": [{
            "start": 0.0,
            "end": duration,
            "narration": narration,
            "editorial_purpose": "Preserve the complete cleaned talking-head delivery",
            "energy": 0.45,
            "concept_density": 0.4,
            "keep_eye_contact": True,
            "composition_mode": "talking_head",
            "layout_variant": "talking_head",
            "subject_mode": "original",
            "visual_style": "natural_lifestyle",
            "visual_strategy": "none",
            "visual_brief": "",
            "motion_brief": "",
            "camera_move": "static",
            "transition_in": "direct_cut",
            "transition_out": "direct_cut",
            "emphasis_preset": "none",
            "caption_mode": "off",
            "sound_intent": [],
            "priority": "essential",
            "safety": ["Preserve clinical meaning", "Do not start paid media generation from fallback"],
        }],
    }


def _fallback_visual_bible(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "look": "clean restrained dental education with natural lifestyle inserts",
        "contrast": "medium with protected skin tones",
        "saturation": "natural; stronger colour only in simple editorial graphics",
        "lighting": "soft natural daylight for photography; flat clean lighting for diagrams",
        "depth": "realistic photographic depth for lifestyle scenes; clear flat layers for diagrams",
        "palette": ["warm cream", "clinic white", "restrained teal", "coral warning accent"],
        "backgrounds": ["authentic clinic", "warm neutral home", "clean off-white diagram field"],
        "camera_language": ["natural 35mm lifestyle", "stable macro only when physically credible", "simple asymmetric composition"],
        "texture_language": ["real food texture", "real brush materials", "clean vector dental layers"],
        "avoid": ["waxy anatomy", "floating teeth", "literal melting enamel", "collage seams", "fake text", "generic stock smile", "excessive teal glow"],
        "medical_rules": ["use clinically restrained generated illustrations for anatomy and mechanisms", "no impossible brush geometry", "no diagnosis claims not present in narration"],
    }


def _compact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "aspect_ratio", "editing_profile", "editing_intensity", "visual_generation_style",
        "foreground_treatment", "sfx_density", "preferred_layouts", "captions_mode",
        "image_candidates_per_slot", "max_visual_scenes", "max_broll_coverage",
        "max_initial_generations", "max_motion_scenes",
    )
    return {key: settings.get(key) for key in keys}


def _compact_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": transcript.get("duration_seconds"),
        "language": transcript.get("language_code") or transcript.get("language"),
        "phrases": transcript.get("phrases") or [],
    }


def _compact_visual(visual: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": visual.get("duration_seconds"),
        "windows": visual.get("windows") or [],
        "reframe_summary": visual.get("reframe_summary") or {},
        "recommendation": visual.get("recommendation"),
    }
