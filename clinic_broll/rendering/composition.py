from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from ..core.io import link_or_copy, read_json, write_json
from ..core.paths import run_paths
from ..core.state import load_run


FOREGROUND_CHUNK_SECONDS = 2.0


def build(run_id: str, mode: str) -> Path:
    if mode not in {"still", "motion", "final"}:
        raise ValueError(mode)
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    transcript = read_json(paths.transcript / "transcript.json", {"phrases": []})
    source_meta = read_json(paths.source / "metadata.json", {})
    duration = float(source_meta.get("duration_seconds") or transcript.get("duration_seconds") or 1)
    composition = paths.compositions / mode
    assets = composition / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    source = paths.source / ("master.mp4" if mode == "final" else "proxy.mp4")
    link_or_copy(source, assets / "talking-head.mp4")
    foreground_source = paths.matte / "foreground.webm"
    foreground_available = foreground_source.exists()
    if foreground_available:
        link_or_copy(foreground_source, assets / "foreground.webm")

    rendered_slots = []
    for slot in plan.get("slots", []):
        media = _select_media(paths.root, slot, mode)
        if not media:
            continue
        destination = assets / f"{slot['slot_id']}{media.suffix.lower()}"
        link_or_copy(media, destination)
        rendered_slots.append(
            {
                **slot,
                "media_file": destination.name,
                "media_kind": "video" if media.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"} else "image",
            }
        )

    manifest = {
        "run_id": run_id,
        "mode": mode,
        "width": int(meta["settings"].get("width", 1080)),
        "height": int(meta["settings"].get("height", 1920)),
        "fps": int(meta["settings"].get("fps", 30)),
        "duration": duration,
        "foreground_available": foreground_available,
        "slots": rendered_slots,
        "phrases": transcript.get("phrases") or [],
        "captions": transcript.get("captions") or transcript.get("phrases") or [],
    }
    write_json(composition / "composition-manifest.json", manifest)
    (composition / "index.html").write_text(_html(manifest), encoding="utf-8")
    return composition / "index.html"


def _select_media(run_root: Path, slot: dict[str, Any], mode: str) -> Path | None:
    def latest(kind: str) -> str | None:
        versions = (slot.get("versions") or {}).get(kind) or []
        return versions[-1]["path"] if versions else None

    candidates: list[str | None]
    if mode == "still":
        # Review the newest candidate even when an older accepted still remains selected.
        candidates = [latest("stills"), slot.get("selected_still")]
    elif mode == "motion":
        # Review the newest motion candidate; fall back to the accepted/current still.
        candidates = [latest("motion"), slot.get("selected_motion"), slot.get("selected_still"), latest("stills")]
    else:
        # Final output uses only explicitly selected (therefore approved) assets.
        candidates = [slot.get("selected_motion"), slot.get("selected_still")]

    for candidate in candidates:
        if not candidate:
            continue
        path = run_root / candidate
        if path.exists():
            return path
    return None


def _html(manifest: dict[str, Any]) -> str:
    width, height = manifest["width"], manifest["height"]
    duration, fps = manifest["duration"], manifest["fps"]
    slot_markup = "\n".join(_slot_markup(slot) for slot in manifest["slots"])
    # Keep transparent foreground media bounded to the windows where it is
    # actually visible. HyperFrames extracts alpha video as full-resolution
    # RGBA PNG frames; declaring one composition-length clip makes Chromium
    # retain thousands of injected frames even while CSS opacity is zero.
    # Source-bounded clip elements omit inactive alpha frames from capture;
    # data-media-start preserves the immutable source timeline.
    foreground = _foreground_markup(manifest)
    phrases = json.dumps(manifest["phrases"], ensure_ascii=False)
    captions = json.dumps(manifest.get("captions") or manifest["phrases"], ensure_ascii=False)
    slots_json = json.dumps(
        [
            {
                "id": s["slot_id"],
                "start": s["start"],
                "end": s["end"],
                "layout": s["layout_template"],
                "foreground": s.get("keep_subject_foreground", True),
                "foreground_chunks": (
                    len(_foreground_intervals(s, int(manifest["fps"])))
                    if manifest["foreground_available"] and s.get("keep_subject_foreground", True)
                    else 0
                ),
            }
            for s in manifest["slots"]
        ],
        ensure_ascii=False,
    )
    template = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Clinic B-roll · __MODE__</title>
