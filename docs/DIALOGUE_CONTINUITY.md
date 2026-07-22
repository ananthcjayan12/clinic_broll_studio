# Dialogue cleanup and continuity pipeline

This branch adds a non-destructive dialogue editor before visual planning.

## Pipeline stages

1. Inputs and normalization
2. Malayalam transcription
3. Dialogue cleanup analysis — routed through the selected Grok, Codex, Claude Code, or Kimi model and paused for review
4. Clean master and continuity map
5. Local video analysis
6. B-roll planning
7. Optional foreground matte
8. Still generation
9. Still preview
10. Motion generation
11. Motion preview
12. Final render
13. Final QA

## Cleanup modes

- `off`: no removals; a canonical clean master is still produced.
- `conservative`: obvious duplicate words and unusually long pauses only.
- `balanced`: recommended default for clinic reels.
- `tight`: shorter pauses and more aggressive repetition candidates, still protected by review.

The model proposes edit decisions only. Local FFmpeg code executes the approved cuts with short audio/video continuity fades. The uploaded source, normalized source master, source transcript, and source-to-clean mapping remain preserved.

## Review rules

Edits containing negations, warnings, recommendations, treatment conditions, quantities, or other clinically sensitive language are always marked for manual review. The Studio provides actions to approve the recommendation, keep the original, remove, shorten a pause, edit timing, or reset a decision.

## Existing runs

Existing v1.1 runs are migrated to the 13-stage map. Stages after transcription are reset to pending so dialogue cleanup can be reviewed before downstream visual timing is regenerated. The previous artifacts remain on disk until the operator rewinds or reruns them.

## Serving the Studio

Use the supported command so the dialogue review API is registered:

```bash
clinic-broll serve
```
