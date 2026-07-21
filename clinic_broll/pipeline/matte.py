from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..core.config import FFMPEG
from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import load_run


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    provider = str(meta["settings"].get("matting_provider") or "mediapipe")
    if provider == "none":
        report = {"provider": "none", "foreground_available": False, "message": "Layered foreground disabled by run setting"}
        write_json(paths.matte / "report.json", report)
        return {"artifacts": ["matte/report.json"], "summary": report}
    if provider != "mediapipe":
        raise RuntimeError(f"Unsupported matting provider: {provider}")
    return _mediapipe_matte(run_id)


def _mediapipe_matte(run_id: str) -> dict[str, Any]:
    try:
        import mediapipe as mp
    except ImportError as exc:
        raise RuntimeError("MediaPipe is required for layered foreground matting. Run: pip install -e '.[matting]'") from exc

    paths = run_paths(run_id)
    source = paths.source / "master.mp4"
    metadata = read_json(paths.source / "metadata.json", {})
    fps = float(metadata.get("fps") or 30)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("Could not open master video for matting")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    paths.matte.mkdir(parents=True, exist_ok=True)

    destination = paths.matte / "foreground.webm"
    encode_log = paths.matte / "foreground-encode.log"
    command = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}",
        "-framerate", f"{fps:g}", "-i", "pipe:0",
        "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-row-mt", "1", "-threads", "4",
        "-b:v", "0", "-crf", "18", str(destination),
    ]
    frame_index = 0
    previous_alpha: np.ndarray | None = None
    processing_error: Exception | None = None
    with encode_log.open("wb") as log_handle:
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_handle)
        segmenter = None
        try:
            segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mask = segmenter.process(rgb).segmentation_mask.astype(np.float32)
                # Preserve hair edges while reducing temporal flicker and pinholes.
                mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=1.6)
                mask = np.clip((mask - 0.08) / 0.84, 0.0, 1.0)
                if previous_alpha is not None:
                    mask = previous_alpha * 0.30 + mask * 0.70
                previous_alpha = mask
                alpha = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
                bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
                bgra[:, :, 3] = alpha
                if encoder.stdin is None:
                    raise RuntimeError("FFmpeg matte encoder stdin is unavailable")
                encoder.stdin.write(bgra.tobytes())
                frame_index += 1
        except BrokenPipeError as exc:
            processing_error = RuntimeError("Transparent VP9 encoder stopped while receiving matte frames")
            processing_error.__cause__ = exc
        except Exception as exc:  # ensure the encoder is reaped before surfacing local processing errors
            processing_error = exc
        finally:
            capture.release()
            if segmenter is not None:
                segmenter.close()
            if encoder.stdin is not None:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
        returncode = encoder.wait(timeout=7200)
    if processing_error is not None:
        destination.unlink(missing_ok=True)
        raise processing_error
    if frame_index == 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError("No video frames were produced during matting")
    if returncode != 0 or not destination.exists():
        detail = encode_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"Transparent VP9 encoding failed: {detail}")
    report = {
        "provider": "mediapipe",
        "foreground_available": True,
        "frames": frame_index,
        "fps": fps,
        "width": width,
        "height": height,
        "output": "matte/foreground.webm",
        "note": "Review hair and moving hands in the layered preview before final approval.",
    }
    write_json(paths.matte / "report.json", report)
    return {"artifacts": ["matte/foreground.webm", "matte/report.json"], "summary": report}
