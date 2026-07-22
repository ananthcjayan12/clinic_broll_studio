from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.config import FFMPEG, FFPROBE, HYPERFRAMES, PYTHON, RUNS_ROOT, STATIC_ROOT
from ..core.io import read_json, write_json
from ..core.models import PROVIDER_CATALOG, TASK_CATALOG, validate_model_map
from ..core.paths import require_run_id, run_paths
from ..core.state import (
    STAGE_BY_NUMBER,
    create_run,
    list_runs,
    load_run,
    mark_stage,
    public_run_detail,
    rewind_run,
    save_run,
    stage_record,
)
from ..pipeline.plan import slot_action, update_slot
from ..providers.registry import availability

RUNS_ROOT.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Clinic B-roll Studio V2", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.mount("/runs", StaticFiles(directory=RUNS_ROOT), name="runs")

_processes: dict[str, subprocess.Popen[Any]] = {}
_process_lock = threading.RLock()


class StepRequest(BaseModel):
    step: int
    force: bool = False
    confirm_paid: bool = False


class ThroughRequest(BaseModel):
    target_step: int
    force: bool = False
    confirm_paid: bool = False


class RewindRequest(BaseModel):
    from_step: int


class SkipRequest(BaseModel):
    step: int


class ModelMapRequest(BaseModel):
    task_models: dict[str, dict[str, str]]


class SettingsRequest(BaseModel):
    settings: dict[str, Any]


class SlotUpdateRequest(BaseModel):
    updates: dict[str, Any]


class SlotActionRequest(BaseModel):
    action: str


class ApproveAllRequest(BaseModel):
    level: str


class PaidActionRequest(BaseModel):
    confirm_paid: bool = False


class RefineSlotRequest(BaseModel):
    confirm_paid: bool = False
    instruction: str = ""


class SoundCueUpdateRequest(BaseModel):
    updates: dict[str, Any]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "providers": availability(), "tools": _doctor_tools()}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {
        "providers": PROVIDER_CATALOG,
        "tasks": TASK_CATALOG,
        "v2": {
            "editing_profiles": ["clean_medical", "natural_colorful", "modern_tech_explainer", "high_energy_reel", "minimal_professional", "custom"],
            "editing_intensities": ["low", "medium", "high"],
            "visual_styles": ["natural", "natural_colorful", "cinematic", "medical_illustration", "mixed"],
            "foreground_treatments": ["auto", "never", "only_approved", "prefer_when_clean"],
            "sfx_densities": ["off", "low", "medium", "high"],
            "layout_variants": [
                "talking_head", "broll_top_speaker_bottom", "speaker_top_broll_bottom",
                "speaker_left_broll_right", "broll_left_speaker_right", "picture_in_picture",
                "floating_visual", "full_broll", "layered_foreground",
            ],
        },
    }


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return list_runs()


