from pathlib import Path

import pytest

from clinic_broll.rendering.hyperframes import _attempt_log, _capture_worker_args, _next_attempt, _render_lock


def test_render_attempt_logs_are_versioned():
    output = Path("preview.mp4")
    assert _attempt_log(output, 1).name == "preview.mp4.render-attempt-01.log"
    assert _attempt_log(output, 2).name == "preview.mp4.render-attempt-02.log"


def test_next_render_attempt_does_not_overwrite_logs(tmp_path):
    output = tmp_path / "preview.mp4"
    _attempt_log(output, 1).write_text("prior attempt", encoding="utf-8")
    assert _next_attempt(output) == 2


def test_alpha_compositions_partition_capture_workers(tmp_path):
    alpha = tmp_path / "alpha.html"
    alpha.write_text('<video src="foreground.webm"></video>', encoding="utf-8")
    plain = tmp_path / "plain.html"
    plain.write_text("<main></main>", encoding="utf-8")
    assert _capture_worker_args(alpha) == ["--workers", "6", "--no-low-memory-mode"]
    assert _capture_worker_args(plain) == []


def test_render_lock_rejects_concurrent_process_for_same_output(tmp_path):
    output = tmp_path / "preview.mp4"
    with _render_lock(output):
        with pytest.raises(RuntimeError, match="already running"):
            with _render_lock(output):
                pass
