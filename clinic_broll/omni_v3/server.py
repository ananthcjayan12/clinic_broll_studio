from __future__ import annotations

import shutil
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.config import RUNS_ROOT
from ..core.io import read_json
from ..core.paths import require_run_id, run_paths
from ..core.state import create_run, list_runs, load_run, save_run, stage_record
from ..pipeline.orchestrator import run_through
from ..studio.dialogue_api import register_dialogue_routes
from . import director, jobs

STATIC_ROOT = Path(__file__).resolve().parent / "static"
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Clinic Omni Reel Studio", version="3.0.0")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.mount("/runs", StaticFiles(directory=RUNS_ROOT), name="runs")
register_dialogue_routes(app)

_processes: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


class JobAction(BaseModel):
    action: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return list_runs()


@app.post("/api/runs")
async def create(
    run_id: str = Form(...),
    video: UploadFile = File(...),
    editing_intensity: str = Form("medium"),
    max_omni_jobs: int = Form(5),
    max_omni_credits: int = Form(280),
) -> dict[str, Any]:
    created_root: Path | None = None
    try:
        run_id = require_run_id(run_id)
        suffix = Path(video.filename or ".mp4").suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
            raise ValueError("Upload an MP4, MOV, M4V or WebM video")
        settings = {
            "production_mode": "omni_presenter_reel",
            "editing_profile": "modern_tech_explainer",
            "editing_intensity": editing_intensity if editing_intensity in {"low", "medium", "high"} else "medium",
            "captions_mode": "all",
            "sfx_density": "low",
            "image_candidates_per_slot": 1,
            "max_omni_jobs": max(1, min(int(max_omni_jobs), 8)),
            "max_omni_credits": max(40, min(int(max_omni_credits), 800)),
            "omni_segment_max_seconds": 8,
            "omni_handle_seconds": 0.4,
        }
        create_run(run_id, original_filename=video.filename or "upload.mp4", settings=settings)
        paths = run_paths(run_id)
        created_root = paths.root
        destination = paths.source / f"upload{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            while chunk := await video.read(1024 * 1024):
                handle.write(chunk)
        if destination.stat().st_size == 0:
            raise ValueError("Uploaded video is empty")
        return _payload(run_id)
    except Exception as exc:
        if created_root is not None:
            shutil.rmtree(created_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def detail(run_id: str) -> dict[str, Any]:
    try:
        return _payload(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/analyse")
def analyse(run_id: str) -> dict[str, Any]:
    _launch(run_id, "Transcribe and analyse dialogue", lambda: run_through(run_id, 3, confirm_paid=True))
    return {"started": True}


@app.post("/api/runs/{run_id}/clean")
def clean(run_id: str) -> dict[str, Any]:
    _launch(run_id, "Build clean master and analyse video", lambda: run_through(run_id, 5, confirm_paid=True))
    return {"started": True}


@app.post("/api/runs/{run_id}/direct")
def direct(run_id: str) -> dict[str, Any]:
    _launch(run_id, "Create Omni reel plan", lambda: director.run(run_id))
    return {"started": True}


@app.post("/api/runs/{run_id}/prepare-jobs")
def prepare(run_id: str) -> dict[str, Any]:
    _launch(run_id, "Prepare Google Flow job pack", lambda: jobs.prepare_jobs(run_id))
    return {"started": True}


@app.get("/api/runs/{run_id}/omni")
def omni_manifest(run_id: str) -> dict[str, Any]:
    return _manifest_payload(run_id)


@app.post("/api/runs/{run_id}/omni/{job_id}/result")
async def upload_result(run_id: str, job_id: str, video: UploadFile = File(...)) -> dict[str, Any]:
    try:
        result = await jobs.import_result(run_id, job_id, video)
        return {"job": result, "manifest": _manifest_payload(run_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/omni/{job_id}/action")
def job_action(run_id: str, job_id: str, request: JobAction) -> dict[str, Any]:
    try:
        result = jobs.approve_job(run_id, job_id, request.action)
        return {"job": result, "manifest": _manifest_payload(run_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/finalise-omni")
def finalise(run_id: str) -> dict[str, Any]:
    try:
        return jobs.finalise_jobs(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/preview")
def preview(run_id: str) -> dict[str, Any]:
    _launch(run_id, "Build final reel preview", lambda: run_through(run_id, 13, confirm_paid=True))
    return {"started": True}


@app.post("/api/runs/{run_id}/approve-render")
def approve_render(run_id: str) -> dict[str, Any]:
    meta = load_run(run_id)
    if stage_record(meta, 13)["status"] != "complete":
        raise HTTPException(status_code=400, detail="Build the complete preview first")
    meta.setdefault("approvals", {})["complete_preview"] = True
    save_run(meta)
    _launch(run_id, "Render and quality-check final reel", lambda: run_through(run_id, 15, confirm_paid=True))
    return {"started": True}


def _launch(run_id: str, label: str, operation: Callable[[], Any]) -> None:
    load_run(run_id)
    with _lock:
        active = _processes.get(run_id)
        if active and active.get("running"):
            raise HTTPException(status_code=409, detail="This production already has an active task")
        _processes[run_id] = {"running": True, "label": label, "error": None}
        meta = load_run(run_id)
        meta["active_process"] = {"label": label}
        save_run(meta)

    def worker() -> None:
        try:
            operation()
        except Exception as exc:
            with _lock:
                _processes[run_id]["error"] = str(exc)
                _processes[run_id]["traceback"] = traceback.format_exc()[-6000:]
        finally:
            with _lock:
                _processes[run_id]["running"] = False
            try:
                meta = load_run(run_id)
                meta["active_process"] = None
                save_run(meta)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True, name=f"omni-v3-{run_id}").start()


def _payload(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    process = _processes.get(run_id, {"running": False, "label": None, "error": None})
    manifest = jobs.load_manifest(run_id)
    plan = read_json(paths.root / "omni" / "plan.json", {}) or {}
    dialogue = read_json(paths.dialogue / "edit-plan.json", {}) or {}
    return {
        **meta,
        "process": process,
        "omni_plan": plan,
        "omni_manifest": _manifest_payload(run_id, manifest),
        "dialogue_unresolved": sum(1 for item in dialogue.get("edits") or [] if item.get("status") not in {"approved", "kept"}),
        "artifacts": {
            "source": _url(run_id, paths.source / "proxy.mp4"),
            "clean": _url(run_id, paths.source / "proxy.mp4") if stage_record(meta, 4)["status"] == "complete" else None,
            "preview": _url(run_id, paths.previews / "complete-preview.mp4"),
            "final": _url(run_id, paths.renders / "final.mp4"),
            "plan": _url(run_id, paths.root / "omni" / "plan.json"),
            "manifest": _url(run_id, paths.root / "omni" / "manifest.json"),
        },
    }


def _manifest_payload(run_id: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(manifest or jobs.load_manifest(run_id))
    enriched = []
    for item in payload.get("jobs") or []:
        item = dict(item)
        for key in ("input_path", "prompt_path", "thumbnail_path", "raw_result_path", "normalised_result_path"):
            item[key.replace("_path", "_url")] = _url(run_id, run_paths(run_id).root / item[key]) if item.get(key) else None
        enriched.append(item)
    payload["jobs"] = enriched
    return payload


def _url(run_id: str, path: Path) -> str | None:
    if not path.exists():
        return None
    return f"/runs/{run_id}/{path.relative_to(run_paths(run_id).root).as_posix()}"


def main() -> None:
    uvicorn.run("clinic_broll.omni_v3.server:app", host="127.0.0.1", port=8766, reload=False)


if __name__ == "__main__":
    main()
