from __future__ import annotations

import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from pathlib import Path
from typing import Any, Iterator

from ..core.config import PROJECT_ROOT
from ..core.paths import run_paths
from ..core.state import append_log
from .composition import build


def _binary() -> list[str]:
    explicit = os.getenv("CBS_HYPERFRAMES_BIN", "").strip()
    if explicit:
        return [explicit]
    global_binary = shutil.which("hyperframes")
    if global_binary:
        return [global_binary]
    local = PROJECT_ROOT / "node_modules" / ".bin" / "hyperframes"
    if local.exists():
        return [str(local)]
    npx = shutil.which("npx")
    if npx:
        return [npx, "hyperframes"]
    raise RuntimeError("HyperFrames is not installed. Run `npm install` and `npm run doctor`.")


def render(
    run_id: str,
    mode: str,
    output: Path,
    *,
    window: tuple[float, float] | None = None,
    composition_name: str | None = None,
) -> dict[str, Any]:
    with _render_lock(output):
        composition = build(run_id, mode, window=window, composition_name=composition_name)
        output.parent.mkdir(parents=True, exist_ok=True)
        # HyperFrames resolves local media paths from its working directory. Run
        # inside the generated composition so ``assets/...`` is deterministic and
        # does not leak run-specific media into a shared repository-level folder.
        base_command = [*_binary(), "render", "-c", composition.name, "-o", str(output.resolve())]
        if output.suffix.lower() == ".webm":
            base_command.extend(["--format", "webm"])
        worker_args = _capture_worker_args(composition, output)
        render_env = os.environ.copy()
        if worker_args:
            # HyperFrames expands alpha video into full-resolution RGBA PNG data
            # URIs. Its <=8 GB auto profile pins capture to one Chrome process,
            # which exhausts that process's decoded-image cache on long mattes.
            # The pinned dependency patch gives each worker a bounded contiguous
            # range and streams frames directly to FFmpeg without disk staging.
            # HyperFrames evaluates the parallel-stream eligibility gate before it
            # automatically disables its default drawElement experiment for
            # multi-worker renders. Opt out explicitly so the gate sees the actual
            # screenshot route from the start.
            render_env["PRODUCER_EXPERIMENTAL_FAST_CAPTURE"] = "false"
            render_env["HF_CAPTURE_PARALLEL_STREAM"] = "true"
            worker_count = worker_args[worker_args.index("--workers") + 1]
            route = "streaming worker" if output.suffix.lower() == ".webm" else "partitioned capture workers"
            append_log(run_paths(run_id), f"HyperFrames: alpha composition using {worker_count} {route}")
        logs: list[Path] = []
        result: _RenderResult | None = None
        retried_coverage = False
        retried_stall = False
        for _ in range(3):
            attempt = _next_attempt(output)
            log = _attempt_log(output, attempt)
            logs.append(log)
            command = [*base_command, *worker_args]
            result = _run_streaming(run_id, command, composition.parent, log, env=render_env)
            if result.returncode == 0:
                break
            if not retried_coverage and _is_video_coverage_failure(result.output):
                retried_coverage = True
                append_log(
                    run_paths(run_id),
                    "HyperFrames: transient video-frame coverage miss; retrying once with a fresh capture session",
                )
                continue
            if not retried_stall and _is_parallel_capture_stall(result.output):
                retried_stall = True
                worker_args = _reduced_capture_worker_args(worker_args)
                retry_workers = worker_args[worker_args.index("--workers") + 1]
                append_log(
                    run_paths(run_id),
                    f"HyperFrames: capture stalled; retrying with {retry_workers} workers",
                )
                continue
            raise RuntimeError(f"HyperFrames render failed: {result.output[-5000:]}")
        assert result is not None
        if result.returncode != 0:
            raise RuntimeError(f"HyperFrames render failed: {result.output[-5000:]}")
        if not output.exists():
            raise RuntimeError(f"HyperFrames completed without producing {output.suffix or 'an output file'}")
        return {
            "composition": str(composition),
            "output": str(output),
            "log": str(logs[-1]),
            "logs": [str(item) for item in logs],
        }


def _capture_worker_args(composition: Path, output: Path | None = None) -> list[str]:
    if output is not None and output.suffix.lower() == ".webm":
        return ["--workers", "1", "--no-low-memory-mode"]
    if "foreground.webm" not in composition.read_text(encoding="utf-8"):
        return []
    # HyperFrames only routes multi-worker MP4 capture to its no-disk streaming
    # encoder. A transparent WebM with multiple workers falls back to thousands
    # of full-resolution PNGs, which needs more scratch space than a typical
    # workstation has available. One worker activates HyperFrames' native VP9
    # streaming encoder and preserves alpha without staging frames on disk.
    return ["--workers", "6", "--no-low-memory-mode"]


def _is_video_coverage_failure(output: str) -> bool:
    """Recognize HyperFrames' nondeterministic media-extraction safety abort."""
    return "captured 0 of expected" in output and "coverage 0.0%" in output


def _is_parallel_capture_stall(output: str) -> bool:
    return "Parallel capture stalled: no frame progress" in output


def _reduced_capture_worker_args(worker_args: list[str]) -> list[str]:
    if "--workers" not in worker_args:
        return worker_args
    reduced = worker_args.copy()
    value_index = reduced.index("--workers") + 1
    reduced[value_index] = str(min(3, int(reduced[value_index])))
    return reduced


@contextmanager
def _render_lock(output: Path) -> Iterator[None]:
    """Prevent two processes from exhausting memory on the same render."""
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.with_suffix(output.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            flock(handle.fileno(), LOCK_EX | LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"A HyperFrames render is already running for {output.name}") from exc
        try:
            yield
        finally:
            flock(handle.fileno(), LOCK_UN)


class _RenderResult:
    def __init__(self, returncode: int, output: str) -> None:
        self.returncode = returncode
        self.output = output


def _attempt_log(output: Path, attempt: int) -> Path:
    return output.with_suffix(output.suffix + f".render-attempt-{attempt:02d}.log")


def _next_attempt(output: Path) -> int:
    attempt = 1
    while _attempt_log(output, attempt).exists():
        attempt += 1
    return attempt


def _run_streaming(
    run_id: str,
    command: list[str],
    cwd: Path,
    log: Path,
    *,
    env: dict[str, str] | None = None,
) -> _RenderResult:
    """Stream renderer output to both its artifact log and the Studio console."""
    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    lines: list[str] = []
    started = time.monotonic()
    try:
        with log.open("w", encoding="utf-8") as handle:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                lines.append(line)
                handle.write(raw_line)
                handle.flush()
                if line:
                    append_log(run_paths(run_id), f"HyperFrames: {line}")
                if time.monotonic() - started > 14400:
                    process.kill()
                    raise subprocess.TimeoutExpired(command, 14400)
        return _RenderResult(process.wait(), "\n".join(lines))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def validate(run_id: str, mode: str) -> dict[str, Any]:
    """Run the public HyperFrames composition linter before final rendering.

    HyperFrames 0.7.x exposes ``lint`` rather than a separate ``validate`` CLI
    command. Keeping this wrapper local prevents a CLI-version mismatch from
    breaking production after all paid media has already been approved.
    """
    composition = build(run_id, mode)
    command = [*_binary(), "lint", ".", "--json"]
    result = subprocess.run(command, cwd=composition.parent, capture_output=True, text=True, timeout=600)
    report = {
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }
    if result.returncode != 0:
        raise RuntimeError(f"HyperFrames lint failed: {(result.stderr or result.stdout)[-4000:]}")
    return {"lint": report}
