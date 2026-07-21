from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import TextProvider


class ClaudeCLIProvider(TextProvider):
    def _binary(self) -> str:
        explicit = os.getenv("CBS_CLAUDE_BIN", "").strip()
        binary = explicit or shutil.which("claude")
        if not binary:
            raise RuntimeError("Claude Code CLI was not found. Install Claude Code or set CBS_CLAUDE_BIN.")
        return binary

    def available(self) -> tuple[bool, str]:
        try:
            binary = self._binary()
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
            return result.returncode == 0, (result.stdout or result.stderr).strip()
        except Exception as exc:
            return False, str(exc)

    def call_text(self, *, task: str, model: str, reasoning_effort: str, system: str, user: str, cwd: Path, output_schema: dict[str, Any] | None = None) -> str:
        del task, reasoning_effort
        binary = self._binary()
        schema_rule = ""
        if output_schema:
            schema_rule = "\nReturn one JSON object matching this JSON Schema exactly:\n" + json.dumps(output_schema, ensure_ascii=False)
        prompt = f"{user}{schema_rule}\nReturn only the final answer without Markdown fences."
        command = [
            binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--max-turns",
            "4",
            "--system-prompt",
            system,
            "--disallowedTools",
            "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch",
        ]
        if model:
            command.extend(["--model", model])
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=int(os.getenv("CBS_MODEL_TIMEOUT_SECONDS", "2400")))
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {(result.stderr or result.stdout)[-4000:]}")
        try:
            payload = json.loads(result.stdout)
            response = payload.get("result") or payload.get("message") or payload.get("content")
            if isinstance(response, list):
                response = "\n".join(str(item.get("text") or item) for item in response)
            if response:
                return str(response).strip()
        except json.JSONDecodeError:
            pass
        if not result.stdout.strip():
            raise RuntimeError("Claude CLI returned no response")
        return result.stdout.strip()
