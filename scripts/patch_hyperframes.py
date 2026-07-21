#!/usr/bin/env python3
"""Apply the Clinic B-roll alpha-capture fix to pinned HyperFrames 0.7.62.

HyperFrames' forced parallel streaming route always interleaves tasks. That is
appropriate for drawElement, but for screenshot capture it makes every browser
decode the entire alpha timeline. Contiguous ranges bound each browser's RGBA
working set while the existing reorder buffer still writes frames in order.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "node_modules" / "hyperframes" / "package.json"
CLI = ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js"
SUPPORTED_VERSION = "0.7.62"

REPLACEMENTS = (
    (
        "      const tasks = deParallelStream ? distributeFramesInterleaved(totalFrames, workerCount, workDir) : distributeFrames(totalFrames, workerCount, workDir);",
        """      // Screenshot streaming must keep each worker on a bounded contiguous
      // range. Interleaving is only useful for drawElement and makes every
      // browser traverse the complete alpha-video timeline, multiplying its
      // decoded RGBA working set by workerCount.
      const useInterleavedTasks = deParallelStream && captureCfg.useDrawElement;
      const tasks = useInterleavedTasks ? distributeFramesInterleaved(totalFrames, workerCount, workDir) : distributeFrames(totalFrames, workerCount, workDir);""",
    ),
    (
        "`[Render] Parallel drawElement capture stalled: no frame progress for ${stallTimeoutMs}ms (stuck at ${lastCapturedFrames}/${totalFrames}).`",
        "`[Render] Parallel capture stalled: no frame progress for ${stallTimeoutMs}ms (stuck at ${lastCapturedFrames}/${totalFrames}).`",
    ),
    (
        "`[Render] Parallel drawElement capture stalled after ${stallTimeoutMs}ms with no frame progress (last frame ${lastCapturedFrames}/${totalFrames}); falling back to screenshot.`",
        "`[Render] Parallel capture stalled after ${stallTimeoutMs}ms with no frame progress (last frame ${lastCapturedFrames}/${totalFrames}).`",
    ),
    (
        "`[Render] Parallel ${captureParallelStream} capture will stream to the encoder (interleaved, ${workerCount} workers) instead of the disk path. Set HF_CAPTURE_PARALLEL_STREAM=false to disable.`",
        "`[Render] Parallel ${captureParallelStream} capture will stream to the encoder (partitioned, ${workerCount} workers) instead of the disk path. Set HF_CAPTURE_PARALLEL_STREAM=false to disable.`",
    ),
    (
        """      const stallController = new AbortController();
      const forwardParentAbort = () => stallController.abort();""",
        """      const stallController = new AbortController();
      let rejectStall;
      const stallPromise = new Promise((_, reject) => {
        rejectStall = reject;
      });
      const forwardParentAbort = () => stallController.abort();""",
    ),
    (
        """          );
          reorderBuffer.abort(stallErr);
          stallController.abort();""",
        """          );
          rejectStall(stallErr);
          reorderBuffer.abort(stallErr);
          stallController.abort();""",
    ),
    (
        "        workerResults = await executeParallelCapture(",
        "        workerResults = await Promise.race([executeParallelCapture(",
    ),
    (
        """          deParallelStream ? { ...captureCfg, enableBrowserPool: false } : captureCfg
        );""",
        """          deParallelStream ? { ...captureCfg, enableBrowserPool: false } : captureCfg
        ), stallPromise]);""",
    ),
)


def main() -> None:
    if not PACKAGE.exists() or not CLI.exists():
        raise SystemExit("HyperFrames is not installed; run npm install again")
    version = json.loads(PACKAGE.read_text(encoding="utf-8"))["version"]
    if version != SUPPORTED_VERSION:
        raise SystemExit(
            f"HyperFrames patch supports {SUPPORTED_VERSION}, found {version}; review the upstream capture implementation"
        )
    source = CLI.read_text(encoding="utf-8")
    changed = False
    for original, patched in REPLACEMENTS:
        if patched in source:
            continue
        if original not in source:
            raise SystemExit("HyperFrames patch target changed; refusing an unverified runtime modification")
        source = source.replace(original, patched, 1)
        changed = True
    if changed:
        CLI.write_text(source, encoding="utf-8")
        print("Applied HyperFrames partitioned screenshot-streaming patch")
    else:
        print("HyperFrames partitioned screenshot-streaming patch already applied")


if __name__ == "__main__":
    main()
