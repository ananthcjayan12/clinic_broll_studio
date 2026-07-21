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
DEFAULT_FEATHER_PX = 4
DEFAULT_TEMPORAL_BLEND = 0.12
DEFAULT_DECONTAMINATION_STRENGTH = 0.72


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    provider = str(meta["settings"].get("matting_provider") or "mediapipe")
    if provider == "none":
        report = {"provider": "none", "foreground_available": False, "message": "Layered foreground disabled by run setting"}
        write_json(paths.matte / "report.json", report)
        return {"artifacts": ["matte/report.json"], "summary": report}
    if provider not in {"mediapipe", "mediapipe_refined"}:
        raise RuntimeError(f"Unsupported matting provider: {provider}")
    return _mediapipe_matte(run_id)


def _mediapipe_matte(run_id: str) -> dict[str, Any]:
    try:
        import mediapipe as mp
    except ImportError as exc:
        raise RuntimeError("MediaPipe is required for layered foreground matting. Run: pip install -e '.[matting]'") from exc

    paths = run_paths(run_id)
    meta = load_run(run_id)
    source = paths.source / "master.mp4"
    metadata = read_json(paths.source / "metadata.json", {})
    fps = float(metadata.get("fps") or 30)
    feather_px = _positive_int(meta["settings"].get("matte_feather_px"), DEFAULT_FEATHER_PX, minimum=1, maximum=12)
    temporal_blend = _bounded_float(
        meta["settings"].get("matte_temporal_blend"), DEFAULT_TEMPORAL_BLEND, minimum=0.0, maximum=0.35
    )
    decontamination_strength = _bounded_float(
        meta["settings"].get("matte_decontamination_strength"),
        DEFAULT_DECONTAMINATION_STRENGTH,
        minimum=0.0,
        maximum=1.0,
    )
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("Could not open master video for matting")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    append_log(
        paths,
        f"Foreground matte: segmenting {total_frames} frames with refined MediaPipe edges "
        f"(feather={feather_px}px, temporal={temporal_blend:.2f})",
    )
    paths.matte.mkdir(parents=True, exist_ok=True)

    destination = paths.matte / "foreground.webm"
    encode_log = paths.matte / "foreground-encode.log"
    command = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}",
        "-framerate", f"{fps:g}", "-i", "pipe:0",
        "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-row-mt", "1", "-threads", "4",
        "-b:v", "0", "-crf", "16", str(destination),
    ]
    frame_index = 0
    previous_alpha: np.ndarray | None = None
    processing_error: Exception | None = None
    edge_pixel_total = 0
    opaque_pixel_total = 0
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
                raw_mask = _segment_frame(mp, segmenter, legacy_segmenter, rgb, frame_index, fps)
                alpha_float = _refine_mask(
                    raw_mask,
                    frame,
                    previous_alpha=previous_alpha,
                    feather_px=feather_px,
                    temporal_blend=temporal_blend,
                )
                previous_alpha = alpha_float
                foreground_bgr = _decontaminate_edges(
                    frame,
                    alpha_float,
                    strength=decontamination_strength,
                    radius=max(2, feather_px + 1),
                )
                alpha = np.clip(alpha_float * 255.0, 0, 255).astype(np.uint8)
                edge_pixel_total += int(np.count_nonzero((alpha > 5) & (alpha < 250)))
                opaque_pixel_total += int(np.count_nonzero(alpha >= 250))
                bgra = cv2.cvtColor(foreground_bgr, cv2.COLOR_BGR2BGRA)
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
        except Exception as exc:
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
        "provider": "mediapipe_refined",
        "foreground_available": True,
        "frames": frame_index,
        "fps": fps,
        "width": width,
        "height": height,
        "output": "matte/foreground.webm",
        "refinement": {
            "edge_only_feather_px": feather_px,
            "temporal_blend": temporal_blend,
            "edge_color_decontamination": decontamination_strength,
            "guided_filter": True,
            "global_blur": False,
        },
        "edge_pixels": edge_pixel_total,
        "opaque_pixels": opaque_pixel_total,
        "note": "Review fast hand movement and very fine flyaway hair in the layered preview before final approval.",
    }
    write_json(paths.matte / "report.json", report)
    append_log(paths, f"Foreground matte: refined transparent video complete ({frame_index} frames)")
    return {"artifacts": ["matte/foreground.webm", "matte/report.json"], "summary": report}


