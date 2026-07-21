from __future__ import annotations

import traceback
from typing import Any, Callable

from ..core.paths import run_paths
from ..core.state import STAGE_BY_NUMBER, append_log, load_run, mark_stage, next_incomplete, stage_record
from . import analyze, final_render, ingest, matte, motion, plan, preview, qa, stills, transcribe


_STAGE_RUNNERS: dict[int, Callable[[str], dict[str, Any]]] = {
    1: ingest.run,
    2: transcribe.run,
    3: analyze.run,
    4: plan.run,
    5: matte.run,
    6: stills.run,
    7: preview.run_still,
    8: motion.run,
    9: preview.run_motion,
    10: final_render.run,
    11: qa.run,
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
            f"Stage {stage_number} ({stage.label}) may use an API or subscription quota; rerun with explicit paid/provider-usage confirmation"
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
        # Human review is an explicit production boundary. A second user action
        # resumes the pipeline after slot approvals have been recorded.
        if STAGE_BY_NUMBER[next_stage].human_gate:
            append_log(run_paths(run_id), f"Paused at human review gate after stage {next_stage}")
            return load_run(run_id)


def _check_gate(meta: dict[str, Any], stage_number: int) -> None:
    paths = run_paths(meta["run_id"])
    if stage_number > 1:
        previous = stage_record(meta, stage_number - 1)
        if previous["status"] not in {"complete", "skipped"}:
            raise RuntimeError(f"Stage {stage_number - 1} must complete first")
    if stage_number == 6:
        from ..core.io import read_json
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        if not any(slot.get("status") == "plan_approved" for slot in plan_payload.get("slots", [])):
            raise RuntimeError("Approve at least one B-roll plan slot before generating stills")
    if stage_number == 8:
        from ..core.io import read_json
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        if not any(slot.get("status") == "still_approved" for slot in plan_payload.get("slots", [])):
            raise RuntimeError("Approve at least one still before generating motion")
    if stage_number == 10:
        from ..core.io import read_json
        plan_payload = read_json(paths.plan / "broll_plan.json", {"slots": []})
        unresolved = [
            slot.get("slot_id") for slot in plan_payload.get("slots", [])
            if slot.get("status") in {"suggested", "still_review", "motion_review"}
        ]
        if unresolved:
            raise RuntimeError(
                "Resolve every pending slot review before final rendering: " + ", ".join(str(item) for item in unresolved)
            )
