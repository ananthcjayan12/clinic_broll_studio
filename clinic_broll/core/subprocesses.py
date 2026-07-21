from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable


class CommandError(RuntimeError):
    pass


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=merged,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown command failure")[-6000:]
        raise CommandError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result


def executable_available(command: str) -> bool:
    from shutil import which

    return bool(which(command) or Path(command).exists())