<style>
:root {--teal:#36d1c4;--navy:#07111f;--panel:#102442;--ink:#f2f8ff;--muted:#a8c0d8}
*{box-sizing:border-box}
html,body{margin:0;background:#02070d;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;overflow:hidden}
#stage{position:relative;width:__WIDTH__px;height:__HEIGHT__px;overflow:hidden;background:#081321;transform-origin:top left}
.track{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
#master{z-index:1}
.slot{position:absolute;inset:0;z-index:10;pointer-events:none;opacity:0;overflow:hidden}
.panel{position:absolute;background:linear-gradient(145deg,#0a2a54 0%,#102f69 58%,#081b38 100%);box-shadow:0 -18px 60px rgba(0,0,0,.24);overflow:hidden}
.panel:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.075) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.075) 1px,transparent 1px);background-size:38px 38px;opacity:.5}
.panel:after{content:"";position:absolute;left:0;right:0;height:34px;background:linear-gradient(135deg,transparent 11px,#fff 12px,#fff 17px,transparent 18px) 0 0/34px 34px repeat-x;opacity:.96}
.bottom_board .panel{left:0;right:0;bottom:0;height:var(--region)} .bottom_board .panel:after{top:-17px}
.top_board .panel{left:0;right:0;top:0;height:var(--region)} .top_board .panel:after{bottom:-17px;transform:rotate(180deg)}
.left_panel .panel{left:0;top:0;bottom:0;width:var(--region)} .right_panel .panel{right:0;top:0;bottom:0;width:var(--region)}
.torn_split .panel{left:0;right:0;bottom:0;height:var(--region);clip-path:polygon(0 8%,7% 3%,15% 9%,24% 2%,35% 8%,44% 3%,56% 9%,65% 2%,76% 8%,88% 3%,100% 9%,100% 100%,0 100%)}
.floating_cards .panel{right:5%;top:12%;width:54%;height:42%;border-radius:34px}
.full_frame .panel{inset:0}
.generated{position:absolute;z-index:2;object-fit:cover;border-radius:24px;box-shadow:0 22px 60px rgba(0,0,0,.38)}
.bottom_board .generated,.top_board .generated,.torn_split .generated,.generated.bottom_board,.generated.top_board,.generated.torn_split{left:6%;width:88%;height:76%;bottom:6%}
.top_board .generated,.generated.top_board{top:6%;bottom:auto}
.left_panel .generated,.generated.left_panel{left:3%;top:12%;width:50%;height:76%}
.right_panel .generated,.generated.right_panel{right:3%;top:12%;width:50%;height:76%}
.floating_cards .generated,.generated.floating_cards{right:8%;top:16%;width:48%;height:34%}
.full_frame .generated,.generated.full_frame{inset:0;width:100%;height:100%;border-radius:0}
.generated-track{z-index:20;opacity:0;pointer-events:none}
.local-copy{position:absolute;z-index:4;left:7%;right:7%;bottom:5.5%;font-size:48px;line-height:1.05;font-weight:850;text-align:center;text-shadow:0 4px 20px rgba(0,0,0,.65)}
.foreground{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:30;pointer-events:none}
.caption{position:absolute;z-index:60;left:7%;right:7%;bottom:7.5%;padding:16px 22px;border-radius:22px;background:rgba(3,10,18,.82);font-weight:800;font-size:42px;line-height:1.22;text-align:center;text-shadow:0 2px 5px #000;opacity:0}
.brand{position:absolute;z-index:70;top:3.5%;right:4%;padding:10px 16px;border:1px solid rgba(255,255,255,.28);border-radius:999px;background:rgba(5,19,34,.62);font-size:23px;font-weight:800;letter-spacing:.04em}
</style>
</head>
<body>
<div id="stage" data-composition-id="clinic-broll" data-no-timeline data-start="0" data-duration="__DURATION__" data-width="__WIDTH__" data-height="__HEIGHT__" data-fps="__FPS__">
  <video id="master" class="track" data-start="0" data-duration="__DURATION__" data-track-index="0" data-volume="1" data-has-audio="true" src="assets/talking-head.mp4" playsinline preload="auto"></video>
  __SLOTS__
  __FOREGROUND__
  <div id="caption" class="caption"></div>
  <div class="brand">SMILE CRAFT</div>
</div>
<script>
const DURATION=__DURATION__;
const PHRASES=__PHRASES__;
const CAPTIONS=__CAPTIONS__;
const SLOTS=__SLOTS_JSON__;
const stage=document.getElementById('stage');
const caption=document.getElementById('caption');
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const ease=v=>1-Math.pow(1-clamp(v),3);
function renderAt(t){
  for(const slot of SLOTS){
    const el=document.getElementById(slot.id);
    const media=document.getElementById(`${slot.id}-media`);
    const foreground=Array.from({length:slot.foreground_chunks||0},(_,i)=>document.getElementById(slot.id+'-foreground-'+String(i+1).padStart(2,'0'))).filter(Boolean);
    if(!el) continue;
    const active=t>=slot.start && t<slot.end;
    if(!active){el.style.opacity='0';if(media)media.style.opacity='0';for(const fg of foreground)fg.style.opacity='0';continue}
    const d=slot.end-slot.start,p=(t-slot.start)/d;
    const intro=ease(clamp(p/0.13)),outro=ease(clamp((1-p)/0.13));
    const opacity=String(Math.min(intro,outro));
    el.style.opacity=opacity;
    if(media)media.style.opacity=opacity;
    for(const fg of foreground)fg.style.opacity=opacity;
    const panel=el.querySelector('.panel');
    const y=(1-intro)*80-(1-outro)*30;
    if(panel) panel.style.transform=`translate3d(0,${y}px,0)`;
    if(media) media.style.transform=`scale(${1.025+0.025*p})`;
  }
  const phrase=CAPTIONS.find(p=>t>=Number(p.start)&&t<Number(p.end));
  caption.textContent=phrase?phrase.text:'';
  caption.style.opacity=phrase?'1':'0';
}
window.addEventListener('hf-seek',e=>renderAt(Number(e.detail.time||0)));
renderAt(0);
window.__hf_ready__=true;
</script>
</body>
</html>'''
    replacements = {
        "__MODE__": html.escape(str(manifest["mode"])),
        "__WIDTH__": str(width),
        "__HEIGHT__": str(height),
        "__DURATION__": f"{duration:.6f}",
        "__FPS__": str(fps),
        "__SLOTS__": slot_markup,
        "__FOREGROUND__": foreground,
        "__PHRASES__": phrases,
        "__CAPTIONS__": captions,
        "__SLOTS_JSON__": slots_json,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _foreground_markup(manifest: dict[str, Any], max_chunk_seconds: float = FOREGROUND_CHUNK_SECONDS) -> str:
    """Author source-aligned alpha clips small enough for Chromium's RGBA cache.

    HyperFrames injects extracted alpha-video frames as full-resolution PNGs.
    A composition-length media element makes Chromium retain enough decoded
    RGBA frames to wedge screenshot capture. These clips omit all source frames
    outside visible slot windows; data-media-start keeps every chunk on the
    original immutable source clock.
    """
    if not manifest["foreground_available"]:
        return ""
    markup: list[str] = []
    fps = int(manifest["fps"])
    for slot in manifest["slots"]:
        if not slot.get("keep_subject_foreground", True):
            continue
        slot_id = str(slot["slot_id"])
        for index, (chunk_start, chunk_end) in enumerate(
            _foreground_intervals(slot, fps, max_chunk_seconds)
        ):
            chunk_start = round(chunk_start, 6)
            chunk_end = round(chunk_end, 6)
            chunk_duration = chunk_end - chunk_start
            markup.append(
                f'<video id="{html.escape(slot_id)}-foreground-{index + 1:02d}" class="foreground clip" '
                f'data-foreground-slot="{html.escape(slot_id)}" '
                f'data-start="{chunk_start:.6f}" data-duration="{chunk_duration:.6f}" '
                f'data-media-start="{chunk_start:.6f}" data-track-index="40" '
                'src="assets/foreground.webm" muted playsinline></video>'
            )
    return "\n".join(markup)


def _foreground_intervals(
    slot: dict[str, Any], fps: int, max_chunk_seconds: float = FOREGROUND_CHUNK_SECONDS
) -> list[tuple[float, float]]:
    """Split a slot at frame-aligned internal boundaries without moving its ends."""
    start = float(slot["start"])
    end = start + float(slot["duration"])
    chunk_count = max(1, math.ceil((end - start) / max_chunk_seconds))
    boundaries = [start]
    for index in range(1, chunk_count):
        target = start + (end - start) * index / chunk_count
        boundary = round(target * fps) / fps
        if boundary <= boundaries[-1]:
            boundary = boundaries[-1] + 1 / fps
        if boundary < end:
            boundaries.append(boundary)
    boundaries.append(end)
    return list(zip(boundaries, boundaries[1:]))


def _slot_markup(slot: dict[str, Any]) -> str:
    start, duration = float(slot["start"]), float(slot["duration"])
    region = f"{float(slot.get('panel_region', 0.44)) * 100:.1f}%"
    source = html.escape(slot["media_file"])
    slot_id = html.escape(str(slot["slot_id"]))
    layout = html.escape(str(slot["layout_template"]))
    direct_media = ""
    if slot["media_kind"] == "video":
        # HyperFrames requires timed media to be a direct stage child. The
        # panel and the video are sibling clips with the same immutable window.
        media_tag = ""
        direct_media = (
            f'<video id="{slot_id}-media" class="generated generated-track clip {layout}" '
            f'data-start="{start:.6f}" data-duration="{duration:.6f}" data-track-index="20" data-loop '
            f'style="--region:{region}" src="assets/{source}" muted playsinline preload="auto"></video>'
        )
    else:
        # The section owns the immutable timeline window. A still image is a
        # visual child of that timed section and therefore must not register as
        # a second independent HyperFrames clip.
        media_tag = f'<img id="{slot_id}-media" class="generated" src="assets/{source}" alt="" />'
    copy = html.escape(str(slot.get("text_overlay") or ""))
    copy_markup = f'<div id="{slot_id}-copy" class="local-copy">{copy}</div>' if copy else ""
    panel = (
        f'<section id="{slot_id}" class="slot clip {layout}" '
        f'data-start="{start:.6f}" data-duration="{duration:.6f}" data-track-index="10" style="--region:{region}">'
        f'<div id="{slot_id}-panel" class="panel"></div>{media_tag}{copy_markup}</section>'
    )
    return panel + direct_media
