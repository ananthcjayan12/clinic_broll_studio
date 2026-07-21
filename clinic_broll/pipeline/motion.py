from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import load_run
from ..providers.grok_media import generate_media
from ..providers.registry import call_task_json
from .common import load_prompt, load_schema, run_ffmpeg


def run(run_id: str, *, slot_id: str | None = None, force: bool = False) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json")
    if not plan:
        raise RuntimeError("B-roll plan is missing")
    eligible = [
        slot for slot in plan["slots"]
        if (slot_id is None or slot["slot_id"] == slot_id)
        and slot["status"] in {"still_approved", "motion_review", "motion_approved"}
        and slot.get("selected_still")
    ]
    if not eligible:
        raise RuntimeError("No still-approved slots are ready for image-to-video generation")
    generated = [_generate_one(paths, meta, slot, force=force) for slot in eligible]
    write_json(paths.plan / "broll_plan.json", plan)
    return {"artifacts": ["assets/motion/", "prompts/motion/", "responses/motion/", "plan/broll_plan.json"], "summary": {"generated": generated}}


def _generate_one(paths, meta: dict[str, Any], slot: dict[str, Any], *, force: bool) -> dict[str, Any]:
    existing = list(slot.get("versions", {}).get("motion") or [])
    version = len(existing) + 1
    version_id = f"v{version:02d}"
    version_dir = paths.assets / "motion" / slot["slot_id"] / version_id
    raw = version_dir / "provider-output.mp4"
    destination = version_dir / "motion.mp4"
    if destination.exists() and not force:
        return {"slot_id": slot["slot_id"], "version": version_id, "status": "cached"}
    still_path = paths.root / slot["selected_still"]
    if not still_path.exists():
        raise RuntimeError(f"Approved still is missing for {slot['slot_id']}")
    selection = meta["settings"]["task_models"]["motion_prompt"]
    system = load_prompt("motion_prompt.system.txt")
    user = load_prompt("motion_prompt.user.txt").format(slot=json.dumps(slot, ensure_ascii=False, indent=2), still_path=still_path, duration=float(slot["duration"]))
    schema = load_schema("media_prompt.schema.json")
    prompt_payload = call_task_json(task="motion_prompt", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
    full_prompt = _merge_prompt(prompt_payload, float(slot["duration"]))
    prompt_path = paths.prompts / "motion" / slot["slot_id"] / f"{version_id}.json"
    response_path = paths.responses / "motion" / slot["slot_id"] / f"{version_id}.json"
    write_json(prompt_path, {"selection": selection, "slot": slot["slot_id"], **prompt_payload})
    record = generate_media(
        provider=meta["settings"].get("media_provider", "grok_cli"),
        prompt=full_prompt,
        destination=raw,
        media_type="video",
        cwd=paths.root,
        reference=still_path,
        duration=max(1, round(float(slot["duration"]))),
        aspect_ratio=meta["settings"].get("aspect_ratio", "9:16"),
    )
    _normalize_motion(
        raw, destination, float(slot["duration"]), int(meta["settings"].get("fps", 30)),
        int(meta["settings"].get("width", 1080)), int(meta["settings"].get("height", 1920)),
    )
    record.update({"path": str(destination), "version": version_id})
    write_json(response_path, record)
    relative = destination.relative_to(paths.root).as_posix()
    slot.setdefault("versions", {}).setdefault("motion", []).append({"version": version_id, "path": relative, "created_at": time.time(), "prompt": prompt_payload, "status": "review"})
    slot["status"] = "motion_review"
    return {"slot_id": slot["slot_id"], "version": version_id, "status": "generated", "path": relative}


def _merge_prompt(payload: dict[str, Any], duration: float) -> str:
    constraints = "\n".join(f"- {item}" for item in payload.get("negative_constraints") or [])
    return f"{payload['prompt']}\n\nTarget duration: {duration:.2f} seconds.\nSTRICT NEGATIVE CONSTRAINTS\n{constraints}\nNo text, morphing, new teeth, new instruments, or camera whip."


def _normalize_motion(raw: Path, destination: Path, duration: float, fps: int, width: int, height: int) -> None:
    # The immutable timeline belongs to the local compositor, not the generator.
    # Loop or trim the provider result to the exact slot duration.
    run_ffmpeg([
        "-stream_loop", "-1", "-i", str(raw), "-t", f"{duration:.6f}",
        "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ])
