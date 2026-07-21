from pathlib import Path

from clinic_broll.pipeline.final_render import (
    _chunk_windows,
    _composite_command,
    _concat_command,
    _next_final_candidate,
    _next_overlay_dir,
    _publish_final,
    _write_concat_manifest,
)


def test_overlay_outputs_are_versioned(tmp_path):
    assert _next_overlay_dir(tmp_path).name == "final-overlay-v001"
    (tmp_path / "final-overlay-v001").mkdir()
    assert _next_overlay_dir(tmp_path).name == "final-overlay-v002"


def test_chunk_windows_are_frame_aligned_and_do_not_split_slots():
    slots = [
        {"start": 6.5, "duration": 5.5},
        {"start": 14.72, "duration": 2.94},
        {"start": 18.1, "duration": 5.4},
        {"start": 24.14, "duration": 4.62},
        {"start": 36.76, "duration": 6.74},
    ]
    windows = _chunk_windows(49.48, 30, slots)
    assert windows[0] == (0.0, 6.5)
    assert windows[-1][1] == 49.48
    assert all((end - start) <= 10.0 for start, end in windows)
    assert all(round(start * 30) == start * 30 for start, _ in windows)
    for boundary in [end for _, end in windows[:-1]]:
        assert not any(slot["start"] < boundary < slot["start"] + slot["duration"] for slot in slots)


def test_overlay_chunks_use_lossless_concat_manifest(tmp_path):
    chunks = [tmp_path / "part-001.webm", tmp_path / "part-002.webm"]
    manifest = tmp_path / "parts.ffconcat"
    _write_concat_manifest(manifest, chunks)
    assert manifest.read_text(encoding="utf-8") == (
        "ffconcat version 1.0\nfile 'part-001.webm'\nfile 'part-002.webm'\n"
    )
    command = _concat_command(manifest, tmp_path / "overlay.webm")
    assert command[command.index("-c") + 1] == "copy"


def test_final_composite_keeps_master_audio_and_uses_alpha_overlay():
    command = _composite_command(Path("master.mp4"), Path("overlay.webm"), Path("final.mp4"))
    assert "[0:v:0][1:v:0]overlay=0:0:format=auto:eof_action=pass[v]" in command
    assert command[command.index("-map") + 1] == "[v]"
    assert "0:a?" in command
    overlay_input = command.index("overlay.webm")
    assert command[overlay_input - 3:overlay_input] == ["-c:v", "libvpx-vp9", "-i"]
    assert command[-1] == "final.mp4"


def test_existing_final_is_only_archived_when_candidate_is_published(tmp_path):
    canonical = tmp_path / "final.mp4"
    canonical.write_bytes(b"old")
    candidate = _next_final_candidate(tmp_path)
    candidate.write_bytes(b"new")
    archive = _publish_final(candidate, canonical)
    assert archive == tmp_path / "final-v001.mp4"
    assert archive.read_bytes() == b"old"
    assert canonical.read_bytes() == b"new"
    assert not candidate.exists()
