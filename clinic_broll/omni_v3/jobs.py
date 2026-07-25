from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run, mark_stage, stage_record
from ..pipeline.common import ffprobe, run_ffmpeg

RESOLVED = {"approved", "fallback"}
RESULT_READY = {"review", "approved", "fallback"}


def load_manifest(run_id: str) -> dict[str, Any]:
    path = run_paths(run_id).root / "omni" / "manifest.json"
    return read_json(path, {"version": "3.0", "jobs": []}) or {"version": "3.0", "jobs": []}


def prepare_jobs(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.root / "omni" / "plan.json", {}) or {}
    source = paths.source / "master.mp4"
    if not source.exists() or not plan.get("jobs"):
        raise RuntimeError("Run the Omni Reel Director before preparing Flow jobs")

    source_meta = read_json(paths.source / "metadata.json", {}) or {}
    duration = float(source_meta.get("duration_seconds") or plan.get("duration_seconds") or 0)
    width = int(meta["settings"].get("width") or 1080)
    height = int(meta["settings"].get("height") or 1920)
    fps = int(meta["settings"].get("fps") or 30)
    configured_handle = max(0.0, min(float(meta["settings"].get("omni_handle_seconds") or 0.4), 1.0))
    omni_root = paths.root / "omni"
    jobs_root = omni_root / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    manifest_jobs: list[dict[str, Any]] = []
    for planned in plan["jobs"]:
        job_id = str(planned["job_id"])
        job_dir = jobs_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        target_start = float(planned["start"])
        target_end = float(planned["end"])
        input_start = max(0.0, target_start - configured_handle)
        input_end = min(duration, target_end + configured_handle)
        handle_left = target_start - input_start
        handle_right = input_end - target_end
        input_duration = input_end - input_start
        input_path = job_dir / "input.mp4"
        thumbnail = job_dir / "thumbnail.jpg"
        prompt_path = job_dir / "prompt.txt"
        job_path = job_dir / "job.json"

        if not input_path.exists():
            run_ffmpeg(
                [
                    "-ss", f"{input_start:.6f}", "-i", str(source), "-t", f"{input_duration:.6f}",
                    "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(input_path),
                ],
                paths=paths,
                label=f"Prepare {job_id} Flow input",
                duration=input_duration,
            )
        if not thumbnail.exists():
            run_ffmpeg(
                [
                    "-ss", f"{min(input_duration / 2, handle_left + max(0.1, (target_end-target_start)/2)):.3f}",
                    "-i", str(input_path), "-frames:v", "1", "-vf", "scale=360:-2", str(thumbnail),
                ],
                paths=paths,
                label=f"Create {job_id} thumbnail",
            )
        prompt_path.write_text(str(planned["prompt"]).strip() + "\n", encoding="utf-8")
        job = {
            **planned,
            "status": "ready",
            "attempts": 0,
            "input_start": round(input_start, 3),
            "input_end": round(input_end, 3),
            "input_duration": round(input_duration, 3),
            "handle_left": round(handle_left, 3),
            "handle_right": round(handle_right, 3),
            "input_path": input_path.relative_to(paths.root).as_posix(),
            "prompt_path": prompt_path.relative_to(paths.root).as_posix(),
            "thumbnail_path": thumbnail.relative_to(paths.root).as_posix(),
            "raw_result_path": None,
            "normalised_result_path": None,
            "qc": None,
            "decision": None,
        }
        write_json(job_path, job)
        manifest_jobs.append(job)

    manifest = {
        "version": "3.0",
        "production_mode": "omni_presenter_reel",
        "run_id": run_id,
        "created_at": time.time(),
        "model": "Gemini Omni Flash in Google Flow",
        "instructions": [
            "Open Flow and choose Gemini Omni Flash.",
            "Use one output per job.",
            "Upload input.mp4 and paste prompt.txt exactly.",
            "Download the completed video and upload it back to this job card.",
        ],
        "estimated_credits": sum(int(item.get("estimated_credits") or 40) for item in manifest_jobs),
        "jobs": manifest_jobs,
    }
    write_json(omni_root / "manifest.json", manifest)
    mark_stage(
        run_id,
        7,
        "complete",
        artifacts=["omni/manifest.json", "omni/jobs/"],
        summary={"jobs": len(manifest_jobs), "estimated_credits": manifest["estimated_credits"]},
    )
    append_log(paths, f"Omni handoff: prepared {len(manifest_jobs)} Flow jobs")
    return manifest


