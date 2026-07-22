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
CAPTION_MODES = {"off", "all", "auto"}
MODES = {"still", "motion", "complete", "final"}


def build(
    run_id: str,
    mode: str,
    *,
    window: tuple[float, float] | None = None,
    composition_name: str | None = None,
) -> Path:
    if mode not in MODES:
        raise ValueError(mode)
    paths = run_paths(run_id)
    meta = load_run(run_id)
    plan = read_json(paths.plan / "broll_plan.json", {"slots": []})
    transcript = read_json(paths.transcript / "transcript.json", {"phrases": []})
    source_meta = read_json(paths.source / "metadata.json", {})
    choreography = read_json(paths.editorial / "edit-choreography.json", {"cues": []})
    source_duration = float(source_meta.get("duration_seconds") or transcript.get("duration_seconds") or 1)
    window_start, window_end = window or (0.0, source_duration)
    if window_start < 0 or window_end <= window_start or window_end > source_duration + 1e-6:
        raise ValueError(f"Invalid composition window: {window_start:.6f}-{window_end:.6f}")
    duration = window_end - window_start
    composition = paths.compositions / (composition_name or mode)
    assets = composition / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    source_head = paths.source / ("master.mp4" if mode == "final" else "proxy.mp4")
    link_or_copy(source_head, assets / "source-head.mp4")
    if mode != "final":
        link_or_copy(paths.source / "proxy.mp4", assets / "talking-head.mp4")
    foreground_source = paths.matte / "foreground.webm"
    foreground_available = foreground_source.exists()
    if foreground_available:
        link_or_copy(foreground_source, assets / "foreground.webm")

    choreo_by_scene = {item.get("scene_id"): item for item in choreography.get("cues", [])}
    rendered_slots: list[dict[str, Any]] = []
    for slot in plan.get("slots", []):
        if slot.get("status") == "rejected":
            continue
        windowed_slot = _slot_in_window(slot, window_start, window_end)
        if windowed_slot is None:
            continue
        media = _select_media(paths.root, slot, mode)
        destination = None
        media_kind = None
        if media:
            destination = assets / f"{slot['slot_id']}{media.suffix.lower()}"
            link_or_copy(media, destination)
            media_kind = "video" if media.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"} else "image"
        cue = choreo_by_scene.get(slot.get("scene_id"), slot.get("choreography") or {})
        rendered_slots.append({
            **windowed_slot,
            "media_file": destination.name if destination else None,
            "media_kind": media_kind,
            "choreography": cue,
        })

    raw_captions_mode = meta["settings"].get("captions_mode")
    captions_mode = "auto" if raw_captions_mode is None else str(raw_captions_mode)
    if captions_mode not in CAPTION_MODES:
        captions_mode = "off"
    manifest = {
        "run_id": run_id,
        "mode": mode,
        "width": int(meta["settings"].get("width", 1080)),
        "height": int(meta["settings"].get("height", 1920)),
        "fps": int(meta["settings"].get("fps", 30)),
        "duration": duration,
        "timeline_offset": window_start,
        "foreground_available": foreground_available,
        "captions_mode": captions_mode,
        "slots": rendered_slots,
        "phrases": _items_in_window(transcript.get("phrases") or [], window_start, window_end),
        "captions": _items_in_window(transcript.get("captions") or transcript.get("phrases") or [], window_start, window_end),
    }
    write_json(composition / "composition-manifest.json", manifest)
    (composition / "index.html").write_text(_html(manifest), encoding="utf-8")
    return composition / "index.html"


def _slot_in_window(slot: dict[str, Any], window_start: float, window_end: float) -> dict[str, Any] | None:
    start = float(slot["start"])
    end = start + float(slot["duration"])
    if end <= window_start or start >= window_end:
        return None
    if start < window_start - 1e-6 or end > window_end + 1e-6:
        raise ValueError(f"Chunk boundary splits slot {slot.get('slot_id')}: {start:.6f}-{end:.6f}")
    return {**slot, "start": start - window_start, "end": end - window_start}


def _items_in_window(items: list[dict[str, Any]], window_start: float, window_end: float) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    for item in items:
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start)
        if end <= window_start or start >= window_end:
            continue
        shifted.append({**item, "start": start - window_start, "end": end - window_start})
    return shifted


