from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import ffprobe, load_prompt, load_schema


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Final QA: inspecting streams, duration, and selected assets")
    meta = load_run(run_id)
    final = paths.renders / "final.mp4"
    if not final.exists():
        raise RuntimeError("Final render is missing")
    probe = ffprobe(final)
    source_meta = read_json(paths.source / "metadata.json", {})
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    final_duration = float((probe.get("format") or {}).get("duration") or 0)
    source_duration = float(source_meta.get("duration_seconds") or 0)
    streams = probe.get("streams") or []
    findings: list[dict[str, Any]] = []
    if not any(item.get("codec_type") == "video" for item in streams):
        findings.append({"severity": "error", "message": "Final output has no video stream", "slot_id": None})
    if not any(item.get("codec_type") == "audio" for item in streams):
        findings.append({"severity": "error", "message": "Final output has no audio stream", "slot_id": None})
    if abs(final_duration - source_duration) > 0.12:
        findings.append({"severity": "error", "message": f"Duration differs from source by {final_duration-source_duration:.3f}s", "slot_id": None})
    for slot in plan.get("slots", []):
        selected = slot.get("selected_motion") or slot.get("selected_still")
        if selected and not (paths.root / selected).exists():
            findings.append({"severity": "error", "message": "Selected slot asset is missing", "slot_id": slot["slot_id"]})
        if slot.get("layout_template") == "full_frame" and slot.get("priority") == "optional":
            findings.append({"severity": "warning", "message": "Optional slot uses full-frame cover; confirm doctor eye contact is not lost", "slot_id": slot["slot_id"]})
    technical_status = "fail" if any(item["severity"] == "error" for item in findings) else "pass"
    technical = {"status": technical_status, "source_duration": source_duration, "final_duration": final_duration, "findings": findings, "probe": probe}
    write_json(paths.qa / "technical.json", technical)

    manifest = {
        "transcript": read_json(paths.transcript / "transcript.json", {}),
        "plan": plan,
        "final_duration": final_duration,
        "source_duration": source_duration,
    }
    selection = meta["settings"]["task_models"]["semantic_qa"]
    system = load_prompt("semantic_qa.system.txt")
    user = load_prompt("semantic_qa.user.txt").format(
        manifest=json.dumps(manifest, ensure_ascii=False, indent=2),
        technical_findings=json.dumps(findings, ensure_ascii=False, indent=2),
    )
    schema = load_schema("qa.schema.json")
    try:
        append_log(paths, f"Final QA: requesting semantic review from {selection['provider']} ({selection['model']})")
        semantic = call_task_json(task="semantic_qa", selection=selection, system=system, user=user, cwd=paths.root, output_schema=schema)
    except Exception as exc:
        semantic = {"status": "needs_review", "summary": f"Automated semantic QA unavailable: {exc}", "findings": []}
    write_json(paths.qa / "semantic.json", semantic)
    overall = "fail" if technical_status == "fail" or semantic.get("status") == "fail" else ("needs_review" if semantic.get("status") == "needs_review" else "pass")

    repair_advice = None
    if overall != "pass":
        repair_selection = meta["settings"]["task_models"]["repair_advisor"]
        repair_system = load_prompt("repair_advisor.system.txt")
        measured = {"status": overall, "technical": technical, "semantic": semantic}
        repair_user = load_prompt("repair_advisor.user.txt").format(
            qa_report=json.dumps(measured, ensure_ascii=False, indent=2),
            plan=json.dumps(plan, ensure_ascii=False, indent=2),
        )
        try:
            repair_advice = call_task_json(
                task="repair_advisor", selection=repair_selection, system=repair_system,
                user=repair_user, cwd=paths.root, output_schema=schema,
            )
        except Exception as exc:
            repair_advice = {
                "status": "needs_review",
                "summary": f"Automated repair advice unavailable: {exc}",
                "findings": [],
            }
        write_json(paths.qa / "repair-advice.json", repair_advice)

    report = {"status": overall, "technical": technical, "semantic": semantic, "repair_advice": repair_advice}
    append_log(paths, f"Final QA: completed with status {overall}")
    write_json(paths.qa / "final-report.json", report)
    artifacts = ["qa/technical.json", "qa/semantic.json", "qa/final-report.json"]
    if repair_advice is not None:
        artifacts.append("qa/repair-advice.json")
    return {"artifacts": artifacts, "summary": {"status": overall, "technical_findings": len(findings)}}
