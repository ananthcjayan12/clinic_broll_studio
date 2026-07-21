from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .io import now_iso, read_json, write_json


def record_usage(run_root: Path, ledger_name: str, entry: dict[str, Any]) -> None:
    """Append one provider operation to a run-local, inspectable usage ledger."""
    path = run_root / "costs" / ledger_name
    payload = read_json(path, {"version": "1.0", "records": []}) or {"version": "1.0", "records": []}
    record = {
        "id": str(uuid.uuid4()),
        "recorded_at": now_iso(),
        **entry,
    }
    payload.setdefault("records", []).append(record)
    write_json(path, payload)


def usage_summary(run_root: Path) -> dict[str, Any]:
    ledgers = {}
    total = 0
    failures = 0
    for name in ("model_usage.json", "media_usage.json", "asr_usage.json"):
        payload = read_json(run_root / "costs" / name, {"records": []}) or {"records": []}
        records = payload.get("records") or []
        ledgers[name.removesuffix(".json")] = len(records)
        total += len(records)
        failures += sum(1 for record in records if record.get("status") == "failed")
    return {"total_operations": total, "failed_operations": failures, "ledgers": ledgers}


class UsageTimer:
    def __init__(self) -> None:
        self.started = time.monotonic()

    def seconds(self) -> float:
        return round(time.monotonic() - self.started, 3)
