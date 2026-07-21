from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import ModelSelection
from ..core.usage import UsageTimer, record_usage
from .base import TextProvider
from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .grok_cli import GrokCLIProvider
from .kimi_api import KimiAPIProvider

_PROVIDERS: dict[str, TextProvider] = {
    "grok_cli": GrokCLIProvider(),
    "codex_cli": CodexCLIProvider(),
    "claude_cli": ClaudeCLIProvider(),
    "kimi_api": KimiAPIProvider(),
}


def provider(name: str) -> TextProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported provider: {name}") from exc


def call_task_json(
    *,
    task: str,
    selection: dict[str, str],
    system: str,
    user: str,
    cwd: Path,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    engine = provider(selection["provider"])
    timer = UsageTimer()
    try:
        result = engine.call_json(
            task=task,
            model=selection["model"],
            reasoning_effort=selection.get("reasoning_effort", "high"),
            system=system,
            user=user,
            cwd=cwd,
            output_schema=output_schema,
        )
    except Exception as exc:
        record_usage(cwd, "model_usage.json", {
            "task": task, "provider": selection["provider"], "model": selection["model"],
            "reasoning_effort": selection.get("reasoning_effort", "high"),
            "status": "failed", "duration_seconds": timer.seconds(), "error": str(exc),
        })
        raise
    record_usage(cwd, "model_usage.json", {
        "task": task, "provider": selection["provider"], "model": selection["model"],
        "reasoning_effort": selection.get("reasoning_effort", "high"),
        "status": "complete", "duration_seconds": timer.seconds(),
        "note": "CLI subscription token/credit balance is not exposed when applicable.",
    })
    return result


def call_task_text(
    *,
    task: str,
    selection: dict[str, str],
    system: str,
    user: str,
    cwd: Path,
) -> str:
    timer = UsageTimer()
    try:
        result = provider(selection["provider"]).call_text(
            task=task,
            model=selection["model"],
            reasoning_effort=selection.get("reasoning_effort", "high"),
            system=system,
            user=user,
            cwd=cwd,
        )
    except Exception as exc:
        record_usage(cwd, "model_usage.json", {
            "task": task, "provider": selection["provider"], "model": selection["model"],
            "reasoning_effort": selection.get("reasoning_effort", "high"),
            "status": "failed", "duration_seconds": timer.seconds(), "error": str(exc),
        })
        raise
    record_usage(cwd, "model_usage.json", {
        "task": task, "provider": selection["provider"], "model": selection["model"],
        "reasoning_effort": selection.get("reasoning_effort", "high"),
        "status": "complete", "duration_seconds": timer.seconds(),
    })
    return result


def availability() -> dict[str, dict[str, Any]]:
    result = {}
    for name, engine in _PROVIDERS.items():
        ok, detail = engine.available()
        result[name] = {"available": ok, "detail": detail}
    return result