async def import_result(run_id: str, job_id: str, upload: UploadFile) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    manifest = load_manifest(run_id)
    job = _job(manifest, job_id)
    suffix = Path(upload.filename or ".mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise ValueError("Upload an MP4, MOV, M4V or WebM Omni result")
    job_dir = paths.root / "omni" / "jobs" / job_id
    raw = job_dir / f"provider-result{suffix}"
    with raw.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            handle.write(chunk)
    if raw.stat().st_size == 0:
        raise ValueError("Uploaded Omni result is empty")

    probe = ffprobe(raw)
    output_duration = float((probe.get("format") or {}).get("duration") or 0)
    if output_duration <= 0:
        raise RuntimeError("The uploaded Omni result has no measurable duration")
    target_duration = float(job["duration"])
    expected_input_duration = float(job["input_duration"])
    ratio = expected_input_duration / output_duration
    width = int(meta["settings"].get("width") or 1080)
    height = int(meta["settings"].get("height") or 1920)
    fps = int(meta["settings"].get("fps") or 30)
    handle_left = float(job.get("handle_left") or 0)
    normalised = job_dir / "normalised.mp4"
    filter_chain = (
        f"setpts={ratio:.9f}*PTS,"
        f"trim=start={handle_left:.6f}:duration={target_duration:.6f},"
        "setpts=PTS-STARTPTS,"
        f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    )
    run_ffmpeg(
        [
            "-i", str(raw), "-vf", filter_chain, "-an", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(normalised),
        ],
        paths=paths,
        label=f"Normalise {job_id} Omni result",
        duration=target_duration,
    )
    normalised_probe = ffprobe(normalised)
    final_duration = float((normalised_probe.get("format") or {}).get("duration") or 0)
    stream = next((item for item in normalised_probe.get("streams") or [] if item.get("codec_type") == "video"), {})
    drift = output_duration - expected_input_duration
    qc = {
        "source_result_duration": round(output_duration, 3),
        "expected_input_duration": round(expected_input_duration, 3),
        "normalised_duration": round(final_duration, 3),
        "duration_drift_before_retime": round(drift, 3),
        "retime_ratio": round(ratio, 5),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "requires_identity_review": True,
        "warning": "Review face identity, mouth movement, hands and dental anatomy before approval.",
        "technical_pass": abs(final_duration - target_duration) <= max(0.08, 2 / fps),
    }
    job.update({
        "status": "review",
        "attempts": int(job.get("attempts") or 0) + 1,
        "raw_result_path": raw.relative_to(paths.root).as_posix(),
        "normalised_result_path": normalised.relative_to(paths.root).as_posix(),
        "qc": qc,
        "decision": None,
    })
    _save_manifest(paths, manifest)
    _complete_handoff_stage_if_ready(run_id, manifest)
    append_log(paths, f"Omni handoff: {job_id} uploaded and normalised for review")
    return job


def approve_job(run_id: str, job_id: str, action: str) -> dict[str, Any]:
    if action not in {"approve", "fallback", "retry"}:
        raise ValueError("Action must be approve, fallback or retry")
    paths = run_paths(run_id)
    manifest = load_manifest(run_id)
    job = _job(manifest, job_id)
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []}) or {"slots": []}
    slot = next((item for item in plan.get("slots") or [] if item.get("job_id") == job_id), None)
    if slot is None:
        raise RuntimeError(f"No timeline slot is linked to {job_id}")

    if action == "approve":
        result = str(job.get("normalised_result_path") or "")
        if not result or not (paths.root / result).exists():
            raise RuntimeError("Upload an Omni result before approving it")
        job.update({"status": "approved", "decision": "approve"})
        slot["status"] = "motion_approved"
        slot["selected_motion"] = result
        slot["selected_omni"] = result
        slot.setdefault("versions", {}).setdefault("motion", []).append({
            "version": f"omni-{int(job.get('attempts') or 1):02d}",
            "path": result,
            "status": "approved",
            "provider": "flow_manual",
        })
        slot.setdefault("versions", {}).setdefault("omni", []).append({
            "version": f"v{int(job.get('attempts') or 1):02d}", "path": result, "status": "approved"
        })
        slot.setdefault("review", {})["motion"] = "approve_motion"
    elif action == "fallback":
        job.update({"status": "fallback", "decision": "fallback"})
        slot.update({
            "status": "talking_head", "selected_motion": None, "selected_omni": None,
            "visual_strategy": "none", "provider_usage": False, "composition_mode": "talking_head",
            "layout_variant": "talking_head", "layout_template": "full_frame", "subject_mode": "original",
        })
        slot.setdefault("review", {})["motion"] = "fallback"
    else:
        job.update({"status": "ready", "decision": "retry", "qc": None})
        slot.update({"status": "plan_approved", "selected_motion": None, "selected_omni": None})
        slot.setdefault("review", {})["motion"] = "retry"

    write_json(paths.plan / "broll_plan.json", plan)
    _save_manifest(paths, manifest)
    _complete_handoff_stage_if_ready(run_id, manifest)
    if all(item.get("status") in RESOLVED for item in manifest.get("jobs") or []):
        mark_stage(
            run_id,
            9,
            "complete",
            artifacts=["omni/manifest.json", "plan/broll_plan.json"],
            summary={"approved": sum(item["status"] == "approved" for item in manifest["jobs"]), "fallback": sum(item["status"] == "fallback" for item in manifest["jobs"])},
        )
    append_log(paths, f"Omni handoff: {job_id} → {action}")
    return job


