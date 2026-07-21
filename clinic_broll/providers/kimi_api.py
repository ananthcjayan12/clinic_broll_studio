from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from .base import TextProvider


class KimiAPIProvider(TextProvider):
    def available(self) -> tuple[bool, str]:
        return (bool(os.getenv("MOONSHOT_API_KEY")), "MOONSHOT_API_KEY configured" if os.getenv("MOONSHOT_API_KEY") else "MOONSHOT_API_KEY missing")

    def call_text(self, *, task: str, model: str, reasoning_effort: str, system: str, user: str, cwd: Path, output_schema: dict[str, Any] | None = None) -> str:
        del task, cwd
        key = os.getenv("MOONSHOT_API_KEY")
        if not key:
            raise RuntimeError("MOONSHOT_API_KEY is required for Kimi API")
        base = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
        schema_rule = ""
        if output_schema:
            schema_rule = "\nReturn one JSON object matching this schema:\n" + json.dumps(output_schema, ensure_ascii=False)
        resolved_model = model or os.getenv("CBS_KIMI_MODEL", "kimi-k2.6")
        effort_rule = (
            f"\nReasoning depth requested by the operator: {reasoning_effort}. "
            "Do not expose chain-of-thought; return only the requested result."
        )
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user + schema_rule + effort_rule},
            ],
            "max_tokens": 32000,
        }
        # Current K2.6/K2.7 reasoning deployments control sampling internally.
        if not resolved_model.lower().startswith(("kimi-k2.6", "kimi-k2.7")):
            payload["temperature"] = 0.2
        if output_schema:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=int(os.getenv("CBS_MODEL_TIMEOUT_SECONDS", "2400")),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Kimi API failed with HTTP {response.status_code}: {response.text[-3000:]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Kimi API returned no choices")
        text = str((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Kimi API returned no text")
        return text
