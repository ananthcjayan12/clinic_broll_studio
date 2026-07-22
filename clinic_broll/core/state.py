from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from .io import now_iso, read_json, safe_move_to_history, write_json
from .models import DEFAULT_MODEL_MAP, validate_model_map
from .paths import RunPaths, run_paths
from .usage import usage_summary


@dataclass(frozen=True)
class Stage:
    number: int
    key: str
    label: str
    paid: bool = False
    human_gate: bool = False


STAGES: tuple[Stage, ...] = (
    Stage(1, "ingest", "Inputs & normalize"),
    Stage(2, "transcribe", "Malayalam transcription", paid=True),
    Stage(3, "dialogue_analysis", "Dialogue cleanup analysis", paid=True, human_gate=True),
    Stage(4, "clean_master", "Clean master & continuity"),
    Stage(5, "analyze", "Local video analysis"),
    Stage(6, "plan", "B-roll planning", paid=True, human_gate=True),
    Stage(7, "matte", "Foreground subject matte"),
    Stage(8, "stills", "Generate still candidates", paid=True),
    Stage(9, "still_preview", "Still preview", human_gate=True),
    Stage(10, "motion", "Generate approved motion", paid=True),
    Stage(11, "motion_preview", "Motion preview", human_gate=True),
    Stage(12, "render", "Final render"),
    Stage(13, "qa", "Final QA", paid=True),
)
STAGE_BY_NUMBER = {item.number: item for item in STAGES}
STAGE_BY_KEY = {item.key: item for item in STAGES}

STAGE_OUTPUTS: dict[int, tuple[str, ...]] = {
    1: (
        "source/master.mp4", "source/proxy.mp4", "source/speech.wav", "source/metadata.json",
        "dialogue/source-master.mp4", "dialogue/source-proxy.mp4",
        "dialogue/source-speech.wav", "dialogue/source-metadata.json",
    ),
    2: ("transcript", "dialogue/source-transcript.json", "dialogue/source-captions.srt"),
    3: ("dialogue/edit-plan.json", "prompts/dialogue", "responses/dialogue"),
    4: (
        "dialogue/source-to-clean-map.json",
        "dialogue/continuity-plan.json",
        "source/master.mp4",
        "source/proxy.mp4",
        "source/speech.wav",
        "source/metadata.json",
        "transcript/transcript.json",
        "transcript/captions.srt",
    ),
    5: ("analysis",),
    6: ("plan",),
    7: ("matte",),
    8: ("assets/stills", "prompts/stills", "responses/stills"),
    9: ("compositions/still", "previews/still-preview.mp4"),
    10: ("assets/motion", "prompts/motion", "responses/motion"),
    11: ("compositions/motion", "previews/motion-preview.mp4"),
    12: ("compositions/final", "renders/final.mp4"),
    13: ("qa",),
}


def _empty_stage(stage: Stage) -> dict[str, Any]:
    return {
        "number": stage.number,
        "key": stage.key,
        "label": stage.label,
        "paid": stage.paid,
        "human_gate": stage.human_gate,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "error": None,
        "artifacts": [],
    }


