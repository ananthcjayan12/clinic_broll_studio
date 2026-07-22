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
from .graphics import render_local_graphic


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
            "local_graphics": sum(1 for item in generated if not item.get("provider_usage")),
        },
    }


def _candidate_count(meta: dict[str, Any], slot: dict[str, Any]) -> int:
    if slot.get("visual_strategy") in {"dental_diagram", "editorial_graphic"}:
        return 1
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
    if strategy in {"dental_diagram", "editorial_graphic"}:
        append_log(paths, f"Visual {slot['slot_id']} {version_id}: rendering deterministic {strategy}")
        record = render_local_graphic(
            destination,
            slot,
            width=int(meta["settings"].get("width") or 1080),
            height=int(meta["settings"].get("height") or 1920),
        )
        selection = {"provider": "local", "model": strategy}
        provider_usage = False
    else:
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
        append_log(paths, f"Visual {slot['slot_id']} {version_id}: requesting concise photographic direction from {selection['provider']}")
        prompt_payload = call_task_json(task="image_prompt", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
        full_prompt = _merge_prompt(prompt_payload, slot, visual_bible)
        prompt_path = paths.prompts / "stills" / slot["slot_id"] / f"{version_id}.json"
        write_json(prompt_path, {"selection": selection, "scene": slot["scene_id"], "slot": slot["slot_id"], "visual_bible": visual_bible, **prompt_payload})
        append_log(paths, f"Visual {slot['slot_id']} {version_id}: generating one authentic lifestyle image")
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
    strategy = str(slot.get("visual_strategy") or "generated_photo")
    if strategy in {"dental_diagram", "editorial_graphic"}:
        selected = candidates[-1]["path"]
        review = {
            "decision": "approve",
            "selected_path": selected,
            "scores": [{
                "path": selected,
                "naturalness": 90,
                "colour_quality": 86,
                "visual_interest": 82,
                "composition_fit": 88,
                "medical_accuracy": 92 if strategy == "dental_diagram" else 95,
                "motion_potential": 80,
                "artificial_appearance": 8,
            }],
            "reason": "Deterministic local graphic selected; no image-model anatomy or physics was used.",
            "requires_human_review": strategy == "dental_diagram",
        }
        selection = {"provider": "local", "model": strategy}
    else:
        selection = meta["settings"]["task_models"]["visual_candidate_reviewer"]
        system = load_prompt("visual_candidate_reviewer.system.txt")
        user = load_prompt("visual_candidate_reviewer.user.txt").format(
            scene=json.dumps(slot, ensure_ascii=False, indent=2),
            visual_bible=json.dumps(visual_bible, ensure_ascii=False, indent=2),
            candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
        )
        try:
            review = call_task_json(
                task="visual_candidate_reviewer",
                selection=selection,
                system=system,
                user=user,
                cwd=paths.root,
                output_schema=load_schema("visual_candidate_review.schema.json"),
            )
            valid_paths = {item["path"] for item in candidates}
            if review.get("selected_path") not in valid_paths:
                review["selected_path"] = None
            review = _enforce_review_thresholds(review)
        except Exception as exc:
            review = _deterministic_review(candidates)
            review["reason"] += f" Automated semantic review unavailable: {exc}"
    slot["candidate_review"] = review
    write_json(review_dir / "candidate-review.json", {"selection": selection, "review": review})
    if review.get("decision") == "reject_all":
        append_log(paths, f"Visual {slot['slot_id']}: all generated candidates rejected; revise the visual strategy or prompt")
    else:
        append_log(paths, f"Visual {slot['slot_id']}: reviewer recommends {review.get('selected_path') or 'manual review'}")


def _enforce_review_thresholds(review: dict[str, Any]) -> dict[str, Any]:
    scores = list(review.get("scores") or [])
    acceptable = [
        item for item in scores
        if float(item.get("medical_accuracy", 0)) >= 85
        and float(item.get("artificial_appearance", 100)) <= 30
        and float(item.get("composition_fit", 0)) >= 70
    ]
    selected = review.get("selected_path")
    if not acceptable or selected not in {item.get("path") for item in acceptable}:
        review["decision"] = "reject_all"
        review["selected_path"] = None
        review["requires_human_review"] = True
        review["reason"] = "All candidates failed the minimum medical-accuracy, composition, or artificiality threshold. " + str(review.get("reason") or "")
    else:
        review["decision"] = review.get("decision") or "manual_medical_review"
    return review


def _deterministic_review(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for item in candidates:
        metrics = item.get("metrics") or {}
        saturation = float(metrics.get("saturation", 0.4))
        contrast = float(metrics.get("contrast", 0.3))
        brightness = float(metrics.get("brightness", 0.5))
        naturalness = max(0.0, 100 - abs(brightness - 0.55) * 90 - max(0, saturation - 0.82) * 100)
        colour = max(0.0, min(100.0, saturation * 110 + contrast * 35))
        interest = max(0.0, min(100.0, contrast * 120 + saturation * 45))
        scored.append({
            "path": item["path"],
            "naturalness": round(naturalness, 2),
            "colour_quality": round(colour, 2),
            "visual_interest": round(interest, 2),
            "composition_fit": 60,
            "medical_accuracy": 0,
            "motion_potential": 60,
            "artificial_appearance": round(max(0.0, 100 - naturalness), 2),
        })
    return {
        "decision": "reject_all",
        "selected_path": None,
        "scores": scored,
        "reason": "Colour metrics cannot verify anatomy or physical realism, so automatic approval is disabled.",
        "requires_human_review": True,
    }


def _merge_prompt(payload: dict[str, Any], slot: dict[str, Any], visual_bible: dict[str, Any]) -> str:
    constraints = list(payload.get("negative_constraints") or [])
    constraints.extend([
        "one coherent photograph, not a collage",
        "no detached or floating teeth",
        "no waxy, melting, rubbery, translucent, or deformed anatomy",
        "no impossible brush contact or duplicated objects",
        "no written text, watermark, logo, UI, or doctor face",
    ])
    return (
        f"{str(payload.get('prompt') or slot.get('still_brief') or '').strip()}\n\n"
        "Create one authentic real-world lifestyle photograph. Keep the scene simple and physically believable. "
        "Do not visualise microscopic enamel physics or anatomy in photoreal form; those concepts use local diagrams.\n"
        f"COMPOSITION: {slot.get('layout_variant')} with one clear subject and uncluttered negative space.\n"
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
