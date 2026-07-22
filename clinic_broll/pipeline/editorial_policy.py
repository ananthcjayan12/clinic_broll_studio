from __future__ import annotations

from copy import deepcopy
from typing import Any

STYLE_ALIASES = {
    "natural": "natural_lifestyle",
    "natural_colorful": "mixed",
    "cinematic": "natural_lifestyle",
    "medical_illustration": "clean_medical_illustration",
}

PRIORITY_SCORE = {"essential": 3.0, "useful": 2.0, "optional": 1.0}

DEFAULT_BUDGETS = {
    "low": {
        "max_visual_scenes": 3,
        "max_broll_coverage": 0.22,
        "max_initial_generations": 3,
        "max_motion_scenes": 1,
    },
    "medium": {
        "max_visual_scenes": 5,
        "max_broll_coverage": 0.32,
        "max_initial_generations": 6,
        "max_motion_scenes": 2,
    },
    "high": {
        "max_visual_scenes": 7,
        "max_broll_coverage": 0.42,
        "max_initial_generations": 9,
        "max_motion_scenes": 3,
    },
}

VISUAL_STRATEGIES = {"none", "generated_photo", "dental_diagram", "editorial_graphic"}

_LIFESTYLE_WORDS = {
    "food", "meal", "eat", "eating", "tomato", "lime", "lemon", "citrus", "yogurt",
    "bathroom", "home", "water", "glass", "kitchen", "dining", "breakfast", "lunch",
    "ഭക്ഷണം", "കഴി", "തക്കാളി", "നാരങ്ങ", "വെള്ളം",
}
_DENTAL_WORDS = {
    "tooth", "teeth", "enamel", "dentin", "gum", "acid", "acidic", "ph", "saliva",
    "erosion", "sensitivity", "abrasion", "bristle", "demineral", "cavity", "caries",
    "പല്ല", "ഇനാമൽ", "ആസിഡ", "സെൻസിറ്റിവിറ്റി", "ബ്രഷ്",
}
_EDITORIAL_WORDS = {
    "wait", "minute", "minutes", "clock", "time", "avoid", "warning", "recommend",
    "immediately", "later", "before", "after", "30", "60", "കാത്തിരി", "മിനിറ്റ്",
    "ഉടനെ", "ശേഷം",
}


