from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import RUNS_ROOT, RUN_ID_PATTERN

_RUN_RE = re.compile(RUN_ID_PATTERN)


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def meta(self) -> Path:
        return self.root / "studio_run.json"

    @property
    def log(self) -> Path:
        return self.root / "studio.log"

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript"

    @property
    def dialogue(self) -> Path:
        return self.root / "dialogue"

    @property
    def analysis(self) -> Path:
        return self.root / "analysis"

    @property
    def editorial(self) -> Path:
        return self.root / "editorial"

    @property
    def plan(self) -> Path:
        return self.root / "plan"

    @property
    def matte(self) -> Path:
        return self.root / "matte"

    @property
    def sound(self) -> Path:
        return self.root / "sound"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def responses(self) -> Path:
        return self.root / "responses"

    @property
    def compositions(self) -> Path:
        return self.root / "compositions"

    @property
    def previews(self) -> Path:
        return self.root / "previews"

    @property
    def renders(self) -> Path:
        return self.root / "renders"

    @property
    def qa(self) -> Path:
        return self.root / "qa"

    @property
    def costs(self) -> Path:
        return self.root / "costs"

    @property
    def history(self) -> Path:
        return self.root / ".history"


def require_run_id(run_id: str) -> str:
    if not _RUN_RE.fullmatch(run_id):
        raise ValueError("Run ID must contain lowercase letters, numbers, dots, underscores, or hyphens")
    return run_id


def run_paths(run_id: str) -> RunPaths:
    return RunPaths(RUNS_ROOT / require_run_id(run_id))
