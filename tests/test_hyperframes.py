from pathlib import Path

import pytest

from clinic_broll.rendering.hyperframes import (
    _attempt_log,
    _capture_worker_args,
    _is_parallel_capture_stall,
    _is_video_coverage_failure,
    _next_attempt,
    _reduced_capture_worker_args,
    _render_lock,
)


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
    assert _capture_worker_args(alpha, tmp_path / "overlay.webm") == [
        "--workers",
        "1",
        "--no-low-memory-mode",
    ]
    assert _capture_worker_args(plain) == []


def test_only_zero_video_coverage_failures_are_retryable():
    assert _is_video_coverage_failure(
        'Video "foreground-01" captured 0 of expected 55 frames (coverage 0.0%, threshold 95.0%).'
    )
    assert not _is_video_coverage_failure("Video coverage was 92.0%")
    assert not _is_video_coverage_failure("Browser timed out")


def test_parallel_capture_stall_is_retryable_with_fewer_workers():
    assert _is_parallel_capture_stall(
        "[Render] Parallel capture stalled: no frame progress for 60000ms (stuck at 735/1485)."
    )
    assert not _is_parallel_capture_stall("Video extraction failed")
    assert _reduced_capture_worker_args(["--workers", "6", "--no-low-memory-mode"]) == [
        "--workers",
        "3",
        "--no-low-memory-mode",
    ]
    assert _reduced_capture_worker_args(["--workers", "1"]) == ["--workers", "1"]


def test_render_lock_rejects_concurrent_process_for_same_output(tmp_path):
    output = tmp_path / "preview.mp4"
    with _render_lock(output):
        with pytest.raises(RuntimeError, match="already running"):
            with _render_lock(output):
                pass
