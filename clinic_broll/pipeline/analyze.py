from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

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
    sample_interval = max(1, round(fps * 1.0))
    expected_samples = max(1, math.ceil(frame_count / sample_interval))
    append_log(paths, f"V2 visual analysis: scanning {frame_count} frames at one-second intervals (~{expected_samples} samples)")
    keyframes_dir = paths.analysis / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    detector = _face_detector()
    previous_small: np.ndarray | None = None
    previous_lower: np.ndarray | None = None
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
        frame_h, frame_w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        motion = float(np.mean(cv2.absdiff(small, previous_small)) / 255.0) if previous_small is not None else 0.0
        previous_small = small
        lower = cv2.resize(gray[int(frame_h * 0.48):, :], (160, 48))
        hand_activity = float(np.mean(cv2.absdiff(lower, previous_lower)) / 255.0) if previous_lower is not None else 0.0
        previous_lower = lower
        faces = []
        if detector is not None:
            faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(64, 64))
        if len(faces):
            x, y, w, h = [int(value) for value in max(faces, key=lambda item: item[2] * item[3])]
            face_area = (w * h) / max(1, frame_h * frame_w)
            center_x = (x + w / 2) / frame_w
            center_y = (y + h / 2) / frame_h
            face_box = [x / frame_w, y / frame_h, (x + w) / frame_w, (y + h) / frame_h]
        else:
            x = y = w = h = 0
            face_area = 0.0
            center_x, center_y = 0.5, 0.35
            face_box = [0.32, 0.12, 0.68, 0.5]
        eye_line_y = max(0.08, min(0.55, face_box[1] + (face_box[3] - face_box[1]) * 0.38))
        gaze = "left" if center_x < 0.44 else ("right" if center_x > 0.56 else "center")
        negative_space = "right" if center_x < 0.46 else ("left" if center_x > 0.54 else "balanced")
        safe_crop = _safe_crop(center_x, center_y, face_box, hand_activity)
        talking_head_value = min(1.0, 0.35 + face_area * 5.5 + max(0.0, 0.08 - motion) * 2.5)
        layer_suitability = min(1.0, 0.55 + face_area * 3.0 - motion * 1.8 - hand_activity * 0.8)
        split_suitability = min(1.0, 0.5 + face_area * 2.2 - hand_activity * 0.45)
        matte_risk = min(1.0, motion * 2.2 + hand_activity * 2.5 + (0.12 if face_area < 0.03 else 0.0))
        name = f"frame-{len(samples)+1:04d}-{timestamp:08.3f}.jpg"
        destination = keyframes_dir / name
        cv2.imwrite(str(destination), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        sample = {
            "id": f"visual_{len(samples)+1:04d}",
            "time": round(timestamp, 3),
            "motion_score": round(motion, 4),
            "hand_activity": round(hand_activity, 4),
            "face_area_ratio": round(face_area, 4),
            "face_box": [round(value, 4) for value in face_box],
            "face_center_x": round(center_x, 4),
            "face_center_y": round(center_y, 4),
            "eye_line_y": round(eye_line_y, 4),
            "gaze_direction": gaze,
            "negative_space": negative_space,
            "safe_crop": safe_crop,
            "talking_head_value": round(talking_head_value, 4),
            "layer_suitability": round(layer_suitability, 4),
            "split_suitability": round(split_suitability, 4),
            "matte_risk": round(matte_risk, 4),
            "keyframe": f"analysis/keyframes/{name}",
        }
        samples.append(sample)
        if len(samples) % 5 == 0 or len(samples) == expected_samples:
            append_log(paths, f"V2 visual analysis: processed {len(samples)}/{expected_samples} samples")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        thumb = Image.fromarray(rgb).resize((270, 480))
        contact_images.append((thumb, f"{timestamp:05.1f}s split {split_suitability:.2f} matte-risk {matte_risk:.2f}"))
        index += 1
    capture.release()

    windows = []
    for sample_index, sample in enumerate(samples):
        start = max(0.0, float(sample["time"]) - 0.5)
        end = min(duration, float(sample["time"]) + 0.5)
        windows.append({
            "id": f"window_{sample_index+1:04d}",
            "start": round(start, 3),
            "end": round(end, 3),
            "talking_head_value": sample["talking_head_value"],
            "layer_suitability": sample["layer_suitability"],
            "split_suitability": sample["split_suitability"],
            "matte_risk": sample["matte_risk"],
            "motion_score": sample["motion_score"],
            "hand_activity": sample["hand_activity"],
            "face_box": sample["face_box"],
            "eye_line_y": sample["eye_line_y"],
            "gaze_direction": sample["gaze_direction"],
            "negative_space": sample["negative_space"],
            "safe_crop": sample["safe_crop"],
            "keyframe": sample["keyframe"],
        })
    report = {
        "version": "2.0",
        "duration_seconds": duration,
        "sample_interval_seconds": 1.0,
        "samples": samples,
        "windows": windows,
        "reframe_summary": _reframe_summary(samples),
        "recommendation": "Prefer face-aware top/bottom or side splits. Use matte foreground only in low matte-risk windows; use full B-roll or picture-in-picture to bridge hard dialogue cuts.",
    }
    write_json(paths.analysis / "visual-analysis.json", report)
    write_json(paths.analysis / "reframe-analysis.json", {"version": "2.0", "samples": samples, "summary": report["reframe_summary"]})
    append_log(paths, "V2 visual analysis: building contact sheet and reframe map")
    _contact_sheet(contact_images, paths.analysis / "contact-sheet.jpg")
    return {
        "artifacts": ["analysis/visual-analysis.json", "analysis/reframe-analysis.json", "analysis/contact-sheet.jpg", "analysis/keyframes/"],
        "summary": {"samples": len(samples), "duration_seconds": duration, "reframe": report["reframe_summary"]},
    }


def _safe_crop(center_x: float, center_y: float, face_box: list[float], hand_activity: float) -> dict[str, float]:
    width = 0.68 if hand_activity < 0.08 else 0.78
    height = 0.78 if hand_activity < 0.08 else 0.9
    x = max(0.0, min(1.0 - width, center_x - width / 2))
    face_top = face_box[1]
    y = max(0.0, min(1.0 - height, face_top - 0.08))
    return {"x": round(x, 4), "y": round(y, 4), "width": round(width, 4), "height": round(height, 4)}


def _reframe_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"dominant_gaze": "center", "preferred_broll_side": "right", "average_matte_risk": 1.0}
    gazes = [str(item["gaze_direction"]) for item in samples]
    dominant = max(set(gazes), key=gazes.count)
    average_risk = sum(float(item["matte_risk"]) for item in samples) / len(samples)
    average_hand = sum(float(item["hand_activity"]) for item in samples) / len(samples)
    preferred = "right" if dominant == "left" else ("left" if dominant == "right" else "right")
    return {
        "dominant_gaze": dominant,
        "preferred_broll_side": preferred,
        "average_matte_risk": round(average_risk, 4),
        "average_hand_activity": round(average_hand, 4),
        "matte_recommended": average_risk < 0.28,
    }


def _contact_sheet(items: list[tuple[Image.Image, str]], destination: Path) -> None:
    if not items:
        return
    columns = 4
    card_w, card_h = 300, 540
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * card_w, rows * card_h + 90), (9, 20, 34))
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 25), "CLINIC B-ROLL V2 · FACE / GESTURE / REFRAME CONTACT SHEET", fill=(235, 244, 255))
    for index, (image, label) in enumerate(items):
        col, row = index % columns, index // columns
        x, y = col * card_w + 15, row * card_h + 80
        sheet.paste(image, (x, y))
        draw.text((x, y + 492), label, fill=(174, 199, 220))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=88)
