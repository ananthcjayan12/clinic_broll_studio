from clinic_broll.core import paths as paths_module
from clinic_broll.core.state import create_run, load_run, mark_stage, rewind_run
from clinic_broll.core.io import read_json, write_json
from clinic_broll.studio.dialogue_api import _prepare_dialogue_edit


def test_rewind_preserves_upload_and_clears_stale_still_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("demo-v01", original_filename="source.mov")
    root = tmp_path / "demo-v01"
    (root / "source").mkdir(exist_ok=True)
    (root / "source" / "upload.mov").write_bytes(b"original")
    for name in ("master.mp4", "proxy.mp4", "speech.wav", "metadata.json"):
        (root / "source" / name).write_bytes(b"generated")
    asset = root / "assets" / "stills" / "broll_001" / "v01" / "still.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png")
    write_json(root / "plan" / "broll_plan.json", {
        "slots": [{
            "slot_id": "broll_001",
            "status": "still_approved",
            "selected_still": "assets/stills/broll_001/v01/still.png",
            "selected_motion": None,
            "versions": {"stills": [{"path": "assets/stills/broll_001/v01/still.png"}], "motion": []},
            "review": {"plan": "approve_plan", "still": "approve_still", "motion": None},
        }]
    })
    for number in range(1, 10):
        mark_stage("demo-v01", number, "complete")

    # Stage 8 is still generation in the dialogue-cleanup pipeline. Rewinding
    # from stage 6 would intentionally rewind the B-roll plan itself.
    rewind_run("demo-v01", 8)

    assert (root / "source" / "upload.mov").exists()
    plan = read_json(root / "plan" / "broll_plan.json")
    slot = plan["slots"][0]
    assert slot["status"] == "plan_approved"
    assert slot["versions"]["stills"] == []
    assert slot["selected_still"] is None
    assert not asset.exists()
    assert any((root / ".history").iterdir())


def test_rewind_stage_one_preserves_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("demo-v02", original_filename="source.mp4")
    root = tmp_path / "demo-v02"
    (root / "source").mkdir(exist_ok=True)
    (root / "source" / "upload.mp4").write_bytes(b"original")
    (root / "source" / "master.mp4").write_bytes(b"master")
    rewind_run("demo-v02", 1)
    assert (root / "source" / "upload.mp4").exists()
    assert not (root / "source" / "master.mp4").exists()


def test_rewind_stage_three_restores_step_one_and_two_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("demo-v03", original_filename="source.mp4")
    root = tmp_path / "demo-v03"
    backups = {
        "source-master.mp4": ("source/master.mp4", b"original-master"),
        "source-proxy.mp4": ("source/proxy.mp4", b"original-proxy"),
        "source-speech.wav": ("source/speech.wav", b"original-speech"),
        "source-metadata.json": ("source/metadata.json", b'{"duration_seconds": 10}'),
        "source-transcript.json": ("transcript/transcript.json", b'{"words": []}'),
        "source-captions.srt": ("transcript/captions.srt", b"original captions"),
    }
    for backup_name, (canonical_name, original) in backups.items():
        backup = root / "dialogue" / backup_name
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        canonical = root / canonical_name
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"cleaned")
    (root / "dialogue" / "edit-plan.json").write_text("{}", encoding="utf-8")
    for number in range(1, 6):
        mark_stage("demo-v03", number, "complete")

    rewind_run("demo-v03", 3)

    meta = load_run("demo-v03")
    assert [stage["status"] for stage in meta["stages"][:5]] == [
        "complete", "complete", "pending", "pending", "pending",
    ]
    for canonical_name, original in backups.values():
        assert (root / canonical_name).read_bytes() == original


def test_rewind_stage_three_does_not_archive_outputs_of_pending_stage_four(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("demo-v04", original_filename="source.mp4")
    root = tmp_path / "demo-v04"
    master = root / "source" / "master.mp4"
    transcript = root / "transcript" / "transcript.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"step-one-master")
    transcript.write_bytes(b"step-two-transcript")
    for number in range(1, 4):
        mark_stage("demo-v04", number, "complete")

    rewind_run("demo-v04", 3)

    assert master.read_bytes() == b"step-one-master"
    assert transcript.read_bytes() == b"step-two-transcript"


def test_dialogue_change_reopens_completed_clean_master(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "RUNS_ROOT", tmp_path)
    create_run("demo-v05", original_filename="source.mp4")
    root = tmp_path / "demo-v05"
    backup = root / "dialogue" / "source-master.mp4"
    master = root / "source" / "master.mp4"
    backup.parent.mkdir(parents=True, exist_ok=True)
    master.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(b"original-master")
    master.write_bytes(b"clean-master")
    for number in range(1, 5):
        mark_stage("demo-v05", number, "complete")

    _prepare_dialogue_edit("demo-v05")

    meta = load_run("demo-v05")
    assert [stage["status"] for stage in meta["stages"][:4]] == [
        "complete", "complete", "complete", "pending",
    ]
    assert master.read_bytes() == b"original-master"
