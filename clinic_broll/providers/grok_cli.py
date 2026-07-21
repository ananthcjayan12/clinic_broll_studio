from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import TextProvider


class GrokCLIProvider(TextProvider):
    def _binary(self) -> str:
        explicit = os.getenv("CBS_GROK_BIN", "").strip()
        binary = explicit or shutil.which("grok")
        if not binary:
            raise RuntimeError("Grok Build CLI was not found. Install/login to Grok Build or set CBS_GROK_BIN.")
        return binary

    def _models(self) -> tuple[str, tuple[str, ...]]:
        binary = self._binary()
        result = subprocess.run([binary, "models"], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Grok CLI is not authenticated: {(result.stderr or result.stdout)[-2000:]}")
        text = result.stdout + result.stderr
        default_match = re.search(r"^Default model:\s*(\S+)", text, flags=re.MULTILINE)
        models = tuple(re.findall(r"^\s*\*\s+(\S+?)(?:\s+\(default\))?\s*$", text, flags=re.MULTILINE))
        default = default_match.group(1) if default_match else (models[0] if models else "grok-build")
        return default, models

    def available(self) -> tuple[bool, str]:
        try:
            default, models = self._models()
            return True, f"default={default}; models={', '.join(models)}"
        except Exception as exc:
            return False, str(exc)

    def call_text(self, *, task: str, model: str, reasoning_effort: str, system: str, user: str, cwd: Path, output_schema: dict[str, Any] | None = None) -> str:
        binary = self._binary()
        default, available = self._models()
        resolved = default if model in {"", "authenticated-default", "default"} else model
        if available and resolved not in available:
            # Saved model aliases may outlive a CLI release; use the authenticated default.
            resolved = default
        response_rule = "Return only the requested result, with no Markdown fence or commentary."
        if output_schema:
            response_rule += " Return one JSON object conforming to this schema:\n" + json.dumps(output_schema, ensure_ascii=False)
        prompt = f"{response_rule}\n\nSYSTEM INSTRUCTIONS\n{system}\n\nUSER REQUEST\n{user}"
        command = [
            binary,
            "--no-auto-update",
            "--verbatim",
            "-p",
            prompt,
            "--system-prompt-override",
            "You are a bounded analysis worker. Do not edit files. Start immediately and return only the requested final output.",
            "--tools",
            "read_file,list_dir,grep",
            "--output-format",
            "plain",
            "--cwd",
            str(cwd),
            "--model",
            resolved,
            "--effort",
            reasoning_effort,
            "--sandbox",
            "read-only",
            "--permission-mode",
            "dontAsk",
            "--max-turns",
            "16",
            "--no-plan",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=int(os.getenv("CBS_MODEL_TIMEOUT_SECONDS", "2400")))
        if result.returncode != 0:
            raise RuntimeError(f"Grok CLI failed: {(result.stderr or result.stdout)[-4000:]}")
        response = result.stdout.strip()
        if not response:
            raise RuntimeError("Grok CLI returned no response")
        return response
