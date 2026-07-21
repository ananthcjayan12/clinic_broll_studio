# Coding-agent guide

- Preserve immutable source timing.
- Do not move paid generation before human gates.
- Never overwrite a generated asset; add a version.
- Keep provider adapters behind the shared interface.
- Keep HyperFrames compositions free of remote assets.
- Run `python -m pytest` after changes.
- Run `python -m compileall -q clinic_broll` before committing.
- Run `npx hyperframes lint <composition> --json` and a short `npx hyperframes render -c <composition>/index.html -o smoke.mp4` for composition changes.
- The Studio is localhost-only by design.
