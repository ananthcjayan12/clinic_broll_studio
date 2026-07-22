# Clinic B-roll Studio V2

A controlled local production system that turns Malayalam dental-clinic talking-head recordings into professionally paced vertical or horizontal social videos.

V2 is not only a B-roll generator. It combines:

- non-destructive dialogue cleanup;
- an LLM Editorial Director and editable scene graph;
- natural, colourful visual generation with a production-wide Visual Bible;
- modern top/bottom and side split layouts;
- picture-in-picture, full B-roll, floating visual and optional matte-foreground modes;
- face-aware reframing, digital zooms, transitions and emphasis presets;
- automated sound selection from the local `ai_sound_effects_library`;
- complete-edit preview and human approval gates;
- deterministic HyperFrames and FFmpeg rendering;
- technical, visual, edit, audio and clinical QA.

The uploaded source is immutable. Every dialogue cut, scene decision, generated asset, sound cue and rewind remains reviewable and recoverable.

## Why HyperFrames

HyperFrames remains the renderer because the project and reference `vcj` architecture are based on deterministic HTML/CSS/video layers rather than React components. The same inspectable composition is used for local preview and render.

Remotion remains a valid alternative for a React-standardized team, but introducing it here would add a second authoring model without improving the core layered, split-screen or source-reframing requirements.

## V2 pipeline

```text
1. Inputs and normalization
2. Malayalam transcription
3. Dialogue cleanup analysis                    → HUMAN REVIEW
4. Continuous clean master and timing map
5. Face, gesture, crop and matte-risk analysis
6. Editorial Director and Visual Bible          → HUMAN REVIEW
7. Face-aware reframe and optional matte preparation
8. Generate natural visual candidates
9. Visual candidate preview                     → HUMAN REVIEW
10. Generate approved motion assets             (optional)
11. Edit choreography, zooms and transitions
12. Sound Director and local SFX mix             (optional)
13. Complete edit preview                       → HUMAN REVIEW
14. Final render
15. Visual, edit, audio, technical and clinical QA
```

The operator may skip visual generation/preview, motion generation or sound design when the reel should remain simpler.

## Model routing

Every reasoning task can be assigned independently to:

| Provider | Authentication | Typical model |
|---|---|---|
| Grok CLI | SuperGrok / Grok Build login | `grok-4.5` |
| Codex CLI | ChatGPT/Codex login | authenticated default or configured GPT model |
| Claude Code CLI | Claude subscription or Console login | `sonnet`, `opus`, or a full model ID |
| Kimi API | Moonshot API key | `kimi-k2.6` |

V2 routed tasks include:

- dialogue editing;
- Editorial Director;
- Visual Bible direction;
- individual scene refinement;
- image direction;
- visual candidate review;
- motion direction;
- Edit Choreographer;
- Sound Director;
- final edit review;
- clinical QA;
- repair advice.

ElevenLabs Scribe v2 remains the dedicated Malayalam/code-mixed transcription provider. Grok CLI remains the production media generator.

## Dialogue Editor

Stage 3 detects candidates for:

- filler words;
- repeated words and adjacent duplicate sentences;
- abandoned starts and retakes;
- unnecessarily long pauses;
- obvious restart sections.

An LLM classifies the candidates, but local code performs the cuts. Negations, warnings, treatment recommendations and uncertain clinical edits require explicit review.

Stage 4 creates:

```text
dialogue/edit-plan.json
dialogue/source-to-clean-map.json
dialogue/continuity-plan.json
source/master.mp4
source/proxy.mp4
source/speech.wav
transcript/transcript.json
```

The clean master uses short audio/video crossfades. No new speech is synthesized.

## Editorial scene graph

The Editorial Director assigns each scene:

```text
composition_mode
layout_variant
subject_mode
visual_style
keep_eye_contact
camera_move
transition_in / transition_out
emphasis_preset
caption_mode
sound_intent
```

Supported composition modes:

- talking head;
- B-roll top / speaker bottom;
- speaker top / B-roll bottom;
- left and right side splits;
- picture-in-picture;
- floating visual;
- full-screen B-roll;
- optional layered foreground.

The foreground matte is one optional mode. It is automatically replaced with a crop-based split layout when local motion, hand activity or edge-risk analysis makes the matte unsafe.

## Natural and colourful visuals

A run-level `editorial/visual-bible.json` defines:

- palette;
- contrast and saturation;
- lighting and depth;
- camera language;
- material texture;
- medical constraints;
- visual patterns to avoid.

The default direction favours warm natural light, vivid but realistic colour, authentic Kerala/Indian context where appropriate, tactile materials, natural tooth proportions and interesting composition.

The system explicitly avoids:

- generic stock-photo smiles;
- sterile blue 3D backgrounds;
- waxy anatomy;
- overly perfect plastic teeth;
- fake written text;
- repetitive centred compositions;
- excessive teal glow.

Ordinary scenes may receive two candidates; essential scenes may receive three. A routed reviewer scores naturalness, colour, visual interest, composition, medical accuracy, motion potential and artificial appearance. Human medical review remains mandatory.

## Face-aware reframing

Stage 5 samples the clean master and records:

- face box and centre;
- eye line;
- gaze direction;
- available negative space;
- hand activity;
- safe crop;
- split-layout suitability;
- foreground-matte risk.

HyperFrames interpolates source framing inside top/bottom, side split and picture-in-picture layouts. Digital zooms remain anchored to the face rather than the centre of the frame.

## Edit choreography

