from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PIL import Image

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
    if not plan:
        raise RuntimeError("B-roll plan is missing")
    eligible = [
        slot for slot in plan["slots"]
        if (slot_id is None or slot["slot_id"] == slot_id)
        and slot["status"] in {"plan_approved", "still_review", "still_approved"}
    ]
    if not eligible:
        raise RuntimeError("No plan-approved B-roll slots are ready for still generation")
    count = int(meta["settings"].get("image_candidates_per_slot") or 1)
    generated = []
    for slot in eligible:
        for _ in range(count if slot_id is None else 1):
            generated.append(_generate_one(paths, meta, plan, slot, force=force))
    write_json(paths.plan / "broll_plan.json", plan)
    return {"artifacts": ["assets/stills/", "prompts/stills/", "responses/stills/", "plan/broll_plan.json"], "summary": {"generated": generated}}


def _generate_one(paths, meta: dict[str, Any], plan: dict[str, Any], slot: dict[str, Any], *, force: bool) -> dict[str, Any]:
    existing = list(slot.get("versions", {}).get("stills") or [])
    version = len(existing) + 1
    version_id = f"v{version:02d}"
    version_dir = paths.assets / "stills" / slot["slot_id"] / version_id
    destination = version_dir / "still.png"
    if destination.exists() and not force:
        return {"slot_id": slot["slot_id"], "version": version_id, "status": "cached"}
    selection = meta["settings"]["task_models"]["image_prompt"]
    system = load_prompt("image_prompt.system.txt")
    user = load_prompt("image_prompt.user.txt").format(
        slot=json.dumps(slot, ensure_ascii=False, indent=2),
        width=meta["settings"]["width"],
        height=meta["settings"]["height"],
        aspect_ratio=meta["settings"]["aspect_ratio"],
    )
    schema = load_schema("media_prompt.schema.json")
    append_log(paths, f"Still {slot['slot_id']} {version_id}: requesting visual direction from {selection['provider']}")
    prompt_payload = call_task_json(task="image_prompt", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
    full_prompt = _merge_prompt(prompt_payload)
    prompt_path = paths.prompts / "stills" / slot["slot_id"] / f"{version_id}.json"
    response_path = paths.responses / "stills" / slot["slot_id"] / f"{version_id}.json"
    write_json(prompt_path, {"selection": selection, "slot": slot["slot_id"], **prompt_payload})
    append_log(paths, f"Still {slot['slot_id']} {version_id}: generating image with {meta['settings'].get('media_provider', 'grok_cli')}")
    record = generate_media(
        provider=meta["settings"].get("media_provider", "grok_cli"),
        prompt=full_prompt,
        destination=destination,
        media_type="image",
        cwd=paths.root,
        aspect_ratio=meta["settings"].get("aspect_ratio", "9:16"),
    )
    _normalize_png(destination)
    append_log(paths, f"Still {slot['slot_id']} {version_id}: image received and normalized")
    record["path"] = str(destination)
    record["version"] = version_id
    write_json(response_path, record)
    relative = destination.relative_to(paths.root).as_posix()
    slot.setdefault("versions", {}).setdefault("stills", []).append({"version": version_id, "path": relative, "created_at": time.time(), "prompt": prompt_payload, "status": "review"})
    slot["status"] = "still_review"
    return {"slot_id": slot["slot_id"], "version": version_id, "status": "generated", "path": relative}


def _merge_prompt(payload: dict[str, Any]) -> str:
    constraints = "\n".join(f"- {item}" for item in payload.get("negative_constraints") or [])
    return f"{payload['prompt']}\n\nSTRICT NEGATIVE CONSTRAINTS\n{constraints}\nNo written text, watermark, logo, or UI inside the generated image."


def _normalize_png(path: Path) -> None:
    with Image.open(path) as image:
        converted = image.convert("RGB")
        temporary = path.with_suffix(".normalized.png")
        converted.save(temporary, format="PNG", optimize=True)
    temporary.replace(path)
