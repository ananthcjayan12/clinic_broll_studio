from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.io import sha256_file, write_json
from ..core.paths import run_paths
from .common import ffprobe, run_ffmpeg


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    uploads = sorted(paths.source.glob("upload.*"))
    if not uploads:
        raise RuntimeError("Uploaded source video is missing")
    original = uploads[0]
    master = paths.source / "master.mp4"
    proxy = paths.source / "proxy.mp4"
    audio = paths.source / "speech.wav"
    source_probe = ffprobe(original)
    source_video = next((item for item in source_probe.get("streams", []) if item.get("codec_type") == "video"), {})
    if not source_video:
        raise RuntimeError("Uploaded file has no video stream")
    if not any(item.get("codec_type") == "audio" for item in source_probe.get("streams", [])):
        raise RuntimeError("Uploaded talking-head video has no audio stream")
    transfer = str(source_video.get("color_transfer") or "").lower()
    primaries = str(source_video.get("color_primaries") or "").lower()
    hdr = transfer in {"smpte2084", "arib-std-b67"} or primaries == "bt2020"
    if hdr:
        # Convert iPhone/HLG/PQ footage to a predictable SDR BT.709 master so
        # browser preview and final render do not appear washed out. Homebrew
        # FFmpeg includes zscale and tonemap; a missing filter fails loudly.
        video_filter = (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv,"
            "scale='min(1920,iw)':-2:flags=lanczos,fps=30,format=yuv420p"
        )
    else:
        video_filter = "scale='min(1920,iw)':-2:flags=lanczos,fps=30,format=yuv420p"

    run_ffmpeg(
        [
            "-i", str(original),
            "-map_metadata", "-1",
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(master),
        ]
    )
    run_ffmpeg(
        [
            "-i", str(master),
            "-vf", "scale=540:-2:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(proxy),
        ]
    )
    run_ffmpeg(["-i", str(master), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])
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
