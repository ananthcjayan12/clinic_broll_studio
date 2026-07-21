from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from jsonschema import validate


class TextProvider(ABC):
    @abstractmethod
    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def call_text(
        self,
        *,
        task: str,
        model: str,
        reasoning_effort: str,
        system: str,
        user: str,
        cwd: Path,
        output_schema: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError

    def call_json(self, **kwargs: Any) -> dict[str, Any]:
        schema = kwargs.get("output_schema")
        response = self.call_text(**kwargs)
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            start, end = response.find("{"), response.rfind("}")
            if start < 0 or end < start:
                raise RuntimeError("Model response did not contain a JSON object")
            payload = json.loads(response[start : end + 1])
        if schema:
            validate(instance=payload, schema=schema)
        return payload