def finalise_jobs(run_id: str) -> dict[str, Any]:
    manifest = load_manifest(run_id)
    unresolved = [item["job_id"] for item in manifest.get("jobs") or [] if item.get("status") not in RESOLVED]
    if unresolved:
        raise RuntimeError("Approve or fall back every Omni job first: " + ", ".join(unresolved))
    if stage_record(load_run(run_id), 8)["status"] != "complete":
        mark_stage(run_id, 8, "complete", artifacts=["omni/manifest.json"], summary={"results_resolved": len(manifest["jobs"])})
    if stage_record(load_run(run_id), 9)["status"] != "complete":
        mark_stage(run_id, 9, "complete", artifacts=["omni/manifest.json"], summary={"reviewed": len(manifest["jobs"])})
    summary = {
        "approved": sum(item["status"] == "approved" for item in manifest["jobs"]),
        "fallback": sum(item["status"] == "fallback" for item in manifest["jobs"]),
    }
    mark_stage(
        run_id,
        10,
        "complete",
        artifacts=["omni/manifest.json", "plan/broll_plan.json", "omni/jobs/"],
        summary=summary,
    )
    append_log(run_paths(run_id), "Omni handoff: approved media locked into the render timeline")
    return summary


def _complete_handoff_stage_if_ready(run_id: str, manifest: dict[str, Any]) -> None:
    jobs = manifest.get("jobs") or []
    if jobs and all(item.get("status") in RESULT_READY for item in jobs):
        meta = load_run(run_id)
        if stage_record(meta, 8)["status"] != "complete":
            mark_stage(
                run_id,
                8,
                "complete",
                artifacts=["omni/manifest.json", "omni/jobs/"],
                summary={"results_received": len(jobs)},
            )


def _job(manifest: dict[str, Any], job_id: str) -> dict[str, Any]:
    job = next((item for item in manifest.get("jobs") or [] if item.get("job_id") == job_id), None)
    if job is None:
        raise KeyError(job_id)
    return job


def _save_manifest(paths, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = time.time()
    write_json(paths.root / "omni" / "manifest.json", manifest)
    for job in manifest.get("jobs") or []:
        write_json(paths.root / "omni" / "jobs" / job["job_id"] / "job.json", job)
