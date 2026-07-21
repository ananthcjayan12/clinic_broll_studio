from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..core.state import append_log
from ..rendering.composition import build
from ..rendering.hyperframes import render


def run_still(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Still preview: building local HyperFrames composition")
    composition = build(run_id, "still")
    output = paths.previews / "still-preview.mp4"
    result = render(run_id, "still", output)
    append_log(paths, "Still preview: render complete and ready for review")
    return {"artifacts": ["compositions/still/index.html", "previews/still-preview.mp4"], "summary": result}


def run_motion(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Motion preview: building local HyperFrames composition")
    composition = build(run_id, "motion")
    output = paths.previews / "motion-preview.mp4"
    result = render(run_id, "motion", output)
    append_log(paths, "Motion preview: render complete and ready for review")
    return {"artifacts": ["compositions/motion/index.html", "previews/motion-preview.mp4"], "summary": result}
