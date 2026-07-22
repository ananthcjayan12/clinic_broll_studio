from pathlib import Path

from PIL import Image

from clinic_broll.pipeline.editorial import normalize_editorial_plan
from clinic_broll.pipeline.editorial_policy import apply_editorial_policy, repair_editorial_payload
from clinic_broll.pipeline.graphics import render_local_graphic
from clinic_broll.pipeline.stills import _candidate_count


def _dense_payload():
    scenes = []
    for index in range(12):
        start = index * 4.0
        end = start + 4.0
        scenes.append({
            "start": start,
            "end": end,
            "narration": "enamel acid brushing explanation" if index % 2 else "food meal bathroom lifestyle",
            "editorial_purpose": "Explain every phrase visually",
            "composition_mode": "split_layout",
            "layout_variant": "broll_top_speaker_bottom",
            "subject_mode": "cropped_original",
            "visual_style": "natural_colorful",
            "visual_brief": "acid enamel and food",
            "motion_brief": "subtle motion",
            "camera_move": "static",
            "transition_in": "direct_cut",
            "transition_out": "direct_cut",
            "caption_mode": "off",
            "sound_intent": [],
            "priority": "essential" if index < 7 else "useful",
        })
    return {"summary": "over-dense plan", "scenes": scenes}


def test_alias_repair_keeps_valid_director_plan_instead_of_triggering_fallback():
    repaired = repair_editorial_payload(_dense_payload())
    assert all(scene["visual_style"] == "mixed" for scene in repaired["scenes"])


def test_medium_policy_caps_visual_count_coverage_and_generation_requests():
    normalized = normalize_editorial_plan(_dense_payload(), 48.0, fps=30)
    policy = apply_editorial_policy(normalized, {
        "editing_intensity": "medium",
        "image_candidates_per_slot": 1,
        "fps": 30,
    })
    report = policy["budget_report"]
    assert report["visual_scenes"] <= 5
    assert report["visual_coverage_ratio"] <= 0.32
    assert report["expected_image_generations"] <= 6
    assert report["violations"] == []
    assert policy["scenes"][0]["visual_strategy"] == "none"
    assert policy["scenes"][-1]["visual_strategy"] == "none"


def test_candidate_setting_is_a_hard_maximum_for_essential_scenes():
    meta = {"settings": {"image_candidates_per_slot": 2}}
    assert _candidate_count(meta, {"priority": "essential", "visual_strategy": "generated_photo"}) == 2
    assert _candidate_count(meta, {"priority": "essential", "visual_strategy": "dental_diagram"}) == 1


def test_dental_visuals_render_locally_without_a_media_provider(tmp_path: Path):
    destination = tmp_path / "diagram.png"
    record = render_local_graphic(destination, {
        "visual_strategy": "dental_diagram",
        "transcript": "acidic saliva and enamel",
        "purpose": "Show saliva neutralising the acidic environment",
        "still_brief": "clean pH diagram",
    }, width=360, height=640)
    assert destination.exists()
    assert record["provider"] == "local_graphic"
    with Image.open(destination) as image:
        assert image.size == (360, 640)