The LLM chooses only from deterministic presets.

Camera presets:

```text
static
subtle_punch_in
emphasis_punch
slow_push
micro_pull_back
face_follow
object_follow
```

Transition presets:

```text
direct_cut
soft_crossfade
clean_push
vertical_slide
horizontal_swipe
mask_reveal
paper_reveal
zoom_match
blur_transition
dip_to_white
```

Emphasis presets:

```text
none
keyword_pop
card_snap
underline_draw
number_count
icon_bounce
warning_pulse
comparison_flip
```

The choreographer uses B-roll or split layouts to conceal visible dialogue cuts and avoids zooming every sentence.

## Local sound-effects pipeline

Clone the CC0 sound library beside this repository:

```bash
git clone https://github.com/ananthcjayan12/ai_sound_effects_library.git
```

Or set:

```dotenv
SFX_LIBRARY_ROOT=/absolute/path/to/ai_sound_effects_library
```

Stage 12:

1. validates `manifest.json` and referenced WAV files;
2. derives sound intents from scenes and choreography;
3. creates a small local semantic shortlist for each intent;
4. asks the routed Sound Director to select only from those candidates;
5. validates every selected ID and path;
6. mixes delays, gain, fades and a limiter under the clean Malayalam narration.

The UI allows each cue to be moved, muted or gain-adjusted. Sound is not placed on every cut, subtitle or zoom.

## Installation on macOS

Requirements:

- Python 3.11+
- Node.js 22+
- FFmpeg 6+
- Grok Build CLI for media generation
- at least one supported reasoning provider
- ElevenLabs API key

```bash
brew install python@3.11 node ffmpeg
cd clinic_broll_studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[matting]"
npm install
cp .env.example .env
```

Configure `.env`:

```dotenv
ELEVENLABS_API_KEY=...
MOONSHOT_API_KEY=...              # only when Kimi is used
SFX_LIBRARY_ROOT=../ai_sound_effects_library
```

Authenticate the CLI providers you plan to use:

```bash
grok login
codex login
claude
```

Run diagnostics:

```bash
clinic-broll doctor
npm run doctor
npm run smoke:render
```

Start the Studio:

```bash
clinic-broll serve
```

Open `http://127.0.0.1:8765`.

## Studio workflow

1. Create a production and choose editing profile, intensity, visual style, allowed layouts, foreground treatment, caption mode and sound density.
2. Run toward QA. The pipeline pauses at dialogue review.
3. Resolve every proposed dialogue edit and create the clean master.
4. Review the Editorial Director scene graph. Change scene timing, composition, subject treatment, visual style, camera move, transitions and sound intent as needed.
5. Approve visual scenes or keep the original talking head.
6. Generate visual candidates and approve the strongest medically accurate candidate.
7. Generate motion only for scenes that benefit from it; still-only scenes are supported.
8. Review the complete edit with layouts, zooms, transitions, captions and sound.
9. Approve the complete preview, render the final MP4 and run QA.
10. Download the final video from the header.

## Rewinding and regeneration

**Rewind from selected** moves downstream artifacts to:

```text
runs/<run-id>/.history/rewind-from-<stage>-<timestamp>/
```

No artifact is silently deleted.

Each scene supports targeted:

- timing and layout edits;
- AI refinement;
- still regeneration;
- motion regeneration;
- talking-head fallback;
- caption control;
- sound-intent editing.

## Run directory

```text
runs/<run-id>/
  studio_run.json
  studio.log
  source/
  transcript/
  dialogue/
    edit-plan.json
    source-to-clean-map.json
    continuity-plan.json
  analysis/
    visual-analysis.json
    reframe-analysis.json
    contact-sheet.jpg
  editorial/
    editorial-plan.json
    visual-bible.json
    reframe-plan.json
    edit-choreography.json
  plan/
    broll_plan.json
  matte/
  assets/
    stills/<scene>/<version>/
    motion/<scene>/<version>/
  sound/
    library-validation.json
    sound-plan.json
    final-audio.wav
  compositions/
    still/
    complete/
    final/
  previews/
    still-preview.mp4
    complete-preview.mp4
  renders/final.mp4
  qa/
  .history/
```

`plan/broll_plan.json` is retained as a compatibility artifact for existing generation and review code. The source of editorial intent is `editorial/editorial-plan.json`.

## CLI

```bash
clinic-broll serve
clinic-broll doctor
clinic-broll step clinic-run-v02 6 --confirm-paid
clinic-broll through clinic-run-v02 15 --confirm-paid
clinic-broll rewind clinic-run-v02 6
```

## Safeguards

- Never regenerate or alter the doctor’s face.
- Original speaker pixels are used for talking-head, split and picture-in-picture modes.
- Generated media must not be presented as real patient evidence.
- Avoid fake radiographs, scans, treatment documents and outcomes.
- Generated anatomy requires clinical review.
- Malayalam captions are optional and disabled by default.
- Full B-roll, transitions, zooms and SFX are checked for overuse.
- The Studio should remain bound to `127.0.0.1` because it can start provider-backed operations.

## Validation

```bash
python -m pytest
python -m compileall -q clinic_broll scripts
node --check clinic_broll/studio/static/app.js
node --check clinic_broll/studio/static/dialogue-v2.js
npx hyperframes --version
```

External provider calls and a complete production render require the operator’s credentials, Grok media access and real source video. Review ASR accuracy, generated dental anatomy, matte quality, final sound balance and clinical claims before publishing.
