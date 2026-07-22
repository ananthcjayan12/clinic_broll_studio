from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.grok_media import generate_media
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema


def run(run_id: str, *, slot_id: str | None = None, force: bool = False) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
    visual_bible = read_json(paths.editorial / "visual-bible.json", plan.get("visual_bible", {}) if plan else {})
    if not plan:
        raise RuntimeError("V2 editorial compatibility plan is missing")
    report = plan.get("budget_report") or {}
    if report.get("violations"):
        raise RuntimeError(
            "Visual generation is blocked because the editorial budget has violations: "
            + ", ".join(str(item) for item in report["violations"])
        )
    eligible = [
        slot for slot in plan["slots"]
        if (slot_id is None or slot["slot_id"] == slot_id)
        and slot["status"] in {"plan_approved", "still_review", "still_approved"}
        and slot.get("composition_mode") != "talking_head"
        and slot.get("visual_strategy") != "none"
    ]
    if not eligible:
        raise RuntimeError("No editorially approved visual scenes are ready for generation")
    generated: list[dict[str, Any]] = []
    for slot in eligible:
        count = 1 if slot_id is not None else _candidate_count(meta, slot)
        for _ in range(count):
            generated.append(_generate_one(paths, meta, slot, visual_bible, force=force))
        _review_candidates(paths, meta, slot, visual_bible)
        write_json(paths.plan / "broll_plan.json", plan)
    return {
        "artifacts": ["assets/stills/", "prompts/stills/", "responses/stills/", "plan/broll_plan.json"],
        "summary": {
            "generated": generated,
            "reviewed_scenes": len(eligible),
            "provider_generations": sum(1 for item in generated if item.get("provider_usage")),
            "local_graphics": 0,
        },
    }


def _candidate_count(meta: dict[str, Any], slot: dict[str, Any]) -> int:
    # The operator setting is a hard maximum. Priority never silently increases
    # a batch from two candidates to three.
    return max(1, min(int(meta["settings"].get("image_candidates_per_slot") or 1), 3))


