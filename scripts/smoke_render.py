#!/usr/bin/env python3
"""Create and render a synthetic layered composition without calling providers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from clinic_broll.core.io import write_json
from clinic_broll.core.paths import run_paths
from clinic_broll.core.state import create_run
from clinic_broll.rendering.hyperframes import render, validate

RUN_ID = "local-render-smoke"


def main() -> None:
    paths = run_paths(RUN_ID)
    if paths.root.exists():
        shutil.rmtree(paths.root)
    create_run(
        RUN_ID,
        original_filename="synthetic.mp4",
        settings={"width": 540, "height": 960, "fps": 30, "aspect_ratio": "9:16", "matting_provider": "none"},
    )
    paths.source.mkdir(parents=True, exist_ok=True)
    source = paths.source / "master.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    shutil.copy2(source, paths.source / "proxy.mp4")
    shutil.copy2(source, paths.source / "upload.mp4")
    write_json(paths.source / "metadata.json", {"duration_seconds": 3.0, "fps": 30, "width": 540, "height": 960})
    write_json(paths.transcript / "transcript.json", {
        "duration_seconds": 3.0,
        "mode": "codemix",
        "phrases": [{"id": "phrase_0001", "start": 0.0, "end": 3.0, "text": "Malayalam smoke preview"}],
    })

    still = paths.assets / "stills" / "broll_001" / "v01" / "still.png"
    still.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (540, 960), (14, 46, 86))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 520, 495, 900), radius=28, fill=(28, 105, 150))
    draw.text((115, 680), "LAYERED B-ROLL", fill="white")
    image.save(still)
    write_json(paths.plan / "broll_plan.json", {
        "version": "1.0", "fps": 30, "duration_seconds": 3.0,
        "slots": [{
            "slot_id": "broll_001", "start": 0.7, "end": 2.7, "duration": 2.0,
            "layout_template": "bottom_board", "panel_region": 0.47,
            "keep_subject_foreground": False, "text_overlay": "",
            "priority": "useful", "status": "still_approved",
            "selected_still": still.relative_to(paths.root).as_posix(), "selected_motion": None,
            "versions": {"stills": [{"version": "v01", "path": still.relative_to(paths.root).as_posix()}], "motion": []},
        }],
    })
    lint = validate(RUN_ID, "final")
    output = paths.renders / "smoke.mp4"
    result = render(RUN_ID, "final", output)
    print({"lint": lint, "render": result})


if __name__ == "__main__":
    main()