def _select_media(run_root: Path, slot: dict[str, Any], mode: str) -> Path | None:
    def latest(kind: str) -> str | None:
        versions = (slot.get("versions") or {}).get(kind) or []
        return versions[-1]["path"] if versions else None

    if mode == "still":
        candidates = [latest("stills"), slot.get("selected_still")]
    elif mode in {"motion", "complete"}:
        candidates = [latest("motion"), slot.get("selected_motion"), slot.get("selected_still"), latest("stills")]
    else:
        candidates = [slot.get("selected_motion"), slot.get("selected_still")]
    for candidate in candidates:
        if candidate and (run_root / candidate).exists():
            return run_root / candidate
    return None


def _html(manifest: dict[str, Any]) -> str:
    width, height = manifest["width"], manifest["height"]
    duration, fps = manifest["duration"], manifest["fps"]
    slot_markup = "\n".join(_slot_markup(slot, float(manifest.get("timeline_offset") or 0)) for slot in manifest["slots"])
    foreground = _foreground_markup(manifest)
    transparent_base = manifest["mode"] == "final"
    master = "" if transparent_base else (
        f'<video id="master" class="track" data-start="0" data-duration="{duration:.6f}" '
        'data-track-index="0" data-volume="1" data-has-audio="true" '
        'src="assets/talking-head.mp4" playsinline preload="auto"></video>'
    )
    captions = json.dumps(manifest.get("captions") or manifest["phrases"], ensure_ascii=False)
    captions_mode = str(manifest.get("captions_mode") or "off")
    slots_json = json.dumps([_slot_runtime(slot, manifest) for slot in manifest["slots"]], ensure_ascii=False)
    template = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Clinic B-roll V2 · __MODE__</title>
