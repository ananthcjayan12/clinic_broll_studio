from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import TextProvider


class CodexCLIProvider(TextProvider):
    def _binary(self) -> str:
        explicit = os.getenv("CBS_CODEX_BIN", "").strip()
        candidates = [Path(explicit)] if explicit else []
        discovered = shutil.which("codex")
        if discovered:
            candidates.append(Path(discovered))
        candidates.extend(sorted((Path.home() / ".vscode" / "extensions").glob("openai.chatgpt-*/bin/*/codex"), reverse=True))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise RuntimeError("Codex CLI was not found. Install/login to Codex or set CBS_CODEX_BIN.")

    def available(self) -> tuple[bool, str]:
        try:
            binary = self._binary()
            result = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=15)
            detail = (result.stdout + result.stderr).strip()
            return (result.returncode == 0 and "logged in" in detail.lower(), detail or binary)
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
        def normalize(node: Any) -> Any:
            if isinstance(node, list):
                return [normalize(item) for item in node]
            if not isinstance(node, dict):
                return node
            result = {key: normalize(value) for key, value in node.items()}
            if result.get("type") == "object" or "properties" in result:
                result["additionalProperties"] = False
            return result
        return normalize(schema)

    def call_text(self, *, task: str, model: str, reasoning_effort: str, system: str, user: str, cwd: Path, output_schema: dict[str, Any] | None = None) -> str:
        binary = self._binary()
        prompt = (
            "You are a bounded production-planning worker. Do not edit files or run shell commands. "
            "Return only the requested result.\n\n"
            f"SYSTEM INSTRUCTIONS\n{system}\n\nUSER REQUEST\n{user}"
        )
        with tempfile.TemporaryDirectory(prefix="cbs-codex-") as directory:
            output = Path(directory) / "response.txt"
            command = [binary, "exec", "-", "--ephemeral", "--sandbox", "read-only", "--color", "never", "--output-last-message", str(output), "--cd", str(cwd)]
            if model and model != "authenticated-default":
                command.extend(["--model", model])
            command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
            if output_schema:
                schema_path = Path(directory) / "schema.json"
                schema_path.write_text(json.dumps(self._strict_schema(output_schema)), encoding="utf-8")
                command.extend(["--output-schema", str(schema_path)])
            result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=int(os.getenv("CBS_MODEL_TIMEOUT_SECONDS", "2400")))
            if result.returncode != 0:
                detail = (result.stderr or result.stdout)[-4000:]
                raise RuntimeError(f"Codex CLI failed: {detail}")
            if not output.exists():
                raise RuntimeError("Codex CLI returned no final response")
            return output.read_text(encoding="utf-8").strip()
