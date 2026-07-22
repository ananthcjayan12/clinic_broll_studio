from __future__ import annotations

from typing import Any

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from . import matte


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    editorial = read_json(paths.editorial / "editorial-plan.json", {"scenes": []})
    visual = read_json(paths.analysis / "visual-analysis.json", {"windows": []})
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    if not editorial.get("scenes"):
        raise RuntimeError("Editorial plan is required before preparation")

    reframe_plan = {"version": "2.0", "scenes": []}
    windows = visual.get("windows") or []
    for scene in editorial["scenes"]:
        midpoint = (float(scene["start"]) + float(scene["end"])) / 2
        if windows:
            window = min(
                windows,
                key=lambda item: abs(
                    float(item.get("start", 0))
                    + (float(item.get("end", 0)) - float(item.get("start", 0))) / 2
                    - midpoint
                ),
            )
        else:
            window = {}
        preferred_side = str(window.get("negative_space") or "right")
        layout = str(scene.get("layout_variant") or "talking_head")
        if layout == "speaker_left_broll_right":
            speaker_region = "left"
        elif layout == "broll_left_speaker_right":
            speaker_region = "right"
        elif layout == "broll_top_speaker_bottom":
            speaker_region = "bottom"
        elif layout == "speaker_top_broll_bottom":
            speaker_region = "top"
        else:
            speaker_region = preferred_side
        reframe_plan["scenes"].append({
            "scene_id": scene["scene_id"],
            "start": scene["start"],
            "end": scene["end"],
            "face_box": window.get("face_box", [0.32, 0.12, 0.68, 0.5]),
            "eye_line_y": window.get("eye_line_y", 0.3),
            "safe_crop": window.get("safe_crop", {"x": 0.16, "y": 0.04, "width": 0.68, "height": 0.78}),
            "speaker_region": speaker_region,
            "gaze_direction": window.get("gaze_direction", "center"),
            "negative_space": preferred_side,
            "hand_activity": window.get("hand_activity", 0.0),
            "matte_risk": window.get("matte_risk", 1.0),
        })
    write_json(paths.editorial / "reframe-plan.json", reframe_plan)

    by_scene = {item["scene_id"]: item for item in reframe_plan["scenes"]}
    for slot in plan.get("slots", []):
        slot["reframe"] = by_scene.get(slot.get("scene_id"), {})
        if slot.get("composition_mode") == "talking_head":
            slot["layout_template"] = "talking_head"
            slot["subject_mode"] = "original"
            slot["keep_subject_foreground"] = False
            continue
        risk = float(slot["reframe"].get("matte_risk", 1.0))
        treatment = str(meta["settings"].get("foreground_treatment") or "auto")
        if slot.get("subject_mode") == "matte_foreground" and (treatment == "never" or risk >= 0.35):
            slot["subject_mode"] = "cropped_original"
            slot["keep_subject_foreground"] = False
            slot["composition_mode"] = "split_layout"
            if slot.get("layout_variant") == "layered_foreground":
                slot["layout_variant"] = "broll_top_speaker_bottom"
                slot["layout_template"] = "split_top"
            slot.setdefault("safety", []).append("Foreground treatment changed to a crop-based split because edge risk was high")
    write_json(paths.plan / "broll_plan.json", plan)

    artifacts = ["editorial/reframe-plan.json", "plan/broll_plan.json"]
    needs_matte = any(slot.get("keep_subject_foreground") for slot in plan.get("slots", []))
    if needs_matte and meta["settings"].get("matting_provider") != "none":
        append_log(paths, "Preparation: approved low-risk scenes require a foreground matte")
        matte_result = matte.run(run_id)
        artifacts.extend(matte_result.get("artifacts", []))
    else:
        append_log(paths, "Preparation: no foreground matte required; split and picture-in-picture layouts use the original video")
    return {
        "artifacts": artifacts,
        "summary": {
            "scenes": len(reframe_plan["scenes"]),
            "matte_generated": needs_matte and meta["settings"].get("matting_provider") != "none",
        },
    }