def repair_editorial_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair harmless model aliases before deterministic normalization.

    Provider responses occasionally use the run-level style name (for example
    ``natural_colorful``) where the scene schema expects a scene-level style.
    Treat those aliases as repairable instead of throwing away the whole plan.
    """
    repaired = deepcopy(payload or {})
    for scene in repaired.get("scenes") or []:
        style = str(scene.get("visual_style") or "natural_lifestyle")
        scene["visual_style"] = STYLE_ALIASES.get(style, style)
        if scene.get("layout_variant") == "talking_head":
            scene["composition_mode"] = "talking_head"
            scene["subject_mode"] = "original"
    return repaired


def visual_strategy_for(scene: dict[str, Any]) -> str:
    if str(scene.get("composition_mode")) == "talking_head":
        return "none"
    explicit = str(scene.get("visual_strategy") or "")
    if explicit in VISUAL_STRATEGIES:
        return explicit
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("narration", "editorial_purpose", "visual_brief", "text_overlay")
    ).lower()
    tokens = set(text.replace("/", " ").replace("-", " ").split())
    if any(word in text for word in _EDITORIAL_WORDS):
        return "editorial_graphic"
    if any(word in text for word in _DENTAL_WORDS):
        return "dental_diagram"
    if any(word in text for word in _LIFESTYLE_WORDS):
        return "generated_photo"
    style = STYLE_ALIASES.get(str(scene.get("visual_style") or ""), str(scene.get("visual_style") or ""))
    if style in {"clean_medical_illustration", "colorful_macro"}:
        return "dental_diagram"
    if style == "playful_explainer":
        return "editorial_graphic"
    if tokens:
        return "generated_photo"
    return "editorial_graphic"


def resolve_budget(settings: dict[str, Any], duration: float) -> dict[str, Any]:
    intensity = str(settings.get("editing_intensity") or "medium")
    defaults = dict(DEFAULT_BUDGETS.get(intensity, DEFAULT_BUDGETS["medium"]))
    defaults["max_visual_scenes"] = max(0, min(int(settings.get("max_visual_scenes") or defaults["max_visual_scenes"]), 12))
    defaults["max_broll_coverage"] = max(0.0, min(float(settings.get("max_broll_coverage") or defaults["max_broll_coverage"]), 0.60))
    defaults["max_initial_generations"] = max(0, min(int(settings.get("max_initial_generations") or defaults["max_initial_generations"]), 24))
    defaults["max_motion_scenes"] = max(0, min(int(settings.get("max_motion_scenes") or defaults["max_motion_scenes"]), 8))
    defaults["duration_seconds"] = max(0.0, float(duration or 0))
    return defaults


def apply_editorial_policy(plan: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Apply hard local limits after the LLM plan.

    This function is intentionally deterministic. Prompts can express a desired
    density, but only local code is allowed to decide whether a paid generation
    batch is small enough to proceed.
    """
    result = deepcopy(plan)
    duration = float(result.get("duration_seconds") or 0)
    fps = int(result.get("fps") or settings.get("fps") or 30)
    budget = resolve_budget(settings, duration)
    scenes = [dict(item) for item in result.get("scenes") or []]

    for scene in scenes:
        style = str(scene.get("visual_style") or "natural_lifestyle")
        scene["visual_style"] = STYLE_ALIASES.get(style, style)
        scene["operator_visible"] = bool(scene.get("operator_visible", True))
        scene["visual_strategy"] = visual_strategy_for(scene)

    # Hooks and closing advice should preserve eye contact unless the operator
    # explicitly changes them later.
    if scenes:
        _make_talking_head(scenes[0])
        _make_talking_head(scenes[-1])

    visual = [scene for scene in scenes if scene.get("visual_strategy") != "none"]
    ranked = sorted(visual, key=_scene_score, reverse=True)
    keep_ids = {id(scene) for scene in ranked[: budget["max_visual_scenes"]]}
    for scene in visual:
        if id(scene) not in keep_ids:
            _make_talking_head(scene)

    # Enforce the coverage ceiling by removing the least valuable visual
    # moments first. Local diagrams are still visual exposure and count toward
    # pacing even though they do not use a paid image model.
    _trim_to_coverage(scenes, duration * budget["max_broll_coverage"])
    _trim_consecutive_visuals(scenes, maximum_seconds=6.0)

    # Generated photographs are the only initial paid image requests. Keep
    # deterministic diagrams when possible and demote excess photos to the
    # talking head rather than silently starting a huge batch.
    configured_candidates = max(1, min(int(settings.get("image_candidates_per_slot") or 1), 3))
    max_photo_scenes = budget["max_initial_generations"] // configured_candidates
    photos = sorted(
        [scene for scene in scenes if scene.get("visual_strategy") == "generated_photo"],
        key=_scene_score,
        reverse=True,
    )
    for scene in photos[max_photo_scenes:]:
        _make_talking_head(scene)

    scenes = _merge_adjacent_scenes(scenes, fps=fps)
    for index, scene in enumerate(scenes, start=1):
        scene["scene_id"] = f"scene_{index:03d}"
        scene["start_frame"] = round(float(scene.get("start") or 0) * fps)
        scene["end_frame"] = round(float(scene.get("end") or 0) * fps)
        scene["duration"] = round(float(scene.get("end") or 0) - float(scene.get("start") or 0), 3)

    result["scenes"] = scenes
    result["budget_report"] = budget_report(scenes, duration, settings, budget)
    result["policy_applied"] = True
    return result


