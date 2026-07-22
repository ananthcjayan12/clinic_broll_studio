from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..core.io import read_json
from ..core.paths import run_paths
from ..core.state import load_run, mark_stage, save_run, stage_record
from ..pipeline.dialogue import CLEANUP_MODES, approve_safe, resolve_edit, update_edit

_REGISTERED = False


class DialogueEditUpdate(BaseModel):
    updates: dict[str, Any]


class DialogueEditAction(BaseModel):
    action: str


class DialogueSettingsUpdate(BaseModel):
    dialogue_cleanup_mode: str


class SkipStageRequest(BaseModel):
    step: int


def register_dialogue_routes(app: FastAPI) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    router = APIRouter(prefix="/api/dialogue", tags=["dialogue"])

    @router.get("/runs/{run_id}")
    def dialogue_detail(run_id: str) -> dict[str, Any]:
        try:
            paths = run_paths(run_id)
            meta = load_run(run_id)
            plan = read_json(paths.dialogue / "edit-plan.json", {"edits": []}) or {"edits": []}
            mapping = read_json(paths.dialogue / "source-to-clean-map.json", {}) or {}
            continuity = read_json(paths.dialogue / "continuity-plan.json", {}) or {}
            return {
                "settings": {
                    "dialogue_cleanup_mode": meta["settings"].get("dialogue_cleanup_mode", "balanced"),
                    "dialogue_crossfade_ms": meta["settings"].get("dialogue_crossfade_ms", 25),
                },
                "plan": plan,
                "mapping": mapping,
                "continuity": continuity,
                "artifacts": {
                    "source_preview": _url_if_exists(run_id, paths.dialogue / "source-proxy.mp4"),
                    "clean_preview": _url_if_exists(run_id, paths.source / "proxy.mp4") if mapping else None,
                    "timeline_map": _url_if_exists(run_id, paths.dialogue / "source-to-clean-map.json"),
                    "continuity_plan": _url_if_exists(run_id, paths.dialogue / "continuity-plan.json"),
                },
            }
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/runs/{run_id}/settings")
    def update_settings(run_id: str, request: DialogueSettingsUpdate) -> dict[str, Any]:
        _require_idle(run_id)
        mode = request.dialogue_cleanup_mode.lower()
        if mode not in CLEANUP_MODES:
            raise HTTPException(status_code=400, detail="Unsupported dialogue cleanup mode")
        meta = load_run(run_id)
        if stage_record(meta, 3)["status"] not in {"pending", "failed"}:
            raise HTTPException(status_code=400, detail="Rewind from stage 3 before changing dialogue cleanup mode")
        meta["settings"]["dialogue_cleanup_mode"] = mode
        save_run(meta)
        return {"dialogue_cleanup_mode": mode}

    @router.put("/runs/{run_id}/edits/{edit_id}")
    def edit_dialogue(run_id: str, edit_id: str, request: DialogueEditUpdate) -> dict[str, Any]:
        _require_idle(run_id)
        try:
            return update_edit(run_id, edit_id, request.updates)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/edits/{edit_id}/action")
    def act_dialogue(run_id: str, edit_id: str, request: DialogueEditAction) -> dict[str, Any]:
        _require_idle(run_id)
        try:
            return resolve_edit(run_id, edit_id, request.action)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/approve-safe")
    def approve_safe_edits(run_id: str) -> dict[str, Any]:
        _require_idle(run_id)
        try:
            return approve_safe(run_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/skip")
    def skip_optional_stage(run_id: str, request: SkipStageRequest) -> dict[str, Any]:
        _require_idle(run_id)
        if request.step not in {8, 9, 10, 12}:
            raise HTTPException(
                status_code=400,
                detail="Only visual generation, visual preview, motion generation, and sound design may be skipped",
            )
        meta = load_run(run_id)
        if request.step > 1 and stage_record(meta, request.step - 1)["status"] not in {"complete", "skipped"}:
            raise HTTPException(status_code=400, detail="Complete or skip the previous stage first")
        mark_stage(run_id, request.step, "skipped", summary={"reason": "Skipped by operator"})
        return {"skipped": request.step}

    app.include_router(router)
    _REGISTERED = True


def _require_idle(run_id: str) -> None:
    meta = load_run(run_id)
    if meta.get("active_process"):
        raise HTTPException(status_code=409, detail="Stop the active process before changing dialogue edits")


def _url_if_exists(run_id: str, path) -> str | None:
    if not path.exists():
        return None
    return f"/runs/{run_id}/{path.relative_to(run_paths(run_id).root).as_posix()}"
