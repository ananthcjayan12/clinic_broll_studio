# Implementation status

## Implemented

- Local FastAPI control-room UI.
- Permanent run IDs and versioned run directories.
- Upload, stage execution, stop, skip, safe rewind, delete, artifact viewing, and final MP4 download.
- Run-scoped model routing across Grok CLI, Codex CLI, Claude Code CLI, and Kimi K2.6.
- Sarvam-only Malayalam Batch STT with `saaras:v3`, `ml-IN`, and `codemix`.
- Local proxy/audio normalization, keyframe/contact-sheet analysis, face/coverability scoring, and immutable slot timing.
- LLM B-roll planning plus deterministic fallback and manual slot approval.
- Per-slot AI refinement through the selected routing provider.
- Grok Build headless image and image-to-video generation with raw JSONL capture and media-path discovery.
- Versioned still and motion assets with slot-level regeneration.
- MediaPipe foreground segmentation streamed directly into a transparent VP9 encoder without temporary PNG-frame storage.
- Layered HTML composition: source video, graphic panel/media, real subject foreground, Malayalam captions, and brand layer.
- HyperFrames lint/render wrapper with composition-local asset resolution.
- Still-preview, motion-preview, final-render, technical QA, semantic QA, and targeted repair advice.
- Provider usage ledger and explicit confirmation for every API/subscription-backed stage.

## Requires operator credentials or local installation

- Sarvam API key.
- Grok Build login for default media generation and optional Grok reasoning.
- At least one reasoning provider: Grok, Codex, Claude Code, or Kimi API.
- Chrome/Chromium, FFmpeg, Node.js, and HyperFrames.
- MediaPipe optional dependency when the foreground subject layer is enabled.

## Validation performed before packaging

- Python test suite: 14 tests passed.
- Python bytecode compilation and wheel packaging check.
- Browser JavaScript syntax check.
- HyperFrames 0.7.62 lint on a generated synthetic layered composition: zero errors and zero warnings.
- HyperFrames render progressed through composition compilation, source-video frame extraction, and audio processing. The hosted build environment then blocked the renderer's temporary localhost capture URL; this is an environment policy, not a composition or asset-resolution failure. Run `npm run doctor` on the target Mac before production.

## Production review still required

- Benchmark Sarvam on Dr. Pooja's actual Malayalam/code-mixed recordings.
- Review hair, shoulder, and moving-hand matte quality for the chosen camera setup.
- Run one Grok image and image-to-video test because Grok Build media-tool output paths can change between CLI releases.
- Clinically review all generated dental anatomy and never represent generated visuals as real patient evidence.
