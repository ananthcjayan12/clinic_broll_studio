from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable

import requests

from ..core.io import sha256_file, write_json
from ..core.usage import UsageTimer, record_usage

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}


def _grok_binary() -> str:
    explicit = os.getenv("CBS_GROK_BIN", "").strip()
    binary = explicit or shutil.which("grok")
    if not binary:
        raise RuntimeError("Grok Build CLI was not found")
    return binary


def _media_files(roots: Iterable[Path], extensions: set[str], since: float = 0.0) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
            for path in iterator:
                try:
                    if path.is_file() and path.suffix.lower() in extensions and path.stat().st_mtime >= since:
                        found[str(path.resolve())] = path
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            continue
    return found


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _extract_candidates(output: str, extensions: set[str]) -> list[str]:
    candidates: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            for value in _strings(parsed):
                if any(ext in value.lower() for ext in extensions):
                    candidates.append(value)
        except json.JSONDecodeError:
            candidates.extend(re.findall(r"(?:https?://\S+|(?:/|~)[^\s\"']+\.(?:png|jpe?g|webp|mp4|mov|webm|m4v))", line, flags=re.I))
    return candidates


def _materialize_candidate(candidate: str, destination: Path) -> bool:
    cleaned = candidate.strip("'\"),.;")
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        try:
            with urllib.request.urlopen(cleaned, timeout=120) as response:
                destination.write_bytes(response.read())
            return True
        except Exception:
            return False
    path = Path(cleaned).expanduser()
    if path.exists() and path.is_file():
        shutil.copy2(path, destination)
        return True
    return False


