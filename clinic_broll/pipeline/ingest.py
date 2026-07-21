from __future__ import annotations

import subprocess
from typing import Any

from ..core.config import FFMPEG
from ..core.io import sha256_file, write_json
from ..core.paths import run_paths
from ..core.state import append_log, load_run
from .common import ffprobe, run_ffmpeg


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    meta = load_run(run_id)
    uploads = sorted(paths.source.glob("upload.*"))
    if not uploads:
        raise RuntimeError("Uploaded source video is missing")
    original = uploads[0]
    master = paths.source / "master.mp4"
    proxy = paths.source / "proxy.mp4"
    audio = paths.source / "speech.wav"
    source_probe = ffprobe(original)
    source_duration = float((source_probe.get("format") or {}).get("duration") or 0)
    source_video = next((item for item in source_probe.get("streams", []) if item.get("codec_type") == "video"), {})
    if not source_video:
        raise RuntimeError("Uploaded file has no video stream")
    if not any(item.get("codec_type") == "audio" for item in source_probe.get("streams", [])):
        raise RuntimeError("Uploaded talking-head video has no audio stream")
    transfer = str(source_video.get("color_transfer") or "").lower()
    primaries = str(source_video.get("color_primaries") or "").lower()
    hdr = transfer in {"smpte2084", "arib-std-b67"} or primaries == "bt2020"
    target_width = int(meta["settings"].get("width") or 1080)
    target_height = int(meta["settings"].get("height") or 1920)
    scale_filter = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p"
    )
    if hdr:
        # Convert iPhone/HLG/PQ footage to a predictable SDR BT.709 master so
        # browser preview and final render do not appear washed out. Homebrew
        # FFmpeg includes zscale and tonemap; a missing filter fails loudly.
        video_filter = (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv,"
            + scale_filter
        )
    else:
        video_filter = scale_filter

    hardware_encode = _has_videotoolbox()
    if hardware_encode:
        video_encoder = [
            "-c:v", "h264_videotoolbox", "-realtime", "1", "-allow_sw", "1",
            "-profile:v", "high", "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M",
        ]
        append_log(paths, f"Normalize master video: using Apple VideoToolbox at {target_width}x{target_height}")
    else:
        video_encoder = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
        append_log(paths, f"Normalize master video: using software fallback at {target_width}x{target_height}")

    run_ffmpeg(
        [
            "-i", str(original),
            "-map_metadata", "-1",
            "-vf", video_filter,
            *video_encoder,
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(master),
        ],
        paths=paths,
        label="Normalize master video",
        duration=source_duration,
    )
    run_ffmpeg(
        [
            "-i", str(master),
            "-vf", "scale=540:-2:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(proxy),
        ],
        paths=paths,
        label="Create browser proxy",
        duration=source_duration,
    )
    run_ffmpeg(
        ["-i", str(master), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
        paths=paths,
        label="Extract transcription audio",
        duration=source_duration,
    )
    probe = ffprobe(master)
    video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
    metadata = {
        "original_filename": original.name,
        "original_sha256": sha256_file(original),
        "master_sha256": sha256_file(master),
        "duration_seconds": float((probe.get("format") or {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": 30,
        "source_probe": source_probe,
        "master_probe": probe,
        "source_hdr": hdr,
        "source_color_transfer": transfer or None,
        "source_color_primaries": primaries or None,
    }
    write_json(paths.source / "metadata.json", metadata)
    return {"artifacts": ["source/master.mp4", "source/proxy.mp4", "source/speech.wav", "source/metadata.json"], "metadata": metadata}


def _has_videotoolbox() -> bool:
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "h264_videotoolbox" in result.stdout
