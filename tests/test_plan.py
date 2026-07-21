from clinic_broll.pipeline.plan import normalize_plan


def test_plan_is_frame_aligned_and_non_overlapping():
    payload = {
        "summary": "test",
        "slots": [
            {
                "slot_id": "broll_001",
                "start": 1.0,
                "end": 5.0,
                "transcript": "a",
                "purpose": "a",
                "priority": "useful",
                "layout_template": "bottom_board",
                "visual_type": "medical_illustration",
                "keep_subject_foreground": True,
                "panel_region": 0.45,
                "still_brief": "a",
                "motion_brief": "a",
                "safety": [],
            },
            {
                "slot_id": "broll_002",
                "start": 4.0,
                "end": 8.0,
                "transcript": "b",
                "purpose": "b",
                "priority": "essential",
                "layout_template": "right_panel",
                "visual_type": "photoreal_broll",
                "keep_subject_foreground": True,
                "panel_region": 0.5,
                "still_brief": "b",
                "motion_brief": "b",
                "safety": [],
            },
        ],
    }
    plan = normalize_plan(payload, 10.0, fps=30)
    assert plan["slots"][0]["start_frame"] == 30
    assert plan["slots"][1]["start"] >= plan["slots"][0]["end"]
    assert plan["slots"][1]["duration"] > 0


def test_unknown_layout_falls_back():
    payload = {
        "summary": "test",
        "slots": [{
            "start": 1,
            "end": 4,
            "transcript": "x",
            "purpose": "x",
            "priority": "useful",
            "layout_template": "alien",
            "visual_type": "text_card",
            "keep_subject_foreground": True,
            "panel_region": 0.4,
            "still_brief": "x",
            "motion_brief": "x",
            "safety": [],
        }],
    }
    assert normalize_plan(payload, 8)["slots"][0]["layout_template"] == "bottom_board"


def test_plan_never_exceeds_master_duration():
    payload = {
        "summary": "edge",
        "slots": [{
            "start": 9.8,
            "end": 15.0,
            "transcript": "x",
            "purpose": "x",
            "priority": "useful",
            "layout_template": "bottom_board",
            "visual_type": "text_card",
            "keep_subject_foreground": True,
            "panel_region": 0.4,
            "still_brief": "x",
            "motion_brief": "x",
            "safety": [],
        }],
    }
    plan = normalize_plan(payload, 10.0)
    assert plan["slots"] == []