def generate_with_grok_cli(*, prompt: str, destination: Path, media_type: str, cwd: Path, reference: Path | None = None) -> dict[str, Any]:
    """Invoke Grok Build's current Imagine tools in a headless agent turn.

    The CLI currently documents /imagine and /imagine-video in the TUI. In
    headless mode, this worker leaves built-in tools enabled and asks the agent
    to call the matching media tool. It captures returned paths/URLs and also
    detects newly-created media under the run and Grok home directories.
    """
    extensions = IMAGE_EXTENSIONS if media_type == "image" else VIDEO_EXTENSIONS
    destination.parent.mkdir(parents=True, exist_ok=True)
    binary = _grok_binary()
    start = time.time() - 1
    scan_roots = [cwd, Path.home() / ".grok"]
    before = _media_files(scan_roots, extensions)
    reference_rule = f"\nUse this exact approved reference image as the image-to-video source: {reference.resolve()}" if reference else ""
    tool_rule = (
        "Use the built-in Imagine image-generation tool exactly once." if media_type == "image" else
        "Use the built-in Imagine image-to-video tool exactly once."
    )
    full_prompt = (
        f"{tool_rule}\n"
        "Do not edit code and do not create substitute placeholder media. "
        "Wait for the media task to finish, then return the exact local output path or output URL."
        f"{reference_rule}\n\nCREATIVE BRIEF\n{prompt}"
    )
    command = [
        binary,
        "--no-auto-update",
        "-p",
        full_prompt,
        "--output-format",
        "streaming-json",
        "--cwd",
        str(cwd),
        "--always-approve",
        "--sandbox",
        "workspace",
        "--max-turns",
        "24",
        "--no-plan",
        "--no-subagents",
        "--no-memory",
        "--disable-web-search",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=int(os.getenv("CBS_GROK_MEDIA_TIMEOUT_SECONDS", "3600")),
    )
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    (destination.parent / "grok-cli-output.jsonl").write_text(combined, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Grok media generation failed: {combined[-4000:]}")

    for candidate in _extract_candidates(combined, extensions):
        if _materialize_candidate(candidate, destination):
            return _record(destination, "grok_cli", media_type, prompt, reference, candidate)

    after = _media_files(scan_roots, extensions, since=start)
    new_paths = [path for key, path in after.items() if key not in before]
    if new_paths:
        newest = max(new_paths, key=lambda path: path.stat().st_mtime)
        shutil.copy2(newest, destination)
        return _record(destination, "grok_cli", media_type, prompt, reference, str(newest))

    raise RuntimeError(
        "Grok completed but no generated media path or URL was found. Run `grok` interactively once and confirm "
        "that Imagine tools are enabled for this account/CLI version, then retry. The raw CLI output was preserved."
    )


def generate_with_xai_api(*, prompt: str, destination: Path, media_type: str, reference: Path | None = None, duration: int = 5, aspect_ratio: str = "9:16") -> dict[str, Any]:
    key = os.getenv("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY is required for xAI API media generation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {key}"}
    if media_type == "image":
        response = requests.post(
            "https://api.x.ai/v1/images/generations",
            headers={**headers, "Content-Type": "application/json"},
            json={"model": os.getenv("CBS_XAI_IMAGE_MODEL", "grok-imagine-image"), "prompt": prompt, "aspect_ratio": aspect_ratio},
            timeout=300,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"xAI image generation failed: {response.text[-3000:]}")
        data = response.json()
        items = data.get("data") or data.get("images") or []
        url = (items[0] if items else {}).get("url") if isinstance(items[0] if items else {}, dict) else None
        if not url:
            raise RuntimeError(f"xAI image response had no URL: {data}")
        urllib.request.urlretrieve(url, destination)
        return _record(destination, "xai_api", media_type, prompt, reference, url)

    payload: dict[str, Any] = {
        "model": os.getenv("CBS_XAI_VIDEO_MODEL", "grok-imagine-video"),
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": "720p",
    }
    if reference:
        # URL/file upload integration differs by account. The SDK route is preferred
        # when installed; this transparent error prevents silently ignoring the still.
        raise RuntimeError("xAI API image-to-video reference upload is not configured in this starter; use grok_cli or extend Files API integration")
    response = requests.post("https://api.x.ai/v1/videos/generations", headers={**headers, "Content-Type": "application/json"}, json=payload, timeout=300)
    if response.status_code >= 400:
        raise RuntimeError(f"xAI video generation failed: {response.text[-3000:]}")
    request_id = response.json().get("request_id")
    if not request_id:
        raise RuntimeError("xAI video response had no request_id")
    deadline = time.time() + 1800
    data: dict[str, Any] = {}
    while time.time() < deadline:
        check = requests.get(f"https://api.x.ai/v1/videos/{request_id}", headers=headers, timeout=60)
        if check.status_code >= 400:
            raise RuntimeError(f"xAI video polling failed: {check.text[-3000:]}")
        data = check.json()
        if data.get("status") == "done":
            break
        time.sleep(5)
    url = (data.get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"xAI video did not finish: {data}")
    urllib.request.urlretrieve(url, destination)
    return _record(destination, "xai_api", media_type, prompt, reference, url)


def generate_media(*, provider: str, prompt: str, destination: Path, media_type: str, cwd: Path, reference: Path | None = None, duration: int = 5, aspect_ratio: str = "9:16") -> dict[str, Any]:
    timer = UsageTimer()
    try:
        if provider == "grok_cli":
            result = generate_with_grok_cli(prompt=prompt, destination=destination, media_type=media_type, cwd=cwd, reference=reference)
        elif provider == "xai_api":
            result = generate_with_xai_api(prompt=prompt, destination=destination, media_type=media_type, reference=reference, duration=duration, aspect_ratio=aspect_ratio)
        else:
            raise RuntimeError(f"Unsupported media provider: {provider}")
    except Exception as exc:
        record_usage(cwd, "media_usage.json", {
            "provider": provider, "media_type": media_type, "status": "failed",
            "duration_seconds": timer.seconds(), "error": str(exc), "destination": str(destination),
        })
        raise
    record_usage(cwd, "media_usage.json", {
        "provider": provider, "media_type": media_type, "status": "complete",
        "duration_seconds": timer.seconds(), "destination": str(destination),
        "generated_seconds": duration if media_type == "video" else None,
    })
    return result


def _record(destination: Path, provider: str, media_type: str, prompt: str, reference: Path | None, source: str) -> dict[str, Any]:
    record = {
        "provider": provider,
        "media_type": media_type,
        "path": str(destination),
        "sha256": sha256_file(destination),
        "source": source,
        "reference": str(reference) if reference else None,
        "prompt": prompt,
    }
    write_json(destination.parent / "generation.json", record)
    return record
