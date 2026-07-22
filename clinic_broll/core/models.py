from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "grok_cli": {
        "label": "Grok CLI (SuperGrok)",
        "models": ["authenticated-default", "grok-4.5"],
        "efforts": ["low", "medium", "high"],
    },
    "codex_cli": {
        "label": "Codex CLI (ChatGPT)",
        "models": ["authenticated-default", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
        "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
    },
    "claude_cli": {
        "label": "Claude Code CLI",
        "models": ["sonnet", "opus", "haiku", "claude-sonnet-5", "claude-opus-4-8"],
        "efforts": ["low", "medium", "high"],
    },
    "kimi_api": {
        "label": "Kimi API",
        "models": ["kimi-k2.6"],
        "efforts": ["low", "medium", "high"],
    },
}

TASK_CATALOG: dict[str, dict[str, Any]] = {
    "dialogue_editor": {"label": "Dialogue cleanup and continuity", "stage": 3},
    "editorial_director": {"label": "Editorial director and scene graph", "stage": 6},
    "visual_bible_director": {"label": "Visual continuity bible", "stage": 6},
    "slot_refinement": {"label": "Scene refinement", "stage": 6},
    "image_prompt": {"label": "Natural and colourful visual direction", "stage": 8},
    "visual_candidate_reviewer": {"label": "Visual candidate reviewer", "stage": 8},
    "motion_prompt": {"label": "Image-to-video motion direction", "stage": 10},
    "edit_choreographer": {"label": "Edit choreography and camera moves", "stage": 11},
    "sound_director": {"label": "Sound-effects director", "stage": 12},
    "final_edit_reviewer": {"label": "Final visual, edit, and audio review", "stage": 15},
    "semantic_qa": {"label": "Semantic and clinical QA", "stage": 15},
    "repair_advisor": {"label": "Targeted repair advisor", "stage": 15},
}

DEFAULT_MODEL_MAP: dict[str, dict[str, str]] = {
    task: {"provider": "grok_cli", "model": "grok-4.5", "reasoning_effort": "high"}
    for task in TASK_CATALOG
}


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str
    reasoning_effort: str = "high"


def validate_model_map(value: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    source = value or DEFAULT_MODEL_MAP
    result: dict[str, dict[str, str]] = {}
    for task in TASK_CATALOG:
        raw = source.get(task) or DEFAULT_MODEL_MAP[task]
        provider = str(raw.get("provider") or "").strip()
        model = str(raw.get("model") or "").strip()
        effort = str(raw.get("reasoning_effort") or "high").strip().lower()
        if provider not in PROVIDER_CATALOG:
            raise ValueError(f"Unsupported provider for {task}: {provider}")
        if not model:
            raise ValueError(f"Model is required for {task}")
        if effort not in PROVIDER_CATALOG[provider]["efforts"]:
            raise ValueError(f"Unsupported effort for {provider}: {effort}")
        result[task] = {"provider": provider, "model": model, "reasoning_effort": effort}
    return result
