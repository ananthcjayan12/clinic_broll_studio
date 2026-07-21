# Production polish controls

This change adds three operator-facing controls and a refined foreground pipeline.

## Refined subject matte

The MediaPipe confidence map is no longer globally blurred. The matte stage now:

1. removes small mask pinholes and speckles;
2. preserves definite foreground and background pixels;
3. applies an edge-aware guided filter only to the uncertain boundary;
4. uses a small motion-aware temporal blend only on stable edge pixels;
5. propagates nearby subject colours into semi-transparent edge pixels to reduce wall-colour halos;
6. renders the foreground with a restrained local drop shadow.

Existing productions must rerun stage 5 and then rebuild stages 7–10 to use the new matte.

## B-roll-only layout

`broll_only` is an explicit full-screen editorial mode. It always:

- uses the complete canvas;
- hides the graphic board;
- disables the foreground subject layer;
- keeps the original narration audio underneath.

`full_frame` remains available for backward compatibility and may still be combined with a foreground subject when explicitly requested.

## Optional Malayalam captions

New productions default to captions **Off**. Available run modes are:

- `off`: never render transcript captions;
- `auto`: render captions only for slots whose `show_caption` switch is enabled;
- `all`: render captions throughout the transcript.

Each slot also exposes `show_caption` and `caption_position` (`auto`, `top`, or `bottom`). Auto positioning moves captions away from lower boards and torn-paper layouts so local copy and captions do not collide.

For productions created before this setting existed, rerendering uses the safe `auto` compatibility mode; legacy slots have no `show_caption` value and therefore render without captions until enabled.
