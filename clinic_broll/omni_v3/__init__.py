"""Omni-first presenter reel workflow.

This package deliberately keeps Google Flow as a manual subscription-backed
handoff. Local code owns transcription, dialogue cleanup, segmentation, prompt
preparation, result validation, continuity and final rendering.
"""

from .director import run as run_director
from .jobs import approve_job, finalise_jobs, import_result, load_manifest, prepare_jobs

__all__ = [
    "approve_job",
    "finalise_jobs",
    "import_result",
    "load_manifest",
    "prepare_jobs",
    "run_director",
]
