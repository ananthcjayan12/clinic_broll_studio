from clinic_broll.pipeline.dialogue import (
    _approved_removals,
    _clean_master_args,
    _detect_candidates,
    _normalise_plan,
    _timeline_map,
)


def _transcript():
    return {
        "duration_seconds": 8.0,
        "words": [
            {"id": "word_00001", "word": "അപ്പോ", "start": 0.2, "end": 0.55},
            {"id": "word_00002", "word": "brush", "start": 0.62, "end": 0.92},
            {"id": "word_00003", "word": "brush", "start": 0.96, "end": 1.25},
            {"id": "word_00004", "word": "ചെയ്യരുത്", "start": 2.4, "end": 3.0},
        ],
        "phrases": [
            {"id": "phrase_0001", "text": "അപ്പോ brush brush", "start": 0.2, "end": 1.25},
            {"id": "phrase_0002", "text": "ചെയ്യരുത്", "start": 2.4, "end": 3.0},
        ],
    }


def test_detection_finds_filler_repeat_and_pause():
    candidates = _detect_candidates(_transcript(), "balanced")
    categories = {item["category"] for item in candidates}
    assert {"filler", "repeated_word", "long_pause"} <= categories


def test_protected_negation_is_forced_to_review():
    payload = {
        "summary": "test",
        "edits": [{
            "start": 2.4,
            "end": 3.0,
            "category": "other",
            "transcript": "ചെയ്യരുത്",
            "recommended_action": "remove",
            "confidence": 0.9,
            "reason": "test",
            "requires_review": False,
            "medical_risk": "low",
        }],
    }
    plan = _normalise_plan(payload, _transcript(), "balanced")
    assert plan["edits"][0]["requires_review"] is True


def test_approved_short_pause_keeps_target_duration():
    edits = [{
        "status": "approved",
        "resolved_action": "shorten_pause",
        "recommended_action": "shorten_pause",
        "start": 1.0,
        "end": 2.0,
        "target_pause_seconds": 0.3,
    }]
    assert _approved_removals(edits, 5.0) == [(1.3, 2.0)]


def test_timeline_map_accounts_for_crossfade_overlap():
    mapping = _timeline_map([(0.0, 2.0), (3.0, 5.0)], 0.025)
    assert mapping[0]["clean_end"] == 2.0
    assert mapping[1]["clean_start"] == 1.975


def test_ffmpeg_filter_uses_audio_and_video_continuity_fades(tmp_path):
    args = _clean_master_args(tmp_path / "source.mp4", [(0.0, 2.0), (3.0, 5.0)], 0.025, tmp_path / "out.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert "xfade=transition=fade" in graph
    assert "acrossfade" in graph
