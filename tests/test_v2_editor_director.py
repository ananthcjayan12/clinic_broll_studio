from clinic_broll.pipeline.choreography import normalize_choreography
from clinic_broll.pipeline.editorial import editorial_to_broll_plan, normalize_editorial_plan
from clinic_broll.pipeline.sound import _fallback_plan, _shortlist
from clinic_broll.rendering.composition import _html


def _editorial_payload():
    return {
        "summary": "modern dental reel",
        "scenes": [
            {
                "start": 0.0,
                "end": 2.0,
                "narration": "Hook",
                "editorial_purpose": "Build trust",
                "composition_mode": "talking_head",
                "layout_variant": "talking_head",
                "subject_mode": "original",
                "visual_style": "natural_lifestyle",
                "camera_move": "emphasis_punch",
                "transition_in": "direct_cut",
                "transition_out": "direct_cut",
                "caption_mode": "off",
                "sound_intent": [],
            },
            {
                "start": 2.0,
                "end": 6.0,
                "narration": "Explain enamel",
                "editorial_purpose": "Show a colourful macro explanation",
                "composition_mode": "split_layout",
                "layout_variant": "broll_top_speaker_bottom",
                "subject_mode": "cropped_original",
                "visual_style": "colorful_macro",
                "visual_brief": "Natural toothbrush macro",
                "motion_brief": "Slow bristle movement",
                "camera_move": "static",
                "transition_in": "clean_push",
                "transition_out": "direct_cut",
                "caption_mode": "off",
                "sound_intent": ["soft transition"],
            },
            {
                "start": 6.0,
                "end": 9.0,
                "narration": "Show full visual",
                "editorial_purpose": "Complex process",
                "composition_mode": "full_broll",
                "layout_variant": "full_broll",
                "subject_mode": "hidden",
                "visual_style": "clean_medical_illustration",
                "camera_move": "slow_push",
                "transition_in": "zoom_match",
                "transition_out": "direct_cut",
                "caption_mode": "off",
                "sound_intent": ["full screen reveal"],
            },
        ],
    }


def test_editorial_scene_graph_maps_to_modern_compatibility_layouts():
    editorial = normalize_editorial_plan(_editorial_payload(), 9.0, fps=30)
    bible = {
        "look": "natural colourful",
        "palette": ["coral", "mint", "cream"],
        "avoid": ["sterile blue render"],
    }
    plan = editorial_to_broll_plan(editorial, bible)

    assert [scene["scene_id"] for scene in editorial["scenes"]] == ["scene_001", "scene_002", "scene_003"]
    assert plan["slots"][0]["status"] == "talking_head"
    assert plan["slots"][1]["layout_template"] == "split_top"
    assert plan["slots"][1]["subject_mode"] == "cropped_original"
    assert plan["slots"][2]["layout_template"] == "broll_only"
    assert plan["slots"][2]["keep_subject_foreground"] is False


def test_split_layout_renders_original_source_as_a_separate_subject_layer():
    document = _html({
        "mode": "still",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration": 4.0,
        "timeline_offset": 0.0,
        "foreground_available": False,
        "captions_mode": "off",
        "phrases": [],
        "slots": [{
            "slot_id": "broll_001",
            "scene_id": "scene_001",
            "start": 0.0,
            "end": 4.0,
            "duration": 4.0,
            "composition_mode": "split_layout",
            "layout_variant": "broll_top_speaker_bottom",
            "subject_mode": "cropped_original",
            "layout_template": "split_top",
            "panel_region": 0.56,
            "keep_subject_foreground": False,
            "show_caption": False,
            "caption_position": "auto",
            "text_overlay": "",
            "media_file": "broll_001.png",
            "media_kind": "image",
            "reframe": {"face_box": [0.3, 0.1, 0.7, 0.5]},
            "choreography": {"camera_move": "subtle_punch_in", "from_scale": 1.0, "to_scale": 1.065},
        }],
    })

    assert 'class="slot clip split_top' in document
    assert 'id="broll_001-subject"' in document
    assert 'src="assets/source-head.mp4"' in document
    assert '.split_top .subject-copy' in document
    assert '"to_scale": 1.065' in document


def test_choreography_normalization_applies_safe_zoom_limits():
    editorial = normalize_editorial_plan(_editorial_payload(), 9.0, fps=30)
    choreography = normalize_choreography({
        "summary": "test",
        "cues": [{
            "scene_id": "scene_001",
            "start": 0,
            "end": 2,
            "camera_move": "emphasis_punch",
            "transition_in": "direct_cut",
            "transition_out": "direct_cut",
            "emphasis_preset": "warning_pulse",
            "from_scale": 0.8,
            "to_scale": 1.9,
            "anchor": "face",
        }],
    }, editorial)

    first = choreography["cues"][0]
    assert first["from_scale"] == 1.0
    assert first["to_scale"] == 1.15
    assert len(choreography["cues"]) == 3


def test_sound_shortlist_and_fallback_only_use_manifest_ids():
    sounds = [
        {
            "id": "transition_whoosh_fast",
            "path": "sounds/transitions/transition_whoosh_fast.wav",
            "title": "Fast transition whoosh",
            "category": "transitions",
            "tags": ["whoosh", "transition", "fast"],
            "best_for": ["visual transition"],
            "search_text": "fast whoosh visual transition",
            "intensity": 0.7,
            "technical": {"duration_seconds": 0.6},
        },
        {
            "id": "ambient_room",
            "path": "sounds/ambience/ambient_room.wav",
            "title": "Room ambience",
            "category": "ambience",
            "tags": ["room", "bed"],
            "best_for": ["background"],
            "search_text": "quiet room ambience background",
            "intensity": 0.2,
            "technical": {"duration_seconds": 4.0},
        },
    ]
    intents = [{"scene_id": "scene_002", "time": 2.1, "intent": "fast visual transition", "energy": 0.7}]
    shortlisted = _shortlist(intents, sounds, top_n=2)
    plan = _fallback_plan(intents, shortlisted, "medium")

    assert shortlisted[0]["candidates"][0]["id"] == "transition_whoosh_fast"
    assert plan["cues"][0]["sound_id"] in {sound["id"] for sound in sounds}
    assert plan["cues"][0]["gain_db"] <= -17
