from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..core.config import PROJECT_ROOT
from .composition import build


def _binary() -> list[str]:
    explicit = os.getenv("CBS_HYPERFRAMES_BIN", "").strip()
    if explicit:
        return [explicit]
    global_binary = shutil.which("hyperframes")
    if global_binary:
        return [global_binary]
    local = PROJECT_ROOT / "node_modules" / ".bin" / "hyperframes"
    if local.exists():
        return [str(local)]
    npx = shutil.which("npx")
    if npx:
        return [npx, "hyperframes"]
    raise RuntimeError("HyperFrames is not installed. Run `npm install` and `npm run doctor`.")


def render(run_id: str, mode: str, output: Path) -> dict[str, Any]:
    composition = build(run_id, mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    # HyperFrames resolves local media paths from its working directory. Run
    # inside the generated composition so ``assets/...`` is deterministic and
    # does not leak run-specific media into a shared repository-level folder.
    command = [*_binary(), "render", "-c", composition.name, "-o", str(output.resolve())]
    result = subprocess.run(command, cwd=composition.parent, capture_output=True, text=True, timeout=14400)
    log = output.with_suffix(output.suffix + ".render.log")
    log.write_text((result.stdout or "") + "\n" + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"HyperFrames render failed: {(result.stderr or result.stdout)[-5000:]}")
    if not output.exists():
        raise RuntimeError("HyperFrames completed without producing an MP4")
    return {"composition": str(composition), "output": str(output), "log": str(log)}


def validate(run_id: str, mode: str) -> dict[str, Any]:
    """Run the public HyperFrames composition linter before final rendering.

    HyperFrames 0.7.x exposes ``lint`` rather than a separate ``validate`` CLI
    command. Keeping this wrapper local prevents a CLI-version mismatch from
    breaking production after all paid media has already been approved.
    """
    composition = build(run_id, mode)
    command = [*_binary(), "lint", ".", "--json"]
    result = subprocess.run(command, cwd=composition.parent, capture_output=True, text=True, timeout=600)
    report = {
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }
    if result.returncode != 0:
        raise RuntimeError(f"HyperFrames lint failed: {(result.stderr or result.stdout)[-4000:]}")
    return {"lint": report}
