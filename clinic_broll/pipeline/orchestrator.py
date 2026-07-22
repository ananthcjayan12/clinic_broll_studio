from __future__ import annotations

import traceback
from typing import Any, Callable

from ..core.paths import run_paths
from ..core.state import STAGE_BY_NUMBER, append_log, load_run, mark_stage, next_incomplete, stage_record
from . import (
    analyze,
    choreography,
    dialogue,
    editorial_complete,
    final_render,
    ingest,
    motion,
    preparation,
    preview,
    qa,
    sound,
    stills,
    transcribe,
)

_STAGE_RUNNERS: dict[int, Callable[[str], dict[str, Any]]] = {
    1: ingest.run,
    2: transcribe.run,
    3: dialogue.run_analysis,
    4: dialogue.run_clean_master,
    5: analyze.run,
    6: editorial_complete.run,
    7: preparation.run,
    8: stills.run,
    9: preview.run_still,
    10: motion.run,
    11: choreography.run,
    12: sound.run,
    13: preview.run_complete,
    14: final_render.run,
    15: qa.run,
}


def run_stage(run_id: str, stage_number: int, *, force: bool = False, confirm_paid: bool = False) -> dict[str, Any]:
    if stage_number not in STAGE_BY_NUMBER:
        raise ValueError(f"Unknown stage: {stage_number}")
    stage = STAGE_BY_NUMBER[stage_number]
    paths = run_paths(run_id)
    meta = load_run(run_id)
    record = stage_record(meta, stage_number)
    if record["status"] == "complete" and not force:
        append_log(paths, f"Stage {stage_number} already complete; using cache")
        return meta
    if stage.paid and not confirm_paid:
        raise RuntimeError(
            f"Stage {stage_number} ({stage.label}) may use an API or subscription quota; "
            "rerun with explicit paid/provider-usage confirmation"
        )
    _check_gate(meta, stage_number)
    append_log(paths, f"START stage {stage.number}: {stage.label}")
    mark_stage(run_id, stage_number, "running")
    try:
        result = _STAGE_RUNNERS[stage_number](run_id)
        meta = mark_stage(
            run_id,
            stage_number,
            "complete",
            artifacts=result.get("artifacts", []),
            summary=result.get("summary"),
        )
        append_log(paths, f"DONE stage {stage.number}: {stage.label}")
        return meta
    except Exception as exc:
        mark_stage(run_id, stage_number, "failed", error=str(exc))
        append_log(paths, f"FAILED stage {stage.number}: {exc}")
        append_log(paths, traceback.format_exc())
        raise


def run_through(run_id: str, target_stage: int, *, force: bool = False, confirm_paid: bool = False) -> dict[str, Any]:
    if target_stage not in STAGE_BY_NUMBER:
        raise ValueError("Unknown target stage")
    while True:
        meta = load_run(run_id)
        next_stage = next_incomplete(meta)
        if next_stage is None or next_stage > target_stage:
            return meta
        run_stage(run_id, next_stage, force=force, confirm_paid=confirm_paid)
        if STAGE_BY_NUMBER[next_stage].human_gate:
            append_log(run_paths(run_id), f"Paused at human review gate after stage {next_stage}")
            return load_run(run_id)


def _check_gate(meta: dict[str, Any], stage_number: int) -> None:
    from ..core.io import read_json

    paths = run_paths(meta["run_id"])
    if stage_number > 1:
        previous = stage_record(meta, stage_number - 1)
        if previous["status"] not in {"complete", "skipped"}:
            raise RuntimeError(f"Stage {stage_number - 1} must complete first")

    if stage_number == 4:
        dialogue_plan = read_json(paths.dialogue / "edit-plan.json", {"edits": []})
        unresolved = [
            edit.get("edit_id") for edit in dialogue_plan.get("edits", [])
            if edit.get("status") not in {"approved", "kept"}
        ]
        if unresolved:
            raise RuntimeError(
                "Resolve every dialogue cleanup proposal first: "
                + ", ".join(str(item) for item in unresolved)
            )

    if stage_number == 7:
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        unresolved = [
            slot.get("slot_id") for slot in plan_payload.get("slots", [])
            if slot.get("status") == "suggested"
        ]
        # A scene decision is stored on the slot itself.  The older aggregate
        # ``approvals.editorial`` flag is not set by the simplified Studio,
        # including when every scene is intentionally kept as talking head.
        # Requiring it here left a valid all-talking-head plan with no visible
        # approval control and unable to advance.
        if unresolved:
            raise RuntimeError(
                "Approve, reject, or keep talking head for every Editorial Director scene before preparation"
            )

    if stage_number == 8:
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        visual_slots = [slot for slot in plan_payload.get("slots", []) if slot.get("status") == "plan_approved"]
        if not visual_slots:
            raise RuntimeError(
                "No approved generated-visual scenes exist. Skip stages 8–10 when this reel intentionally remains talking-head only."
            )

    if stage_number == 10:
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        visual_slots = [
            slot for slot in plan_payload.get("slots", [])
            if slot.get("status") not in {"rejected", "talking_head"}
        ]
        if visual_slots and not any(slot.get("status") == "still_approved" for slot in visual_slots):
            raise RuntimeError("Approve at least one still before generating motion, or skip motion generation")

    if stage_number == 13:
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        unresolved = [
            slot.get("slot_id") for slot in plan_payload.get("slots", [])
            if slot.get("status") in {"suggested", "still_review", "motion_review"}
        ]
        if unresolved:
            raise RuntimeError(
                "Resolve every pending scene review before the complete edit preview: "
                + ", ".join(str(item) for item in unresolved)
            )

    if stage_number == 14 and not meta.get("approvals", {}).get("complete_preview"):
        raise RuntimeError("Approve the complete edit preview before final rendering")
