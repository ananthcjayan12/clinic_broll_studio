from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..core.state import append_log
from ..rendering.hyperframes import render, validate


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    append_log(paths, "Final render: linting the local HyperFrames composition")
    validation = validate(run_id, "final")
    append_log(paths, "Final render: composition valid; rendering final MP4")
    output = paths.renders / "final.mp4"
    rendered = render(run_id, "final", output)
    append_log(paths, "Final render: MP4 complete")
    return {"artifacts": ["compositions/final/index.html", "renders/final.mp4"], "summary": {"validation": validation, "render": rendered}}
