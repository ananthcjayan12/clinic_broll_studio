# Renderer decision: HyperFrames

## Why it wins for this repository

The composition is fundamentally a browser layering problem rather than a React application problem. It requires:

- one base video;
- one transparent foreground video;
- timed media panels;
- CSS masks and torn-paper shapes;
- deterministic scrubbing;
- a plain preview artifact that an operator can inspect.

HyperFrames directly models these requirements with HTML and timeline data attributes. Its frame clock emits `hf-seek`, which the composition uses as the authoritative animation time.

## Where Remotion would win

Choose Remotion instead when:

- the team already maintains a React design system;
- compositions must be packaged as React components;
- Remotion Lambda is a hard requirement;
- the organization accepts its licensing model and operational stack.

None of those conditions is required for this local clinic production tool.
