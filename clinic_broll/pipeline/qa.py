from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from ..providers.registry import call_task_json
from .common import ffprobe, load_prompt, load_schema


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "V2 QA: inspecting technical streams, editorial variety, generated visuals, and sound cues")
    meta = load_run(run_id)
    final = paths.renders / "final.mp4"
    if not final.exists():
        raise RuntimeError("Final render is missing")
    probe = ffprobe(final)
    source_meta = read_json(paths.source / "metadata.json", {})
    transcript = read_json(paths.transcript / "transcript.json", {})
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    editorial = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    visual_bible = read_json(paths.editorial / "visual-bible.json", {})
    choreography = read_json(paths.editorial / "edit-choreography.json", {"cues": []})
    sound_plan = read_json(paths.sound / "sound-plan.json", {"cues": []})
    final_duration = float((probe.get("format") or {}).get("duration") or 0)
    source_duration = float(source_meta.get("duration_seconds") or 0)
    streams = probe.get("streams") or []
    findings: list[dict[str, Any]] = []

    if not any(item.get("codec_type") == "video" for item in streams):
        findings.append(_finding("error", "Final output has no video stream"))
    if not any(item.get("codec_type") == "audio" for item in streams):
        findings.append(_finding("error", "Final output has no audio stream"))
    if abs(final_duration - source_duration) > 0.15:
        findings.append(_finding("error", f"Duration differs from the clean master by {final_duration-source_duration:.3f}s"))

    active_slots = [slot for slot in plan.get("slots", []) if slot.get("status") != "rejected"]
    for slot in active_slots:
        selected = slot.get("selected_motion") or slot.get("selected_still")
        if slot.get("composition_mode") != "talking_head" and not selected:
            findings.append(_finding("warning", "Visual scene has no approved generated asset and may fall back to talking head", slot))
        if selected and not (paths.root / selected).exists():
            findings.append(_finding("error", "Selected visual asset is missing", slot))
        if slot.get("layout_variant") == "full_broll" and slot.get("priority") == "optional":
            findings.append(_finding("warning", "Optional scene uses full-screen B-roll; confirm eye contact is not unnecessarily lost", slot))
        if slot.get("subject_mode") == "matte_foreground":
            risk = float((slot.get("reframe") or {}).get("matte_risk", 1.0))
            if risk >= 0.35:
                findings.append(_finding("error", f"Foreground matte is enabled despite high local edge-risk score {risk:.2f}", slot))
        review = slot.get("candidate_review") or {}
        selected_path = str(review.get("selected_path") or "")
        selected_score = next((item for item in review.get("scores", []) if item.get("path") == selected_path), None)
        if selected_score:
            if float(selected_score.get("artificial_appearance", 0)) > 45:
                findings.append(_finding("warning", "Selected generated visual has a high artificial-appearance score", slot))
            if float(selected_score.get("medical_accuracy", 100)) < 70:
                findings.append(_finding("warning", "Selected generated visual needs explicit medical-accuracy review", slot))

    layouts = [str(slot.get("layout_variant")) for slot in active_slots]
    layout_counts = Counter(layouts)
    if active_slots and max(layout_counts.values(), default=0) / len(active_slots) > 0.65 and len(layout_counts) > 1:
        findings.append(_finding("warning", "One composition layout dominates more than 65% of the edit; consider more visual rhythm"))
    if len(active_slots) >= 4 and len(layout_counts) == 1:
        findings.append(_finding("warning", "Every scene uses the same composition layout"))

    camera_moves = [str(item.get("camera_move") or "static") for item in choreography.get("cues", [])]
    punch_count = sum(item in {"subtle_punch_in", "emphasis_punch"} for item in camera_moves)
    if camera_moves and punch_count / len(camera_moves) > 0.6:
        findings.append(_finding("warning", "Punch-ins are used on more than 60% of scenes"))
    non_direct_transitions = [
        item for item in choreography.get("cues", [])
        if item.get("transition_in") not in {None, "direct_cut", "soft_crossfade"}
    ]
    if choreography.get("cues") and len(non_direct_transitions) / len(choreography["cues"]) > 0.55:
        findings.append(_finding("warning", "Designed transitions are overused; prefer more direct cuts"))

    enabled_sfx = [cue for cue in sound_plan.get("cues", []) if cue.get("enabled")]
    if source_duration > 0:
        cues_per_30 = len(enabled_sfx) / source_duration * 30
        density_limit = {"low": 4.5, "medium": 8.5, "high": 12.5}.get(str(sound_plan.get("density") or "medium"), 8.5)
        if cues_per_30 > density_limit:
            findings.append(_finding("warning", f"Sound-effect density is {cues_per_30:.1f} cues per 30 seconds"))
    sound_ids = [str(cue.get("sound_id")) for cue in enabled_sfx]
    repeated_sounds = [sound_id for sound_id, count in Counter(sound_ids).items() if count >= 4]
    if repeated_sounds:
        findings.append(_finding("warning", "The same sound effect is reused four or more times: " + ", ".join(repeated_sounds)))
    for cue in enabled_sfx:
        if float(cue.get("gain_db", -18)) > -10:
            findings.append(_finding("warning", f"Sound cue {cue.get('cue_id')} is louder than -10 dB and may mask narration"))
        resolved = cue.get("resolved_path")
        if resolved and not __import__("pathlib").Path(resolved).exists():
            findings.append(_finding("error", f"Sound cue file is missing: {cue.get('sound_id')}"))

    technical_status = "fail" if any(item["severity"] == "error" for item in findings) else "pass"
    technical = {
        "status": technical_status,
        "source_duration": source_duration,
        "final_duration": final_duration,
        "findings": findings,
        "metrics": {
            "active_scenes": len(active_slots),
            "layout_counts": dict(layout_counts),
            "punch_in_count": punch_count,
            "designed_transition_count": len(non_direct_transitions),
            "enabled_sfx": len(enabled_sfx),
        },
        "probe": probe,
    }
    write_json(paths.qa / "technical.json", technical)

    manifest = {
        "transcript": transcript,
        "editorial": editorial,
        "visual_bible": visual_bible,
        "plan": plan,
        "choreography": choreography,
        "sound_plan": sound_plan,
        "final_duration": final_duration,
        "source_duration": source_duration,
    }
    schema = load_schema("qa.schema.json")

    edit_selection = meta["settings"]["task_models"]["final_edit_reviewer"]
    edit_system = load_prompt("final_edit_reviewer.system.txt")
    edit_user = load_prompt("final_edit_reviewer.user.txt").format(
        manifest=json.dumps(manifest, ensure_ascii=False, indent=2),
        technical_findings=json.dumps(findings, ensure_ascii=False, indent=2),
    )
    try:
        append_log(paths, f"V2 QA: requesting final edit review from {edit_selection['provider']} ({edit_selection['model']})")
        edit_review = call_task_json(
            task="final_edit_reviewer",
            selection=edit_selection,
            system=edit_system,
            user=edit_user,
            cwd=paths.root,
            output_schema=schema,
        )
    except Exception as exc:
        edit_review = {"status": "needs_review", "summary": f"Automated edit review unavailable: {exc}", "findings": []}
    write_json(paths.qa / "edit-review.json", edit_review)

    semantic_selection = meta["settings"]["task_models"]["semantic_qa"]
    semantic_system = load_prompt("semantic_qa.system.txt")
    semantic_user = load_prompt("semantic_qa.user.txt").format(
        manifest=json.dumps(manifest, ensure_ascii=False, indent=2),
        technical_findings=json.dumps(findings, ensure_ascii=False, indent=2),
    )
    try:
        append_log(paths, f"V2 QA: requesting clinical review from {semantic_selection['provider']} ({semantic_selection['model']})")
        semantic = call_task_json(
            task="semantic_qa",
            selection=semantic_selection,
            system=semantic_system,
            user=semantic_user,
            cwd=paths.root,
            output_schema=schema,
        )
    except Exception as exc:
        semantic = {"status": "needs_review", "summary": f"Automated semantic QA unavailable: {exc}", "findings": []}
    write_json(paths.qa / "semantic.json", semantic)

    statuses = {technical_status, str(edit_review.get("status")), str(semantic.get("status"))}
    overall = "fail" if "fail" in statuses else ("needs_review" if "needs_review" in statuses else "pass")
    repair_advice = None
    if overall != "pass":
        repair_selection = meta["settings"]["task_models"]["repair_advisor"]
        repair_system = load_prompt("repair_advisor.system.txt")
        measured = {"status": overall, "technical": technical, "edit_review": edit_review, "semantic": semantic}
        repair_user = load_prompt("repair_advisor.user.txt").format(
            qa_report=json.dumps(measured, ensure_ascii=False, indent=2),
            plan=json.dumps(plan, ensure_ascii=False, indent=2),
        )
        try:
            repair_advice = call_task_json(
                task="repair_advisor",
                selection=repair_selection,
                system=repair_system,
                user=repair_user,
                cwd=paths.root,
                output_schema=schema,
            )
        except Exception as exc:
            repair_advice = {"status": "needs_review", "summary": f"Automated repair advice unavailable: {exc}", "findings": []}
        write_json(paths.qa / "repair-advice.json", repair_advice)

    report = {
        "status": overall,
        "technical": technical,
        "edit_review": edit_review,
        "semantic": semantic,
        "repair_advice": repair_advice,
    }
    append_log(paths, f"V2 QA: completed with status {overall}")
    write_json(paths.qa / "final-report.json", report)
    artifacts = ["qa/technical.json", "qa/edit-review.json", "qa/semantic.json", "qa/final-report.json"]
    if repair_advice is not None:
        artifacts.append("qa/repair-advice.json")
    return {"artifacts": artifacts, "summary": {"status": overall, "technical_findings": len(findings)}}


def _finding(severity: str, message: str, slot: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "message": message,
        "slot_id": slot.get("slot_id") if slot else None,
        "scene_id": slot.get("scene_id") if slot else None,
    }