def _refine_mask(
    mask: np.ndarray,
    frame_bgr: np.ndarray,
    *,
    previous_alpha: np.ndarray | None,
    feather_px: int = DEFAULT_FEATHER_PX,
    temporal_blend: float = DEFAULT_TEMPORAL_BLEND,
) -> np.ndarray:
    """Convert a segmentation probability map into a stable, edge-aware alpha matte."""
    height, width = frame_bgr.shape[:2]
    probability = np.asarray(mask, dtype=np.float32)
    if probability.shape != (height, width):
        probability = cv2.resize(probability, (width, height), interpolation=cv2.INTER_LINEAR)
    probability = np.clip(probability, 0.0, 1.0)

    mask_u8 = np.clip(probability * 255.0, 0, 255).astype(np.uint8)
    cleanup_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, cleanup_kernel, iterations=1)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, cleanup_kernel, iterations=1)
    probability = mask_u8.astype(np.float32) / 255.0

    guide = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    working_scale = min(1.0, 960.0 / max(height, width))
    if working_scale < 1.0:
        working_size = (max(2, round(width * working_scale)), max(2, round(height * working_scale)))
        small_guide = cv2.resize(guide, working_size, interpolation=cv2.INTER_AREA)
        small_probability = cv2.resize(probability, working_size, interpolation=cv2.INTER_AREA)
        small_guided = _guided_filter(
            small_guide, small_probability, radius=max(2, round((feather_px + 2) * working_scale)), eps=1e-3
        )
        guided = cv2.resize(small_guided, (width, height), interpolation=cv2.INTER_LINEAR)
    else:
        guided = _guided_filter(guide, probability, radius=max(2, feather_px + 2), eps=1e-3)
    guided = np.clip((guided - 0.035) / 0.93, 0.0, 1.0)

    definite_foreground = probability >= 0.88
    definite_background = probability <= 0.10
    binary = (guided >= 0.5).astype(np.uint8)
    edge_kernel_size = feather_px * 2 + 1
    edge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (edge_kernel_size, edge_kernel_size))
    dilated = cv2.dilate(binary, edge_kernel, iterations=1)
    eroded = cv2.erode(binary, edge_kernel, iterations=1)
    edge_band = dilated != eroded

    alpha = binary.astype(np.float32)
    alpha[edge_band] = guided[edge_band]
    alpha[definite_foreground] = 1.0
    alpha[definite_background] = 0.0
    alpha = _smoothstep(alpha)

    if previous_alpha is not None and previous_alpha.shape == alpha.shape and temporal_blend > 0:
        unknown = (alpha > 0.02) & (alpha < 0.98)
        delta = np.abs(alpha - previous_alpha)
        adaptive_weight = temporal_blend * np.exp(-delta * 10.0)
        alpha[unknown] = (
            previous_alpha[unknown] * adaptive_weight[unknown]
            + alpha[unknown] * (1.0 - adaptive_weight[unknown])
        )

    alpha[definite_foreground] = 1.0
    alpha[definite_background] = 0.0
    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


def _guided_filter(guide: np.ndarray, source: np.ndarray, *, radius: int, eps: float) -> np.ndarray:
    kernel = (radius * 2 + 1, radius * 2 + 1)
    mean_guide = cv2.boxFilter(guide, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    mean_source = cv2.boxFilter(source, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    corr_guide = cv2.boxFilter(guide * guide, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    corr_cross = cv2.boxFilter(guide * source, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    variance = corr_guide - mean_guide * mean_guide
    covariance = corr_cross - mean_guide * mean_source
    a = covariance / (variance + eps)
    b = mean_source - a * mean_guide
    mean_a = cv2.boxFilter(a, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    mean_b = cv2.boxFilter(b, cv2.CV_32F, kernel, normalize=True, borderType=cv2.BORDER_REFLECT)
    return mean_a * guide + mean_b


def _decontaminate_edges(
    frame_bgr: np.ndarray,
    alpha: np.ndarray,
    *,
    strength: float = DEFAULT_DECONTAMINATION_STRENGTH,
    radius: int = 5,
) -> np.ndarray:
    """Propagate nearby opaque subject colours into semi-transparent edge pixels."""
    if strength <= 0:
        return frame_bgr
    edge = (alpha > 0.015) & (alpha < 0.985)
    if not np.any(edge):
        return frame_bgr
    ys, xs = np.nonzero(edge)
    pad = radius * 3
    y0, y1 = max(0, int(ys.min()) - pad), min(frame_bgr.shape[0], int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(frame_bgr.shape[1], int(xs.max()) + pad + 1)
    alpha_roi = alpha[y0:y1, x0:x1]
    frame_roi = frame_bgr[y0:y1, x0:x1].astype(np.float32)
    edge_roi = edge[y0:y1, x0:x1]
    known = (alpha_roi >= 0.96).astype(np.float32)
    propagated = frame_roi.copy()
    kernel_size = max(3, radius * 2 + 1)
    kernel = (kernel_size, kernel_size)
    support = known.copy()
    values = propagated * support[:, :, None]
    for _ in range(3):
        weight = cv2.boxFilter(support, cv2.CV_32F, kernel, normalize=False, borderType=cv2.BORDER_REFLECT)
        colour_sum = cv2.boxFilter(values, cv2.CV_32F, kernel, normalize=False, borderType=cv2.BORDER_REFLECT)
        available = weight > 1e-4
        estimate = colour_sum / np.maximum(weight[:, :, None], 1e-4)
        fill = (support < 0.5) & available
        propagated[fill] = estimate[fill]
        support[fill] = 1.0
        values = propagated * support[:, :, None]
    blend = np.zeros_like(alpha_roi, dtype=np.float32)
    blend[edge_roi] = np.clip((1.0 - alpha_roi[edge_roi]) * strength, 0.0, strength)
    cleaned_roi = frame_roi * (1.0 - blend[:, :, None]) + propagated * blend[:, :, None]
    result = frame_bgr.copy()
    result[y0:y1, x0:x1] = np.clip(cleaned_roi, 0, 255).astype(np.uint8)
    return result


def _smoothstep(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _positive_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


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
