from clinic_broll.omni_v3.director import normalise_plan, to_broll_plan


def transcript(duration=50.0):
    return {
        "duration_seconds": duration,
        "phrases": [
            {"start": 0.0, "end": 10.0, "text": "Hook and explanation"},
            {"start": 10.0, "end": 25.0, "text": "Dental mechanism"},
            {"start": 25.0, "end": 50.0, "text": "Advice and call to action"},
        ],
    }


def test_plan_fills_original_gaps_and_caps_segments():
    raw = {
        "summary": "test",
        "segments": [
            {
                "start": 5,
                "end": 20,
                "treatment": "omni_presenter_edit",
                "purpose": "transform",
                "visual_concept": "environment change",
                "prompt": "Change the background",
                "priority": "essential",
            },
            {
                "start": 30,
                "end": 35,
                "treatment": "omni_broll",
                "purpose": "support",
                "visual_concept": "macro visual",
                "prompt": "Replace the scene",
                "priority": "useful",
            },
        ],
    }
    plan = normalise_plan(
        raw,
        transcript(),
        50.0,
        {"max_omni_jobs": 5, "max_omni_credits": 280, "omni_segment_max_seconds": 8},
    )
    assert plan["scenes"][0]["treatment"] == "original"
    assert plan["scenes"][-1]["treatment"] == "original"
    assert max(item["duration"] for item in plan["jobs"]) <= 8
    assert plan["estimated_credits"] == 80


def test_credit_budget_fails_closed():
    raw = {
        "summary": "dense",
        "segments": [
            {
                "start": index * 5,
                "end": index * 5 + 4,
                "treatment": "omni_presenter_edit",
                "purpose": "edit",
                "visual_concept": "visual",
                "prompt": "Change background",
                "priority": "useful",
            }
            for index in range(8)
        ],
    }
    plan = normalise_plan(
        raw,
        transcript(),
        50.0,
        {"max_omni_jobs": 8, "max_omni_credits": 120, "omni_segment_max_seconds": 8},
    )
    assert len(plan["jobs"]) == 3
    assert plan["estimated_credits"] == 120


def test_compatibility_plan_uses_full_frame_video_overlays():
    raw = {
        "summary": "test",
        "segments": [{
            "start": 5,
            "end": 10,
            "treatment": "omni_presenter_edit",
            "purpose": "edit",
            "visual_concept": "visual",
            "prompt": "Change background",
            "priority": "essential",
        }],
    }
    plan = normalise_plan(raw, transcript(), 50.0, {"max_omni_jobs": 5, "max_omni_credits": 280})
    compatibility = to_broll_plan(plan)
    omni_slot = next(item for item in compatibility["slots"] if item.get("job_id"))
    assert omni_slot["layout_template"] == "broll_only"
    assert omni_slot["subject_mode"] == "hidden"
    assert omni_slot["status"] == "plan_approved"
