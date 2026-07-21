# Architecture

## Design invariants

1. **Source timing is immutable.** Generated visuals can be replaced, but they cannot retime the doctor’s speech.
2. **Paid work is downstream of human approval.** Planning, still preview, and motion preview are explicit review boundaries.
3. **Every generated artifact is versioned.** No media file is overwritten.
4. **Every model task is independently routed.** Provider selection is stored inside the run.
5. **Local code owns composition.** An LLM never directly assembles the final source audio or master timing.
6. **Rewinds preserve history.** Downstream folders are moved, not deleted.
7. **The foreground matte is optional.** The editor still works without it.

## Components

### FastAPI Studio

`clinic_broll/studio/server.py`

- upload handling;
- run listing and details;
- subprocess lifecycle;
- model-map editing;
- slot editing/approval;
- rewind, skip, stop and regeneration actions;
- local artifact serving.

### Worker and orchestrator

`clinic_broll/worker.py` and `clinic_broll/pipeline/orchestrator.py`

Long operations run in a separate process. The Studio stays responsive and can terminate the worker.

### Provider adapters

`clinic_broll/providers/`

All text providers implement the same `TextProvider` interface. Schema validation is local.

### Sarvam adapter

`providers/sarvam.py`

The complete 16 kHz mono source audio is submitted through Sarvam Batch STT using Saaras V3, `ml-IN`, and `codemix`. The short synchronous REST route remains an opt-in test path for clips no longer than 30 seconds.

### Grok media adapter

`providers/grok_media.py`

Media generation is isolated from text reasoning. The adapter captures returned URLs/paths and new local files, then normalizes media in downstream stages.

### Layered composition

`rendering/composition.py`

The composition is deterministic HTML:

- source video;
- timed panels;
- generated media;
- subject alpha video;
- timed Malayalam captions.

The `hf-seek` event controls panel opacity, movement, caption state, and preview synchronization.

### HyperFrames renderer

`rendering/hyperframes.py`

Each composition is rebuilt and passed through the public HyperFrames `lint --json` contract before MP4 generation. The render itself then performs browser/media extraction and frame-coverage checks.

## Model routing

The routing map is run-scoped rather than globally mutable. Changing a task provider affects future executions only; existing responses and assets remain attributable to the model that produced them.

## Invalidation graph

```text
Source → Transcript → Analysis → Plan → Matte → Stills → Still preview
                                               ↓
                                            Motion → Motion preview → Final → QA
```

A single slot regeneration bypasses this graph and replaces only that slot’s versioned asset.
