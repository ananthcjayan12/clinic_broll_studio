from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..core.config import FFMPEG
from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run


SELFIE_SEGMENTER_MODEL = Path(__file__).resolve().parents[1] / "models" / "selfie_segmenter.tflite"


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
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    append_log(paths, f"Foreground matte: segmenting {total_frames} frames with MediaPipe")
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
        legacy_segmenter = False
        try:
            segmenter, legacy_segmenter = _create_segmenter(mp)
            api_name = "Solutions" if legacy_segmenter else "Tasks ImageSegmenter"
            append_log(paths, f"Foreground matte: initialized MediaPipe {api_name}")
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mask = _segment_frame(mp, segmenter, legacy_segmenter, rgb, frame_index, fps)
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
                if frame_index % 150 == 0 or frame_index == total_frames:
                    percent = (frame_index / total_frames * 100) if total_frames else 0
                    append_log(paths, f"Foreground matte: {frame_index}/{total_frames or '?'} frames · {percent:.1f}%")
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
    append_log(paths, f"Foreground matte: transparent video complete ({frame_index} frames)")
    return {"artifacts": ["matte/foreground.webm", "matte/report.json"], "summary": report}


def _create_segmenter(mp):
    """Support both legacy MediaPipe Solutions and current Tasks-only wheels."""
    if hasattr(mp, "solutions"):
        return mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1), True
    if not SELFIE_SEGMENTER_MODEL.exists():
        raise RuntimeError(f"MediaPipe selfie segmentation model is missing: {SELFIE_SEGMENTER_MODEL}")
    options = mp.tasks.vision.ImageSegmenterOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(SELFIE_SEGMENTER_MODEL)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        output_confidence_masks=True,
        output_category_mask=False,
    )
    return mp.tasks.vision.ImageSegmenter.create_from_options(options), False


def _segment_frame(mp, segmenter, legacy: bool, rgb: np.ndarray, frame_index: int, fps: float) -> np.ndarray:
    if legacy:
        return segmenter.process(rgb).segmentation_mask.astype(np.float32)
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    timestamp_ms = round(frame_index * 1000 / fps)
    result = segmenter.segment_for_video(image, timestamp_ms)
    if not result.confidence_masks:
        raise RuntimeError("MediaPipe returned no foreground confidence mask")
    mask = np.array(result.confidence_masks[0].numpy_view(), dtype=np.float32, copy=True)
    return mask[:, :, 0] if mask.ndim == 3 and mask.shape[2] == 1 else mask
