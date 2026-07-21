from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..core.io import read_json, write_json
from ..core.paths import run_paths
from ..core.state import append_log


def _face_detector():
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    return detector if not detector.empty() else None


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    video_path = paths.source / "master.mp4"
    metadata = read_json(paths.source / "metadata.json", {})
    duration = float(metadata.get("duration_seconds") or 0)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Could not open master video for visual analysis")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_interval = max(1, round(fps * 1.5))
    expected_samples = max(1, math.ceil(frame_count / sample_interval))
    append_log(paths, f"Visual analysis: scanning {frame_count} frames at 1.5 second intervals (~{expected_samples} samples)")
    keyframes_dir = paths.analysis / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    detector = _face_detector()
    previous_small: np.ndarray | None = None
    samples: list[dict[str, Any]] = []
    contact_images: list[tuple[Image.Image, str]] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % sample_interval:
            index += 1
            continue
        timestamp = index / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        motion = float(np.mean(cv2.absdiff(small, previous_small)) / 255.0) if previous_small is not None else 0.0
        previous_small = small
        faces = []
        if detector is not None:
            faces = detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(70, 70))
        if len(faces):
            largest = max(faces, key=lambda item: item[2] * item[3])
            x, y, w, h = [int(value) for value in largest]
            face_area = (w * h) / max(1, frame.shape[0] * frame.shape[1])
            face_center_y = (y + h / 2) / frame.shape[0]
        else:
            x = y = w = h = 0
            face_area = 0.0
            face_center_y = 0.4
        # A large, centrally visible face makes full-frame B-roll less desirable,
        # but is ideal for layered panels behind the subject matte.
        talking_head_value = min(1.0, 0.35 + face_area * 5.5 + max(0.0, 0.08 - motion) * 2.5)
        layer_suitability = min(1.0, 0.55 + face_area * 3.0 - motion * 1.8)
        name = f"frame-{len(samples)+1:04d}-{timestamp:08.3f}.jpg"
        destination = keyframes_dir / name
        cv2.imwrite(str(destination), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        sample = {
            "id": f"visual_{len(samples)+1:04d}",
            "time": round(timestamp, 3),
            "motion_score": round(motion, 4),
            "face_area_ratio": round(face_area, 4),
            "face_center_y": round(face_center_y, 4),
            "talking_head_value": round(talking_head_value, 4),
            "layer_suitability": round(layer_suitability, 4),
            "keyframe": f"analysis/keyframes/{name}",
        }
        samples.append(sample)
        if len(samples) % 5 == 0 or len(samples) == expected_samples:
            append_log(paths, f"Visual analysis: processed {len(samples)}/{expected_samples} samples")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        thumb = Image.fromarray(rgb).resize((270, 480))
        contact_images.append((thumb, f"{timestamp:05.1f}s  layer {layer_suitability:.2f}"))
        index += 1
    capture.release()

    windows = []
    for sample_index, sample in enumerate(samples):
        start = max(0.0, float(sample["time"]) - 0.75)
        end = min(duration, float(sample["time"]) + 0.75)
        windows.append({
            "id": f"window_{sample_index+1:04d}",
            "start": round(start, 3),
            "end": round(end, 3),
            "talking_head_value": sample["talking_head_value"],
            "layer_suitability": sample["layer_suitability"],
            "motion_score": sample["motion_score"],
            "keyframe": sample["keyframe"],
        })
    report = {
        "version": "1.0",
        "duration_seconds": duration,
        "sample_interval_seconds": 1.5,
        "samples": samples,
        "windows": windows,
        "recommendation": "Prefer layered panels when layer_suitability is high; avoid full-frame cover when talking_head_value is high.",
    }
    write_json(paths.analysis / "visual-analysis.json", report)
    append_log(paths, "Visual analysis: building contact sheet and saving timing windows")
    _contact_sheet(contact_images, paths.analysis / "contact-sheet.jpg")
    return {"artifacts": ["analysis/visual-analysis.json", "analysis/contact-sheet.jpg", "analysis/keyframes/"], "summary": {"samples": len(samples), "duration_seconds": duration}}


def _contact_sheet(items: list[tuple[Image.Image, str]], destination: Path) -> None:
    if not items:
        return
    columns = 4
    card_w, card_h = 300, 540
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * card_w, rows * card_h + 90), (9, 20, 34))
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 25), "CLINIC B-ROLL · VISUAL ANALYSIS CONTACT SHEET", fill=(235, 244, 255))
    for index, (image, label) in enumerate(items):
        col, row = index % columns, index // columns
        x, y = col * card_w + 15, row * card_h + 80
        sheet.paste(image, (x, y))
        draw.text((x, y + 492), label, fill=(174, 199, 220))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=88)
