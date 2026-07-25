# Clinic Omni Reel Studio V3

V3 is an Omni-first workflow for modern presenter-led reels. It reuses the reliable parts of Clinic B-roll Studio—ingest, word-level Malayalam transcription, reversible dialogue cleanup, clean-master timing, face/gesture analysis, final compositing, captions, sound and QA—while replacing still generation and image-to-video with a manual Google Flow handoff.

## Start

```bash
python -m pip install -e ".[matting]"
clinic-omni
```

Open `http://127.0.0.1:8766`.

## Operator workflow

1. Upload the raw 30–60 second talking-head recording.
2. Click **Transcribe & find dialogue cuts**.
3. Approve safe retakes/fillers and manually review clinical statements.
4. Click **Create clean master**.
5. Click **AI Direct** to create a small credit-bounded Omni plan.
6. Click **Prepare Flow jobs**.
7. For each job:
   - download `input.mp4`;
   - copy the prepared prompt;
   - open Google Flow and select Gemini Omni Flash;
   - request exactly one output;
   - upload the completed result back to the same card;
   - review identity, mouth movement, hands and dental anatomy;
   - approve, retry or use the original segment.
8. Finalise the Omni timeline.
9. Build the complete preview.
10. Approve and render the final reel.

## Why Flow remains manual

The manual handoff uses the consumer Google AI Pro/Flow credit entitlement. The system does not call a separately billed Gemini media API and does not automate browser clicks against an unstable consumer UI.

## File layout

```text
runs/<run-id>/
  omni/
    plan.json
    manifest.json
    jobs/
      OMNI-001/
        input.mp4
        prompt.txt
        thumbnail.jpg
        job.json
        provider-result.mp4
        normalised.mp4
```

## Continuity and audio

Each presenter-edit input includes short handles before and after the intended timeline window. Imported results are retimed to the input duration, trimmed back to the authored window, normalised to the project frame rate/resolution and stripped of generated audio. The clean master remains the continuous audio source during final rendering.

## Credit policy

Defaults:

- maximum five Omni jobs;
- 40 estimated Flow credits per uploaded-video edit;
- 280-credit total authorisation ceiling;
- one output per Flow generation;
- manual retry only for the failed job.

The local Director may suggest more creative beats, but deterministic code enforces the job and credit limits.

## Safety

- Dr Pooja’s original audio is never replaced.
- Generated captions are forbidden; exact Malayalam captions are rendered locally.
- Every Omni presenter edit requires manual identity, lip movement, hand and anatomy review.
- A rejected job can fall back to the original cleaned segment without blocking the reel.
