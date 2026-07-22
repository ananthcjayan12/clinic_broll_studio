from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / ".env",):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(prog="clinic-broll", description="Clinic B-roll Studio")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the local production Studio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    sub.add_parser("doctor", help="Check local tools and provider authentication")

    step = sub.add_parser("step", help="Run exactly one pipeline stage")
    step.add_argument("run_id")
    step.add_argument("stage", type=int)
    step.add_argument("--force", action="store_true")
    step.add_argument("--confirm-paid", action="store_true", help="Confirm provider/API/subscription usage for paid stages")

    through = sub.add_parser("through", help="Run until a target stage or human review gate")
    through.add_argument("run_id")
    through.add_argument("target", type=int)
    through.add_argument("--force", action="store_true")
    through.add_argument("--confirm-paid", action="store_true", help="Confirm provider/API/subscription usage for paid stages")

    rewind = sub.add_parser("rewind", help="Move downstream artifacts to history and reset stages")
    rewind.add_argument("run_id")
    rewind.add_argument("from_stage", type=int)

    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        from .studio.dialogue_api import register_dialogue_routes
        from .studio.server import app

        register_dialogue_routes(app)
        uvicorn.run(app, host=args.host, port=args.port, reload=False)
    elif args.command == "doctor":
        _doctor()
    elif args.command == "step":
        from .pipeline.orchestrator import run_stage

        run_stage(args.run_id, args.stage, force=args.force, confirm_paid=args.confirm_paid)
    elif args.command == "through":
        from .pipeline.orchestrator import run_through

        run_through(args.run_id, args.target, force=args.force, confirm_paid=args.confirm_paid)
    elif args.command == "rewind":
        from .core.state import rewind_run

        rewind_run(args.run_id, args.from_stage)


def _doctor() -> None:
    from .core.config import FFMPEG, FFPROBE, PROJECT_ROOT
    from .providers.registry import availability

    tools = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which(FFMPEG) or (FFMPEG if Path(FFMPEG).exists() else None),
        "ffprobe": shutil.which(FFPROBE) or (FFPROBE if Path(FFPROBE).exists() else None),
        "node": shutil.which("node"),
        "npm": shutil.which("npm"),
        "hyperframes": (
            shutil.which("hyperframes")
            or (str(PROJECT_ROOT / "node_modules" / ".bin" / "hyperframes") if (PROJECT_ROOT / "node_modules" / ".bin" / "hyperframes").exists() else None)
        ),
        "elevenlabs_key": bool(os.getenv("ELEVENLABS_API_KEY")),
    }
    try:
        import mediapipe  # noqa: F401
        tools["mediapipe"] = True
    except ImportError:
        tools["mediapipe"] = False
    print(json.dumps({"tools": tools, "providers": availability()}, indent=2))


if __name__ == "__main__":
    main()
