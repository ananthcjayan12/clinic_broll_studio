from __future__ import annotations

from typing import Any

from ..core.paths import run_paths
from ..rendering.hyperframes import render, validate


def run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    validation = validate(run_id, "final")
    output = paths.renders / "final.mp4"
    rendered = render(run_id, "final", output)
    return {"artifacts": ["compositions/final/index.html", "renders/final.mp4"], "summary": {"validation": validation, "render": rendered}}