def budget_report(
    scenes: list[dict[str, Any]],
    duration: float,
    settings: dict[str, Any],
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    budget = budget or resolve_budget(settings, duration)
    visual = [scene for scene in scenes if scene.get("visual_strategy") not in {None, "none"}]
    photos = [scene for scene in visual if scene.get("visual_strategy") == "generated_photo"]
    local = [scene for scene in visual if scene.get("visual_strategy") in {"dental_diagram", "editorial_graphic"}]
    coverage_seconds = round(sum(max(0.0, float(scene.get("end") or 0) - float(scene.get("start") or 0)) for scene in visual), 3)
    coverage_ratio = round(coverage_seconds / duration, 4) if duration else 0.0
    candidates = max(1, min(int(settings.get("image_candidates_per_slot") or 1), 3))
    expected = len(photos) * candidates
    violations: list[str] = []
    if len(visual) > budget["max_visual_scenes"]:
        violations.append("visual_scene_limit")
    if coverage_ratio > budget["max_broll_coverage"] + 0.001:
        violations.append("coverage_limit")
    if expected > budget["max_initial_generations"]:
        violations.append("generation_limit")
    return {
        "duration_seconds": round(duration, 3),
        "editorial_scenes": len([scene for scene in scenes if scene.get("operator_visible", True)]),
        "visual_scenes": len(visual),
        "generated_photo_scenes": len(photos),
        "local_graphic_scenes": len(local),
        "talking_head_scenes": len(scenes) - len(visual),
        "visual_coverage_seconds": coverage_seconds,
        "visual_coverage_ratio": coverage_ratio,
        "image_candidates_per_scene": candidates,
        "expected_image_generations": expected,
        "limits": {
            "max_visual_scenes": budget["max_visual_scenes"],
            "max_broll_coverage": budget["max_broll_coverage"],
            "max_initial_generations": budget["max_initial_generations"],
            "max_motion_scenes": budget["max_motion_scenes"],
        },
        "violations": violations,
        "approved_for_generation": not violations,
    }


def _scene_score(scene: dict[str, Any]) -> float:
    return (
        PRIORITY_SCORE.get(str(scene.get("priority") or "useful"), 2.0)
        + float(scene.get("concept_density") or 0.5)
        + min(max(float(scene.get("duration") or 0), 0.0), 6.0) / 12.0
        + (0.35 if scene.get("visual_strategy") in {"dental_diagram", "editorial_graphic"} else 0.0)
    )


def _make_talking_head(scene: dict[str, Any]) -> None:
    scene.update({
        "composition_mode": "talking_head",
        "layout_variant": "talking_head",
        "subject_mode": "original",
        "visual_strategy": "none",
        "visual_brief": "",
        "motion_brief": "",
        "text_overlay": "",
        "transition_in": "direct_cut",
        "transition_out": "direct_cut",
        "sound_intent": [],
    })


def _trim_to_coverage(scenes: list[dict[str, Any]], maximum_seconds: float) -> None:
    visual = [scene for scene in scenes if scene.get("visual_strategy") != "none"]
    coverage = sum(float(scene.get("duration") or 0) for scene in visual)
    for scene in sorted(visual, key=_scene_score):
        if coverage <= maximum_seconds + 1e-6:
            break
        coverage -= float(scene.get("duration") or 0)
        _make_talking_head(scene)


def _trim_consecutive_visuals(scenes: list[dict[str, Any]], maximum_seconds: float) -> None:
    run: list[dict[str, Any]] = []
    for scene in scenes + [{"visual_strategy": "none"}]:
        if scene.get("visual_strategy") != "none":
            run.append(scene)
            continue
        while sum(float(item.get("duration") or 0) for item in run) > maximum_seconds and run:
            weakest = min(run, key=_scene_score)
            _make_talking_head(weakest)
            run.remove(weakest)
        run = []


def _merge_adjacent_scenes(scenes: list[dict[str, Any]], *, fps: int) -> list[dict[str, Any]]:
    if not scenes:
        return []
    merged: list[dict[str, Any]] = []
    for current in sorted(scenes, key=lambda item: float(item.get("start") or 0)):
        current = dict(current)
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        same_treatment = (
            previous.get("composition_mode") == current.get("composition_mode")
            and previous.get("layout_variant") == current.get("layout_variant")
            and previous.get("visual_strategy") == current.get("visual_strategy")
            and previous.get("camera_move", "static") == current.get("camera_move", "static")
        )
        touching = float(current.get("start") or 0) - float(previous.get("end") or 0) <= 0.08
        combined_duration = float(current.get("end") or 0) - float(previous.get("start") or 0)
        if same_treatment and touching and combined_duration <= 8.0:
            previous["end"] = current.get("end")
            previous["narration"] = " ".join(filter(None, [str(previous.get("narration") or "").strip(), str(current.get("narration") or "").strip()])).strip()
            previous["visual_brief"] = " ".join(filter(None, [str(previous.get("visual_brief") or "").strip(), str(current.get("visual_brief") or "").strip()])).strip()
            previous["operator_visible"] = bool(previous.get("operator_visible", True) or current.get("operator_visible", True))
            previous["duration"] = round(combined_duration, 3)
            previous["end_frame"] = round(float(previous["end"]) * fps)
        else:
            merged.append(current)
    return merged
