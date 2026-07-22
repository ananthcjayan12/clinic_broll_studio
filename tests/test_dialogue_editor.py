from clinic_broll.pipeline.dialogue import (
    _approved_removals,
    _clean_master_args,
    _detect_candidates,
    _normalise_plan,
    _timeline_map,
    create_manual_removal,
)
from clinic_broll.core import paths as paths_module
from clinic_broll.core.io import read_json, write_json
from clinic_broll.core.state import create_run
from clinic_broll.pipeline import dialogue


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


def test_ffmpeg_filter_uses_frame_accurate_video_cuts_and_audio_fades(tmp_path):
    args = _clean_master_args(tmp_path / "source.mp4", [(0.0, 2.0), (3.0, 5.0)], 0.025, tmp_path / "out.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert "acrossfade" in graph
    assert "xfade" not in graph
    assert "trim=start=0.000000:end=1.975000" in graph
    assert "[v0][v1]concat=n=2:v=1:a=0[vcat]" in graph
    assert "[0:v]split=2[vin0][vin1]" in graph
    assert "[0:a]asplit=2[ain0][ain1]" in graph
    assert graph.count("fps=30,settb=AVTB,setpts=PTS-STARTPTS") == 2


def test_ffmpeg_filter_uses_source_frame_rate_for_each_trimmed_branch(tmp_path):
    args = _clean_master_args(
        tmp_path / "source.mp4",
        [(0.0, 2.0), (3.0, 5.0), (6.0, 8.0)],
        0.025,
        tmp_path / "out.mp4",
        fps=29.97,
    )
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count("fps=29.97,settb=AVTB,setpts=PTS-STARTPTS") == 3


def test_operator_can_add_approved_manual_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("manual-dialogue", original_filename="source.mp4")
    root = tmp_path / "manual-dialogue"
    write_json(root / "dialogue" / "edit-plan.json", {
        "version": "1.0", "source_duration": 8.0, "edits": [],
    })
    write_json(root / "dialogue" / "source-transcript.json", _transcript())

    edit = create_manual_removal("manual-dialogue", start=0.58, end=1.28)

    assert edit["category"] == "manual"
    assert edit["status"] == "approved"
    assert edit["resolved_action"] == "remove"
    assert edit["transcript"] == "brush brush"
    assert read_json(root / "dialogue" / "edit-plan.json")["edits"] == [edit]


def test_full_narration_analysis_runs_without_deterministic_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("full-audit", original_filename="source.mp4")
    root = tmp_path / "full-audit"
    transcript = {
        "duration_seconds": 2.0,
        "language_code": "ml",
        "words": [
            {"word": "നല്ല", "start": 0.1, "end": 0.4},
            {"word": "വാചകം", "start": 0.45, "end": 0.8},
        ],
        "phrases": [{"text": "നല്ല വാചകം", "start": 0.1, "end": 0.8}],
    }
    write_json(root / "dialogue" / "source-transcript.json", transcript)
    calls = []

    def fake_call_task_json(**kwargs):
        calls.append(kwargs)
        return {"summary": "Full audit complete", "edits": []}

    monkeypatch.setattr(dialogue, "call_task_json", fake_call_task_json)

    dialogue.run_analysis("full-audit")

    assert len(calls) == 1
    assert "ENTIRE" in calls[0]["system"]
    assert "do not restrict" in calls[0]["user"]