<style>
@font-face{font-family:"Noto Sans Malayalam";src:local("Noto Sans Malayalam")}
:root{--teal:#32cfc0;--cream:#fff8ec;--coral:#ff806f;--mint:#c8f3e8;--ink:#f7fbff;--navy:#07111f}
*{box-sizing:border-box}html,body{margin:0;background:__PAGE_BACKGROUND__;color:var(--ink);font-family:Inter,"Noto Sans Malayalam",ui-sans-serif,system-ui,-apple-system,sans-serif;overflow:hidden}
#stage{position:relative;width:__WIDTH__px;height:__HEIGHT__px;overflow:hidden;background:__STAGE_BACKGROUND__;transform-origin:top left}
.track{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}#master{z-index:1}
.slot{position:absolute;inset:0;z-index:10;pointer-events:none;opacity:0;overflow:hidden;isolation:isolate}
.panel{position:absolute;z-index:1;background:linear-gradient(145deg,rgba(255,248,236,.98),rgba(200,243,232,.96));box-shadow:0 20px 70px rgba(3,13,25,.25);overflow:hidden}
.panel:before{content:"";position:absolute;inset:0;background:radial-gradient(circle at 15% 10%,rgba(255,128,111,.22),transparent 38%),radial-gradient(circle at 90% 80%,rgba(50,207,192,.22),transparent 42%)}
.generated{position:absolute;z-index:4;object-fit:cover;box-shadow:0 20px 60px rgba(0,0,0,.28);transform-origin:center}
.generated-track{z-index:20;opacity:0;pointer-events:none}
.subject-copy{position:absolute;z-index:6;object-fit:cover;background:#081321;box-shadow:0 16px 48px rgba(0,0,0,.3);transform-origin:var(--face-x,50%) var(--face-y,30%)}
.bottom_board .panel{left:0;right:0;bottom:0;height:var(--region)}.bottom_board .generated{left:5%;right:5%;bottom:4%;width:90%;height:78%;border-radius:26px}
.top_board .panel{left:0;right:0;top:0;height:var(--region)}.top_board .generated{left:5%;right:5%;top:4%;width:90%;height:78%;border-radius:26px}
.left_panel .panel{left:0;top:0;bottom:0;width:var(--region)}.left_panel .generated{left:3%;top:10%;width:50%;height:78%;border-radius:26px}
.right_panel .panel{right:0;top:0;bottom:0;width:var(--region)}.right_panel .generated{right:3%;top:10%;width:50%;height:78%;border-radius:26px}
.torn_split .panel{left:0;right:0;bottom:0;height:var(--region);clip-path:polygon(0 8%,7% 3%,15% 9%,24% 2%,35% 8%,44% 3%,56% 9%,65% 2%,76% 8%,88% 3%,100% 9%,100% 100%,0 100%)}
.torn_split .generated{left:5%;bottom:4%;width:90%;height:78%;border-radius:24px}
.floating_cards .panel,.floating_visual .panel{right:5%;top:12%;width:54%;height:42%;border-radius:34px}.floating_cards .generated,.floating_visual .generated{right:7%;top:15%;width:50%;height:36%;border-radius:28px}
.full_frame .panel,.broll_only .panel{inset:0}.broll_only .panel{display:none}.full_frame .generated,.broll_only .generated{inset:0;width:100%;height:100%;border-radius:0;box-shadow:none}
.split_top .panel{left:0;right:0;top:0;height:56%;background:#091522}.split_top .generated{left:0;top:0;width:100%;height:56%;box-shadow:none}.split_top .subject-copy{left:0;bottom:0;width:100%;height:44%;box-shadow:none}
.split_bottom .panel{left:0;right:0;bottom:0;height:56%;background:#091522}.split_bottom .generated{left:0;bottom:0;width:100%;height:56%;box-shadow:none}.split_bottom .subject-copy{left:0;top:0;width:100%;height:44%;box-shadow:none}
.split_right .panel{right:0;top:0;bottom:0;width:52%;background:#091522}.split_right .generated{right:0;top:0;width:52%;height:100%;box-shadow:none}.split_right .subject-copy{left:0;top:0;width:48%;height:100%;box-shadow:none}
.split_left .panel{left:0;top:0;bottom:0;width:52%;background:#091522}.split_left .generated{left:0;top:0;width:52%;height:100%;box-shadow:none}.split_left .subject-copy{right:0;top:0;width:48%;height:100%;box-shadow:none}
.picture_in_picture .panel{inset:0;background:#07111f}.picture_in_picture .generated{inset:0;width:100%;height:100%;box-shadow:none}.picture_in_picture .subject-copy{right:5%;bottom:7%;width:36%;height:31%;border-radius:28px;border:3px solid rgba(255,255,255,.85)}
.talking_head .panel{display:none}.talking_head .subject-copy{inset:0;width:100%;height:100%;box-shadow:none}
.local-copy{position:absolute;z-index:8;left:7%;right:7%;bottom:5.5%;padding:14px 20px;border-radius:20px;background:rgba(3,10,18,.62);font-size:46px;line-height:1.08;font-weight:850;text-align:center;text-shadow:0 3px 14px rgba(0,0,0,.7)}
.split_top .local-copy,.split_bottom .local-copy,.split_left .local-copy,.split_right .local-copy{bottom:3%;font-size:38px}.broll_only .local-copy{top:7%;bottom:auto}
.foreground{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:30;pointer-events:none;filter:drop-shadow(0 10px 22px rgba(0,0,0,.22));transform:translateZ(0)}
.caption{position:absolute;z-index:60;left:7%;right:7%;padding:16px 22px;border-radius:22px;background:rgba(3,10,18,.82);font-weight:800;font-size:42px;line-height:1.22;text-align:center;text-shadow:0 2px 5px #000;opacity:0}.caption.bottom{bottom:7.5%}.caption.top{top:8.5%}
.brand{position:absolute;z-index:70;top:3.5%;right:4%;padding:10px 16px;border:1px solid rgba(255,255,255,.28);border-radius:999px;background:rgba(5,19,34,.62);font-size:23px;font-weight:800;letter-spacing:.04em}
.emphasis-warning_pulse{filter:saturate(1.08) contrast(1.04)}.emphasis-card_snap .generated{box-shadow:0 24px 80px rgba(0,0,0,.42)}
</style></head><body>
<div id="stage" data-composition-id="clinic-broll" data-no-timeline data-start="0" data-duration="__DURATION__" data-width="__WIDTH__" data-height="__HEIGHT__" data-fps="__FPS__">
__MASTER__
__SLOTS__
__FOREGROUND__
<div id="caption" class="caption bottom"></div><div class="brand">SMILE CRAFT</div></div>
<script>
const CAPTIONS=__CAPTIONS__;const CAPTION_MODE=__CAPTION_MODE__;const SLOTS=__SLOTS_JSON__;
const caption=document.getElementById('caption');const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));const ease=v=>1-Math.pow(1-clamp(v),3);
function placement(slot){if(!slot)return'bottom';if(slot.caption_position==='top'||slot.caption_position==='bottom')return slot.caption_position;return ['bottom_board','torn_split','split_bottom'].includes(slot.layout)?'top':'bottom'}
function transition(slot,intro,outro){const amount=1-intro;const exit=1-outro;switch(slot.transition_in){case'clean_push':return`translate3d(${amount*90}px,0,0)`;case'vertical_slide':return`translate3d(0,${amount*100}px,0)`;case'horizontal_swipe':return`translate3d(${-amount*110}px,0,0)`;case'zoom_match':return`scale(${.92+.08*intro})`;case'blur_transition':return`scale(${.98+.02*intro})`;default:return`translate3d(0,${amount*30-exit*18}px,0)`}}
function renderAt(t){let activeSlot=null;for(const slot of SLOTS){const el=document.getElementById(slot.id);const media=document.getElementById(`${slot.id}-media`);const subject=document.getElementById(`${slot.id}-subject`);const foreground=Array.from({length:slot.foreground_chunks||0},(_,i)=>document.getElementById(slot.id+'-foreground-'+String(i+1).padStart(2,'0'))).filter(Boolean);if(!el)continue;const active=t>=slot.start&&t<slot.end;if(!active){el.style.opacity='0';if(media)media.style.opacity='0';if(subject)subject.style.opacity='0';for(const fg of foreground)fg.style.opacity='0';continue}activeSlot=slot;const d=slot.end-slot.start,p=clamp((t-slot.start)/d);const intro=ease(clamp(p/.12)),outro=ease(clamp((1-p)/.12));const opacity=String(Math.min(intro,outro));el.style.opacity=opacity;el.style.transform=transition(slot,intro,outro);if(media){media.style.opacity=opacity;const base=['broll_only','full_frame','picture_in_picture'].includes(slot.layout)?1.005:1.015;media.style.transform=`scale(${base+.025*p})`}if(subject){subject.style.opacity=opacity;const scale=slot.from_scale+(slot.to_scale-slot.from_scale)*ease(p);subject.style.transform=`scale(${scale})`;subject.style.objectPosition=`${slot.face_x*100}% ${slot.face_y*100}%`}for(const fg of foreground)fg.style.opacity=opacity}
const phrase=CAPTIONS.find(p=>t>=Number(p.start)&&t<Number(p.end));const enabled=CAPTION_MODE==='all'||(CAPTION_MODE==='auto'&&Boolean(activeSlot?.show_caption));caption.textContent=enabled&&phrase?phrase.text:'';caption.className=`caption ${placement(activeSlot)}`;caption.style.opacity=enabled&&phrase?'1':'0'}
window.addEventListener('hf-seek',e=>renderAt(Number(e.detail.time||0)));renderAt(0);window.__hf_ready__=true;
</script></body></html>'''
    replacements = {
        "__MODE__": html.escape(str(manifest["mode"])), "__WIDTH__": str(width), "__HEIGHT__": str(height),
        "__DURATION__": f"{duration:.6f}", "__FPS__": str(fps), "__SLOTS__": slot_markup,
        "__FOREGROUND__": foreground, "__MASTER__": master,
        "__PAGE_BACKGROUND__": "transparent" if transparent_base else "#02070d",
        "__STAGE_BACKGROUND__": "transparent" if transparent_base else "#081321",
        "__CAPTIONS__": captions, "__CAPTION_MODE__": json.dumps(captions_mode), "__SLOTS_JSON__": slots_json,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _slot_runtime(slot: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    cue = slot.get("choreography") or {}
    reframe = slot.get("reframe") or {}
    face_box = reframe.get("face_box") or [0.32, 0.12, 0.68, 0.5]
    face_x = (float(face_box[0]) + float(face_box[2])) / 2
    face_y = (float(face_box[1]) + float(face_box[3])) / 2
    return {
        "id": slot["slot_id"], "start": slot["start"], "end": slot["end"],
        "layout": slot.get("layout_template", "bottom_board"),
        "variant": slot.get("layout_variant", "layered_foreground"),
        "subject_mode": slot.get("subject_mode", "matte_foreground"),
        "show_caption": bool(slot.get("show_caption", False)),
        "caption_position": str(slot.get("caption_position") or "auto"),
        "camera_move": cue.get("camera_move", slot.get("camera_move", "static")),
        "transition_in": cue.get("transition_in", slot.get("transition_in", "direct_cut")),
        "transition_out": cue.get("transition_out", slot.get("transition_out", "direct_cut")),
        "emphasis_preset": cue.get("emphasis_preset", slot.get("emphasis_preset", "none")),
        "from_scale": float(cue.get("from_scale", 1.0)), "to_scale": float(cue.get("to_scale", 1.0)),
        "face_x": face_x, "face_y": face_y,
        "foreground_chunks": len(_foreground_intervals(slot, int(manifest["fps"]))) if manifest["foreground_available"] and slot.get("keep_subject_foreground") else 0,
    }


def _slot_markup(slot: dict[str, Any], timeline_offset: float = 0.0) -> str:
    start, duration = float(slot["start"]), float(slot["duration"])
    region = f"{float(slot.get('panel_region', 0.44)) * 100:.1f}%"
    slot_id = html.escape(str(slot["slot_id"]))
    layout = html.escape(str(slot.get("layout_template") or "bottom_board"))
    emphasis = html.escape(str((slot.get("choreography") or {}).get("emphasis_preset") or slot.get("emphasis_preset") or "none"))
    reframe = slot.get("reframe") or {}
    face_box = reframe.get("face_box") or [0.32, 0.12, 0.68, 0.5]
    face_x = ((float(face_box[0]) + float(face_box[2])) / 2) * 100
    face_y = ((float(face_box[1]) + float(face_box[3])) / 2) * 100
    subject_mode = str(slot.get("subject_mode") or "matte_foreground")
    source_subject = ""
    # Talking-head scenes already use the master video track underneath every
    # slot. Adding another timed copy is redundant and creates fragile tiny
    # clips at editorial continuity boundaries. Subject copies are needed only
    # for split and picture-in-picture layouts.
    if layout != "talking_head" and subject_mode in {"original", "cropped_original", "picture_in_picture"}:
        media_start = timeline_offset + start
        source_subject = (
            f'<video id="{slot_id}-subject" class="subject-copy clip" data-start="{start:.6f}" '
            f'data-duration="{duration:.6f}" data-media-start="{media_start:.6f}" data-track-index="25" '
            f'style="--face-x:{face_x:.2f}%;--face-y:{face_y:.2f}%" src="assets/source-head.mp4" muted playsinline preload="auto"></video>'
        )
    media_tag = ""
    media_file = slot.get("media_file")
    if media_file:
        source = html.escape(str(media_file))
        if slot.get("media_kind") == "video":
            media_tag = (
                f'<video id="{slot_id}-media" class="generated generated-track clip" '
                f'data-start="{start:.6f}" data-duration="{duration:.6f}" data-track-index="20" data-loop '
                f'style="--region:{region}" src="assets/{source}" muted playsinline preload="auto"></video>'
            )
        else:
            media_tag = f'<img id="{slot_id}-media" class="generated" src="assets/{source}" alt=""/>'
    copy = html.escape(str(slot.get("text_overlay") or ""))
    copy_markup = f'<div id="{slot_id}-copy" class="local-copy">{copy}</div>' if copy else ""
    panel = (
        f'<section id="{slot_id}" class="slot {layout} emphasis-{emphasis}" style="--region:{region}">'
        f'<div id="{slot_id}-panel" class="panel"></div>{media_tag}{source_subject}{copy_markup}</section>'
    )
    return panel


def _foreground_markup(manifest: dict[str, Any], max_chunk_seconds: float = FOREGROUND_CHUNK_SECONDS) -> str:
    if not manifest["foreground_available"]:
        return ""
    markup: list[str] = []
    fps = int(manifest["fps"])
    timeline_offset = float(manifest.get("timeline_offset") or 0)
    for slot in manifest["slots"]:
        if not slot.get("keep_subject_foreground"):
            continue
        slot_id = str(slot["slot_id"])
        for index, (chunk_start, chunk_end) in enumerate(_foreground_intervals(slot, fps, max_chunk_seconds)):
            chunk_start, chunk_end = round(chunk_start, 6), round(chunk_end, 6)
            media_start = timeline_offset + chunk_start
            markup.append(
                f'<video id="{html.escape(slot_id)}-foreground-{index + 1:02d}" class="foreground clip" '
                f'data-foreground-slot="{html.escape(slot_id)}" data-start="{chunk_start:.6f}" '
                f'data-duration="{chunk_end - chunk_start:.6f}" data-media-start="{media_start:.6f}" '
                'data-track-index="40" src="assets/foreground.webm" muted playsinline></video>'
            )
    return "\n".join(markup)


def _foreground_intervals(slot: dict[str, Any], fps: int, max_chunk_seconds: float = FOREGROUND_CHUNK_SECONDS) -> list[tuple[float, float]]:
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
