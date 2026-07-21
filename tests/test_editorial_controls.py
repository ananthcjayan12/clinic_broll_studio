from clinic_broll.pipeline.plan import normalize_plan
from clinic_broll.rendering.composition import _html


def _slot(**overrides):
    value = {
        "slot_id": "broll_001",
        "start": 1.0,
        "end": 4.0,
        "duration": 3.0,
        "layout_template": "broll_only",
        "panel_region": 1.0,
        "keep_subject_foreground": False,
        "show_caption": False,
        "caption_position": "auto",
        "text_overlay": "",
        "media_file": "broll_001.png",
        "media_kind": "image",
    }
    value.update(overrides)
    return value


def test_broll_only_is_full_screen_without_foreground_layer():
    document = _html({
        "mode": "still",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration": 5.0,
        "foreground_available": True,
        "captions_mode": "off",
        "phrases": [],
        "slots": [_slot()],
    })
    assert ".broll_only .panel{display:none}" in document
    assert ".generated.broll_only{inset:0;width:100%;height:100%" in document
    assert "foreground.webm" not in document


def test_captions_are_disabled_by_default_and_can_be_slot_gated():
    off_document = _html({
        "mode": "still", "width": 1080, "height": 1920, "fps": 30, "duration": 5.0,
        "foreground_available": False, "captions_mode": "off",
        "phrases": [{"start": 1.0, "end": 3.0, "text": "മലയാളം"}],
        "slots": [_slot(show_caption=True)],
    })
    assert 'const CAPTION_MODE="off";' in off_document
    assert "CAPTION_MODE==='auto'&&Boolean(activeSlot?.show_caption)" in off_document

    auto_document = _html({
        "mode": "still", "width": 1080, "height": 1920, "fps": 30, "duration": 5.0,
        "foreground_available": False, "captions_mode": "auto",
        "phrases": [{"start": 1.0, "end": 3.0, "text": "മലയാളം"}],
        "slots": [_slot(show_caption=True, caption_position="top")],
    })
    assert 'const CAPTION_MODE="auto";' in auto_document
    assert '"show_caption": true' in auto_document
    assert '"caption_position": "top"' in auto_document


def test_plan_normalization_enforces_broll_only_contract():
    plan = normalize_plan({
        "summary": "test",
        "slots": [{
            "slot_id": "broll_001",
            "start": 1,
            "end": 4,
            "transcript": "test",
            "purpose": "test",
            "priority": "useful",
            "layout_template": "broll_only",
            "visual_type": "medical_illustration",
            "keep_subject_foreground": True,
            "panel_region": 0.4,
            "show_caption": False,
            "caption_position": "auto",
            "still_brief": "test",
            "motion_brief": "test",
            "safety": [],
        }],
    }, 10.0)
    slot = plan["slots"][0]
    assert slot["layout_template"] == "broll_only"
    assert slot["keep_subject_foreground"] is False
    assert slot["panel_region"] == 1.0
    assert slot["show_caption"] is False