def create_run(
    run_id: str,
    *,
    original_filename: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = run_paths(run_id)
    if paths.root.exists():
        raise FileExistsError(f"Run already exists: {run_id}")
    paths.root.mkdir(parents=True)
    resolved = {
        "width": DEFAULT_WIDTH,
        "height": DEFAULT_HEIGHT,
        "fps": DEFAULT_FPS,
        "aspect_ratio": "9:16",
        "asr_provider": "elevenlabs",
        "matting_provider": "mediapipe",
        "media_provider": "grok_cli",
        "image_candidates_per_slot": 1,
        "captions_mode": "off",
        "dialogue_cleanup_mode": "balanced",
        "dialogue_crossfade_ms": 25,
        "matte_feather_px": 4,
        "matte_temporal_blend": 0.12,
        "matte_decontamination_strength": 0.72,
        "render_quality": "high",
        "task_models": DEFAULT_MODEL_MAP,
    }
    if settings:
        resolved.update(settings)
    resolved["task_models"] = validate_model_map(resolved.get("task_models"))
    meta = {
        "version": "1.2",
        "run_id": run_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source_filename": original_filename,
        "settings": resolved,
        "stages": [_empty_stage(stage) for stage in STAGES],
        "active_process": None,
        "approvals": {"dialogue": False, "plan": False, "stills": False, "motion": False, "final": False},
        "notes": [],
    }
    write_json(paths.meta, meta)
    append_log(paths, "Run created")
    return meta


def load_run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = read_json(paths.meta)
    if not meta:
        raise FileNotFoundError(f"Unknown run: {run_id}")
    if _migrate_meta(meta):
        write_json(paths.meta, meta)
        append_log(paths, "Run metadata migrated to dialogue-cleanup pipeline v1.2")
    return meta


def _migrate_meta(meta: dict[str, Any]) -> bool:
    changed = False
    settings = meta.setdefault("settings", {})
    defaults = {"dialogue_cleanup_mode": "balanced", "dialogue_crossfade_ms": 25}
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
            changed = True
    validated = validate_model_map(settings.get("task_models"))
    if validated != settings.get("task_models"):
        settings["task_models"] = validated
        changed = True

    current_keys = [str(item.get("key")) for item in meta.get("stages", [])]
    desired_keys = [stage.key for stage in STAGES]
    if current_keys != desired_keys:
        existing = {str(item.get("key")): item for item in meta.get("stages", [])}
        rebuilt = []
        has_dialogue = "dialogue_analysis" in existing
        for stage in STAGES:
            if stage.key in existing and (has_dialogue or stage.number <= 2):
                old = existing[stage.key]
                record = _empty_stage(stage)
                record.update({key: old.get(key) for key in ("status", "started_at", "completed_at", "error", "artifacts")})
                record.update({"number": stage.number, "label": stage.label, "paid": stage.paid, "human_gate": stage.human_gate})
            else:
                record = _empty_stage(stage)
            rebuilt.append(record)
        meta["stages"] = rebuilt
        changed = True
    approvals = meta.setdefault("approvals", {})
    if "dialogue" not in approvals:
        approvals["dialogue"] = False
        changed = True
    if meta.get("version") != "1.2":
        meta["version"] = "1.2"
        changed = True
    return changed


def save_run(meta: dict[str, Any]) -> None:
    meta["updated_at"] = now_iso()
    write_json(run_paths(str(meta["run_id"])).meta, meta)


def append_log(paths: RunPaths, message: str) -> None:
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M:%S")
    with paths.log.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message.rstrip()}\n")


def stage_record(meta: dict[str, Any], stage_number: int) -> dict[str, Any]:
    for item in meta["stages"]:
        if int(item["number"]) == int(stage_number):
            return item
    raise KeyError(stage_number)


def mark_stage(run_id: str, stage_number: int, status: str, **updates: Any) -> dict[str, Any]:
    meta = load_run(run_id)
    record = stage_record(meta, stage_number)
    record["status"] = status
    if status == "running":
        record["started_at"] = now_iso()
        record["completed_at"] = None
        record["error"] = None
    elif status in {"complete", "failed", "skipped", "blocked"}:
        record["completed_at"] = now_iso()
    record.update(updates)
    save_run(meta)
    return meta


def next_incomplete(meta: dict[str, Any]) -> int | None:
    for item in meta["stages"]:
        if item["status"] not in {"complete", "skipped"}:
            return int(item["number"])
    return None