@app.post("/api/runs")
async def create(
    run_id: str = Form(...),
    settings: str = Form("{}"),
    video: UploadFile = File(...),
) -> dict[str, Any]:
    created_root: Path | None = None
    try:
        run_id = require_run_id(run_id)
        payload = _validate_create_settings(json.loads(settings or "{}"))
        suffix = Path(video.filename or ".mp4").suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
            raise ValueError("Upload an MP4, MOV, M4V, or WebM video")
        create_run(run_id, original_filename=video.filename or "upload.mp4", settings=payload)
        paths = run_paths(run_id)
        created_root = paths.root
        destination = paths.source / f"upload{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            while chunk := await video.read(1024 * 1024):
                handle.write(chunk)
        if destination.stat().st_size == 0:
            raise ValueError("Uploaded video is empty")
        return public_run_detail(run_id)
    except Exception as exc:
        if created_root is not None:
            shutil.rmtree(created_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    try:
        detail = public_run_detail(run_id)
        detail["process"] = _process_payload(run_id)
        return detail
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
def run_log(run_id: str) -> str:
    path = run_paths(run_id).log
    return path.read_text(encoding="utf-8")[-50000:] if path.exists() else ""


@app.post("/api/runs/{run_id}/step")
def run_step(run_id: str, request: StepRequest) -> dict[str, Any]:
    _require_paid_confirmation([request.step], request.confirm_paid)
    _launch(run_id, [
        "--step", str(request.step),
        *(["--force"] if request.force else []),
        *(["--confirm-paid"] if request.confirm_paid else []),
    ])
    return {"started": True, "run_id": run_id, "step": request.step}


@app.post("/api/runs/{run_id}/through")
def run_through(run_id: str, request: ThroughRequest) -> dict[str, Any]:
    meta = load_run(run_id)
    pending = [
        int(stage["number"]) for stage in meta["stages"]
        if int(stage["number"]) <= request.target_step and stage["status"] not in {"complete", "skipped"}
    ]
    _require_paid_confirmation(pending, request.confirm_paid)
    _launch(run_id, [
        "--through", str(request.target_step),
        *(["--force"] if request.force else []),
        *(["--confirm-paid"] if request.confirm_paid else []),
    ])
    return {"started": True, "run_id": run_id, "target_step": request.target_step}


@app.post("/api/runs/{run_id}/stop")
def stop(run_id: str) -> dict[str, Any]:
    with _process_lock:
        process = _processes.get(run_id)
        if not process or process.poll() is not None:
            return {"stopped": False, "message": "No active process"}
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        return {"stopped": True}


@app.post("/api/runs/{run_id}/skip")
def skip_stage(run_id: str, request: SkipRequest) -> dict[str, Any]:
    _require_idle(run_id)
    allowed = {8, 9, 10, 12}
    if request.step not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Only visual generation, visual preview, motion generation, and sound design may be skipped",
        )
    meta = load_run(run_id)
    if request.step > 1 and stage_record(meta, request.step - 1)["status"] not in {"complete", "skipped"}:
        raise HTTPException(status_code=400, detail="Complete or skip the previous stage first")
    mark_stage(run_id, request.step, "skipped", summary={"reason": "Skipped by operator"})
    return public_run_detail(run_id)