def _generate_one(paths, meta: dict[str, Any], slot: dict[str, Any], visual_bible: dict[str, Any], *, force: bool) -> dict[str, Any]:
    existing = list(slot.get("versions", {}).get("stills") or [])
    version = len(existing) + 1
    version_id = f"v{version:02d}"
    version_dir = paths.assets / "stills" / slot["slot_id"] / version_id
    destination = version_dir / "still.png"
    if destination.exists() and not force:
        return {"slot_id": slot["slot_id"], "version": version_id, "status": "cached", "provider_usage": False}

    strategy = str(slot.get("visual_strategy") or "generated_photo")
    prompt_payload: dict[str, Any] = {}
    selection = meta["settings"]["task_models"]["image_prompt"]
    system = load_prompt("image_prompt.system.txt")
    user = load_prompt("image_prompt.user.txt").format(
        slot=json.dumps(slot, ensure_ascii=False, indent=2),
        visual_bible=json.dumps(visual_bible, ensure_ascii=False, indent=2),
        width=meta["settings"]["width"],
        height=meta["settings"]["height"],
        aspect_ratio=meta["settings"]["aspect_ratio"],
    )
    schema = load_schema("media_prompt.schema.json")
    append_log(paths, f"Visual {slot['slot_id']} {version_id}: requesting image-model direction from {selection['provider']}")
    prompt_payload = call_task_json(task="image_prompt", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
    full_prompt = _merge_prompt(prompt_payload, slot, visual_bible)
    prompt_path = paths.prompts / "stills" / slot["slot_id"] / f"{version_id}.json"
    write_json(prompt_path, {"selection": selection, "scene": slot["scene_id"], "slot": slot["slot_id"], "visual_bible": visual_bible, **prompt_payload})
    append_log(paths, f"Visual {slot['slot_id']} {version_id}: generating with the configured image model")
    record = generate_media(
        provider=meta["settings"].get("media_provider", "grok_cli"),
        prompt=full_prompt,
        destination=destination,
        media_type="image",
        cwd=paths.root,
        aspect_ratio=meta["settings"].get("aspect_ratio", "9:16"),
    )
    provider_usage = True

    _normalize_png(destination)
    metrics = _image_metrics(destination)
    record.update({"path": str(destination), "version": version_id, "metrics": metrics, "provider_usage": provider_usage})
    response_path = paths.responses / "stills" / slot["slot_id"] / f"{version_id}.json"
    write_json(response_path, record)
    relative = destination.relative_to(paths.root).as_posix()
    slot.setdefault("versions", {}).setdefault("stills", []).append({
        "version": version_id,
        "path": relative,
        "created_at": time.time(),
        "prompt": prompt_payload,
        "metrics": metrics,
        "status": "review",
        "provider_usage": provider_usage,
        "visual_strategy": strategy,
    })
    slot["status"] = "still_review"
    return {
        "slot_id": slot["slot_id"],
        "version": version_id,
        "status": "generated",
        "path": relative,
        "metrics": metrics,
        "provider_usage": provider_usage,
        "visual_strategy": strategy,
    }


def _review_candidates(paths, meta: dict[str, Any], slot: dict[str, Any], visual_bible: dict[str, Any]) -> None:
    versions = (slot.get("versions") or {}).get("stills") or []
    if not versions:
        return
    candidates = [{"path": item["path"], "metrics": item.get("metrics", {})} for item in versions]
    review_dir = paths.responses / "stills" / slot["slot_id"]
    # Generation is intentionally operator-led: retain the newest image and
    # let the user decide whether to use it.  No automatic reviewer may reject
    # an image or force another paid generation.
    selected = candidates[-1]
    metrics = selected.get("metrics") or {}
    review = {
        "decision": "manual_medical_review",
        "selected_path": selected["path"],
        "scores": [{
            "path": selected["path"],
            "naturalness": 0,
            "colour_quality": round(float(metrics.get("saturation", 0)) * 100, 2),
            "visual_interest": round(float(metrics.get("contrast", 0)) * 100, 2),
            "composition_fit": 0,
            "medical_accuracy": 0,
            "motion_potential": 0,
            "artificial_appearance": 0,
        }],
        "reason": "Newest generated version retained for your review; no automatic rejection is applied.",
        "requires_human_review": True,
    }
    selection = {"provider": "operator", "model": "manual_selection"}
    slot["candidate_review"] = review
    write_json(review_dir / "candidate-review.json", {"selection": selection, "review": review})
    append_log(paths, f"Visual {slot['slot_id']}: newest generated version is ready for your review")


def _merge_prompt(payload: dict[str, Any], slot: dict[str, Any], visual_bible: dict[str, Any]) -> str:
    constraints = list(payload.get("negative_constraints") or [])
    constraints.extend([
        "one coherent image, not a collage",
        "no detached or floating teeth",
        "no waxy, melting, rubbery, translucent, or deformed anatomy",
        "no impossible brush contact or duplicated objects",
        "no written text, watermark, logo, UI, or doctor face",
    ])
    return (
        f"{str(payload.get('prompt') or slot.get('still_brief') or '').strip()}\n\n"
        f"VISUAL MEDIUM: {slot.get('visual_strategy')}. Create one polished, high-end editorial image with a single clear idea. "
        "Use a vibrant premium short-form-reel finish: bold off-centre focal point, sculpted soft light, rich mint/coral/warm-cream colour contrast, tactile detail, and cinematic depth. "
        "Do not make a washed-out empty field or a flat generic textbook vector. Use clean anatomy when a tooth or brush is shown.\n"
        f"COMPOSITION: {slot.get('layout_variant')} with intentional safe space for the speaker, while the active visual region is confidently filled and readable on a phone.\n"
        f"AVOID: {'; '.join(dict.fromkeys(str(item) for item in constraints))}"
    )


def _image_metrics(path: Path) -> dict[str, float]:
    with Image.open(path).convert("RGB") as image:
        thumb = image.resize((128, 128))
        stat = ImageStat.Stat(thumb)
        means = [value / 255.0 for value in stat.mean]
        deviations = [value / 255.0 for value in stat.stddev]
        brightness = sum(means) / 3
        contrast = sum(deviations) / 3
        saturation = (max(means) - min(means)) / max(max(means), 1e-6)
        return {"brightness": round(brightness, 4), "contrast": round(contrast, 4), "saturation": round(saturation, 4)}


def _normalize_png(path: Path) -> None:
    with Image.open(path) as image:
        converted = image.convert("RGB")
        temporary = path.with_suffix(".normalized.png")
        converted.save(temporary, format="PNG", optimize=True)
    temporary.replace(path)