def rewind_run(run_id: str, from_stage: int) -> dict[str, Any]:
    if from_stage not in STAGE_BY_NUMBER:
        raise ValueError("Unknown stage")
    paths = run_paths(run_id)
    meta = load_run(run_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    history_root = paths.history / f"rewind-from-{from_stage}-{stamp}"
    history_root.mkdir(parents=True, exist_ok=True)
    write_json(history_root / "studio_run.before.json", meta)
    plan_path = paths.plan / "broll_plan.json"
    plan = read_json(plan_path)
    if plan:
        write_json(history_root / "broll_plan.before.json", plan)

    for number in range(from_stage, max(STAGE_BY_NUMBER) + 1):
        for relative in STAGE_OUTPUTS.get(number, ()):
            safe_move_to_history(paths.root / relative, history_root / f"stage-{number:02d}")
        record = stage_record(meta, number)
        record.update({"status": "pending", "started_at": None, "completed_at": None, "error": None, "artifacts": []})

    if plan and from_stage > 6:
        for slot in plan.get("slots", []):
            if from_stage <= 8:
                slot.setdefault("versions", {})["stills"] = []
                slot.setdefault("versions", {})["motion"] = []
                slot["selected_still"] = None
                slot["selected_motion"] = None
                if slot.get("status") not in {"rejected", "talking_head", "suggested"}:
                    slot["status"] = "plan_approved"
                slot.setdefault("review", {})["still"] = None
                slot.setdefault("review", {})["motion"] = None
            elif from_stage <= 10:
                slot.setdefault("versions", {})["motion"] = []
                slot["selected_motion"] = None
                if slot.get("selected_still") and slot.get("status") not in {"rejected", "talking_head"}:
                    slot["status"] = "still_approved"
                slot.setdefault("review", {})["motion"] = None
        write_json(plan_path, plan)

    if from_stage <= 3:
        meta["approvals"]["dialogue"] = False
    if from_stage <= 6:
        meta["approvals"]["plan"] = False
    if from_stage <= 8:
        meta["approvals"]["stills"] = False
    if from_stage <= 10:
        meta["approvals"]["motion"] = False
    if from_stage <= 12:
        meta["approvals"]["final"] = False
    append_log(paths, f"Rewound from stage {from_stage}; previous artifacts saved in {history_root.name}")
    save_run(meta)
    return meta


def list_runs() -> list[dict[str, Any]]:
    from .config import RUNS_ROOT

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for meta_path in RUNS_ROOT.glob("*/studio_run.json"):
        try:
            meta = read_json(meta_path)
            if not meta:
                continue
            _migrate_meta(meta)
            current = next_incomplete(meta)
            results.append(
                {
                    "run_id": meta["run_id"],
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "source_filename": meta.get("source_filename"),
                    "next_stage": current,
                    "final_ready": (meta_path.parent / "renders" / "final.mp4").exists(),
                }
            )
        except Exception:
            continue
    return sorted(results, key=lambda item: item.get("updated_at") or "", reverse=True)


def public_run_detail(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []}) or {"slots": []}
    transcript = read_json(paths.transcript / "transcript.json", {}) or {}
    artifacts = {
        "source_proxy": _url_if_exists(run_id, paths.source / "proxy.mp4"),
        "still_preview": _url_if_exists(run_id, paths.previews / "still-preview.mp4"),
        "motion_preview": _url_if_exists(run_id, paths.previews / "motion-preview.mp4"),
        "final_video": _url_if_exists(run_id, paths.renders / "final.mp4"),
        "still_composition": _url_if_exists(run_id, paths.compositions / "still" / "index.html"),
        "motion_composition": _url_if_exists(run_id, paths.compositions / "motion" / "index.html"),
        "final_composition": _url_if_exists(run_id, paths.compositions / "final" / "index.html"),
        "contact_sheet": _url_if_exists(run_id, paths.analysis / "contact-sheet.jpg"),
    }
    return {**meta, "plan": plan, "transcript": transcript, "artifacts": artifacts, "usage": usage_summary(paths.root)}


def _url_if_exists(run_id: str, path) -> str | None:
    if not path.exists():
        return None
    relative = path.relative_to(run_paths(run_id).root).as_posix()
    return f"/runs/{run_id}/{relative}"