@app.post("/api/runs/{run_id}/rewind")
def rewind(run_id: str, request: RewindRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        rewind_run(run_id, request.from_step)
        return public_run_detail(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/runs/{run_id}/models")
def models(run_id: str, request: ModelMapRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        meta = load_run(run_id)
        meta["settings"]["task_models"] = validate_model_map(request.task_models)
        save_run(meta)
        return public_run_detail(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/runs/{run_id}/settings")
def update_settings(run_id: str, request: SettingsRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        meta = load_run(run_id)
        allowed = {
            "captions_mode", "editing_profile", "editing_intensity", "visual_generation_style",
            "foreground_treatment", "sfx_density", "preferred_layouts", "image_candidates_per_slot",
        }
        for key, value in request.settings.items():
            if key in allowed:
                meta["settings"][key] = value
        meta["settings"].update(_validate_v2_controls(meta["settings"]))
        save_run(meta)
        return public_run_detail(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/runs/{run_id}/slots/{slot_id}")
def edit_slot(run_id: str, slot_id: str, request: SlotUpdateRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        return update_slot(run_id, slot_id, request.updates)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/slots/{slot_id}/action")
def act_slot(run_id: str, slot_id: str, request: SlotActionRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        return slot_action(run_id, slot_id, request.action)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/slots/{slot_id}/refine")
def refine_slot_endpoint(run_id: str, slot_id: str, request: RefineSlotRequest) -> dict[str, Any]:
    _require_paid_confirmation([6], request.confirm_paid)
    _launch(run_id, [
        "--slot-refine", slot_id, "--confirm-paid",
        "--instruction", request.instruction[:2000],
    ])
    return {"started": True, "slot_id": slot_id, "kind": "refinement"}


@app.post("/api/runs/{run_id}/slots/{slot_id}/regenerate-still")
def regenerate_still(run_id: str, slot_id: str, request: PaidActionRequest) -> dict[str, Any]:
    _require_paid_confirmation([8], request.confirm_paid)
    _launch(run_id, ["--slot-still", slot_id, "--force", "--confirm-paid"])
    return {"started": True, "slot_id": slot_id, "kind": "still"}


@app.post("/api/runs/{run_id}/slots/{slot_id}/regenerate-motion")
def regenerate_motion(run_id: str, slot_id: str, request: PaidActionRequest) -> dict[str, Any]:
    _require_paid_confirmation([10], request.confirm_paid)
    _launch(run_id, ["--slot-motion", slot_id, "--force", "--confirm-paid"])
    return {"started": True, "slot_id": slot_id, "kind": "motion"}


@app.post("/api/runs/{run_id}/approve-all")
def approve_all(run_id: str, request: ApproveAllRequest) -> dict[str, Any]:
    _require_idle(run_id)
    paths = run_paths(run_id)
    try:
        meta = load_run(run_id)
        plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
        if request.level in {"editorial", "plan"}:
            for slot in plan.get("slots", []):
                if slot.get("status") == "suggested":
                    slot_action(run_id, slot["slot_id"], "approve_plan")
            meta = load_run(run_id)
            meta["approvals"]["editorial"] = True
            save_run(meta)
        elif request.level == "still":
            for slot in plan.get("slots", []):
                if slot.get("status") == "still_review":
                    slot_action(run_id, slot["slot_id"], "approve_still")
            meta = load_run(run_id)
            meta["approvals"]["stills"] = True
            save_run(meta)
        elif request.level == "motion":
            for slot in plan.get("slots", []):
                if slot.get("status") == "motion_review":
                    slot_action(run_id, slot["slot_id"], "approve_motion")
        elif request.level == "complete_preview":
            meta["approvals"]["complete_preview"] = True
            save_run(meta)
        elif request.level == "final":
            meta["approvals"]["final"] = True
            save_run(meta)
        else:
            raise ValueError("Unknown approval level")
        return public_run_detail(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/runs/{run_id}/sound/{cue_id}")
def edit_sound_cue(run_id: str, cue_id: str, request: SoundCueUpdateRequest) -> dict[str, Any]:
    _require_idle(run_id)
    try:
        paths = run_paths(run_id)
        plan = read_json(paths.sound / "sound-plan.json", {"cues": []})
        cue = next((item for item in plan.get("cues", []) if item.get("cue_id") == cue_id), None)
        if not cue:
            raise KeyError(cue_id)
        for key in ("time", "gain_db", "enabled", "reason"):
            if key in request.updates:
                cue[key] = request.updates[key]
        cue["time"] = max(0.0, float(cue.get("time", 0)))
        cue["gain_db"] = max(-40.0, min(float(cue.get("gain_db", -18)), -3.0))
        cue["enabled"] = bool(cue.get("enabled", True))
        write_json(paths.sound / "sound-plan.json", plan)
        return cue
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, Any]:
    _require_idle(run_id)
    paths = run_paths(run_id)
    if not paths.root.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    shutil.rmtree(paths.root)
    return {"deleted": True}


def _validate_create_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Settings must be a JSON object")
    media = str(payload.get("media_provider") or "grok_cli")
    asr = str(payload.get("asr_provider") or "elevenlabs")
    matte = str(payload.get("matting_provider") or "mediapipe")
    aspect = str(payload.get("aspect_ratio") or "9:16")
    candidates = int(payload.get("image_candidates_per_slot") or 2)
    if media != "grok_cli":
        raise ValueError("Unsupported media provider")
    if asr != "elevenlabs":
        raise ValueError("Unsupported transcription provider; ElevenLabs Scribe v2 is required")
    if matte not in {"mediapipe", "none"}:
        raise ValueError("Unsupported matting provider")
    if aspect not in {"9:16", "16:9"}:
        raise ValueError("Unsupported aspect ratio")
    if candidates not in {1, 2, 3}:
        raise ValueError("Still candidates per scene must be 1, 2, or 3")
    expected = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    width = int(payload.get("width") or expected[0])
    height = int(payload.get("height") or expected[1])
    if (width, height) != expected:
        raise ValueError(f"{aspect} output must use {expected[0]}x{expected[1]}")
    payload.update({
        "asr_provider": asr,
        "media_provider": media,
        "matting_provider": matte,
        "aspect_ratio": aspect,
        "image_candidates_per_slot": candidates,
        "width": width,
        "height": height,
        "fps": 30,
    })
    payload.update(_validate_v2_controls(payload))
    return payload


def _validate_v2_controls(payload: dict[str, Any]) -> dict[str, Any]:
    profile = str(payload.get("editing_profile") or "modern_tech_explainer")
    intensity = str(payload.get("editing_intensity") or "medium")
    visual_style = str(payload.get("visual_generation_style") or "natural_colorful")
    foreground = str(payload.get("foreground_treatment") or "auto")
    sfx = str(payload.get("sfx_density") or "medium")
    captions = str(payload.get("captions_mode") or "off")
    cleanup = str(payload.get("dialogue_cleanup_mode") or "balanced")
    allowed_profiles = {"clean_medical", "natural_colorful", "modern_tech_explainer", "high_energy_reel", "minimal_professional", "custom"}
    if profile not in allowed_profiles:
        raise ValueError("Unsupported editing profile")
    if intensity not in {"low", "medium", "high"}:
        raise ValueError("Unsupported editing intensity")
    if visual_style not in {"natural", "natural_colorful", "cinematic", "medical_illustration", "mixed"}:
        raise ValueError("Unsupported visual generation style")
    if foreground not in {"auto", "never", "only_approved", "prefer_when_clean"}:
        raise ValueError("Unsupported foreground treatment")
    if sfx not in {"off", "low", "medium", "high"}:
        raise ValueError("Unsupported sound-effects density")
    if captions not in {"off", "auto", "all"}:
        raise ValueError("Unsupported caption mode")
    if cleanup not in {"off", "conservative", "balanced", "tight"}:
        raise ValueError("Unsupported dialogue cleanup mode")
    layouts = payload.get("preferred_layouts") or [
        "talking_head", "broll_top_speaker_bottom", "speaker_top_broll_bottom",
        "speaker_left_broll_right", "broll_left_speaker_right", "picture_in_picture",
        "floating_visual", "full_broll", "layered_foreground",
    ]
    valid_layouts = {
        "talking_head", "broll_top_speaker_bottom", "speaker_top_broll_bottom",
        "speaker_left_broll_right", "broll_left_speaker_right", "picture_in_picture",
        "floating_visual", "full_broll", "layered_foreground",
    }
    layouts = [str(item) for item in layouts if str(item) in valid_layouts]
    if not layouts:
        raise ValueError("Select at least one preferred layout")
    return {
        "editing_profile": profile,
        "editing_intensity": intensity,
        "visual_generation_style": visual_style,
        "foreground_treatment": foreground,
        "sfx_density": sfx,
        "captions_mode": captions,
        "dialogue_cleanup_mode": cleanup,
        "preferred_layouts": layouts,
    }


def _require_paid_confirmation(stage_numbers: list[int], confirmed: bool) -> None:
    paid = [number for number in stage_numbers if number in STAGE_BY_NUMBER and STAGE_BY_NUMBER[number].paid]
    if paid and not confirmed:
        labels = ", ".join(f"{number} ({STAGE_BY_NUMBER[number].label})" for number in paid)
        raise HTTPException(
            status_code=400,
            detail=f"Explicit provider-usage confirmation is required for paid/usage stage(s): {labels}",
        )


def _launch(run_id: str, worker_args: list[str]) -> None:
    _require_idle(run_id)
    paths = run_paths(run_id)
    if not paths.meta.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    log_handle = paths.log.open("a", encoding="utf-8")
    command = [PYTHON, "-m", "clinic_broll.worker", "--run-id", run_id, *worker_args]
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[2],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with _process_lock:
        _processes[run_id] = process
    meta = load_run(run_id)
    meta["active_process"] = {"pid": process.pid, "command": command}
    save_run(meta)

    def monitor() -> None:
        returncode = process.wait()
        log_handle.close()
        try:
            current = load_run(run_id)
            current["active_process"] = None
            if returncode != 0:
                for stage in current.get("stages", []):
                    if stage.get("status") == "running":
                        stage["status"] = "failed"
                        stage["error"] = f"Worker exited with code {returncode}"
            save_run(current)
        except Exception:
            pass

    threading.Thread(target=monitor, daemon=True, name=f"cbs-{run_id}").start()


def _require_idle(run_id: str) -> None:
    with _process_lock:
        process = _processes.get(run_id)
        if process and process.poll() is None:
            raise HTTPException(status_code=409, detail="Stop the active process before changing this run")


def _process_payload(run_id: str) -> dict[str, Any] | None:
    with _process_lock:
        process = _processes.get(run_id)
        if not process:
            return None
        return {"pid": process.pid, "running": process.poll() is None, "returncode": process.poll()}


def _doctor_tools() -> dict[str, Any]:
    from shutil import which

    sound_root = Path(os.getenv("SFX_LIBRARY_ROOT") or (Path(__file__).resolve().parents[3] / "ai_sound_effects_library"))
    return {
        "ffmpeg": bool(which(FFMPEG) or Path(FFMPEG).exists()),
        "ffprobe": bool(which(FFPROBE) or Path(FFPROBE).exists()),
        "hyperframes": bool(
            which(HYPERFRAMES)
            or Path(HYPERFRAMES).exists()
            or (Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "hyperframes").exists()
        ),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "sound_library": (sound_root / "manifest.json").exists(),
        "sound_library_root": str(sound_root),
    }
