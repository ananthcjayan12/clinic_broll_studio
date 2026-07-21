from pathlib import Path

from clinic_broll.core import paths as paths_module
from clinic_broll.core.state import create_run, mark_stage, rewind_run
from clinic_broll.core.io import read_json, write_json


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
    for number in range(1, 8):
        mark_stage("demo-v01", number, "complete")

    rewind_run("demo-v01", 6)

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
