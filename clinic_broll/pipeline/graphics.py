from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def render_local_graphic(path: Path, slot: dict[str, Any], *, width: int, height: int) -> dict[str, Any]:
    """Render a clean deterministic explainer frame.

    Medical mechanisms are intentionally rendered as restrained educational
    diagrams instead of asking a photoreal image model to invent anatomy or
    physics. The source talking-head and compositor remain responsible for all
    text and branding.
    """
    strategy = str(slot.get("visual_strategy") or "editorial_graphic")
    text = " ".join(
        str(slot.get(key) or "")
        for key in ("transcript", "purpose", "still_brief", "text_overlay")
    ).lower()
    # Render oversized then downsample.  Pillow's vector-like primitives are
    # otherwise visibly jagged on vertical video canvases, particularly on
    # tooth contours and fine bristles.
    scale = 2
    render_width, render_height = width * scale, height * scale
    image = _background(render_width, render_height)
    draw = ImageDraw.Draw(image, "RGBA")
    if strategy == "dental_diagram":
        if any(token in text for token in ("brush", "bristle", "abrasion", "sandpaper", "ബ്രഷ")):
            kind = "brush_friction"
            _brush_friction(draw, render_width, render_height)
        elif any(token in text for token in ("saliva", "neutral", "buffer", "ph", "acid", "ആസിഡ")):
            kind = "acid_buffer"
            _acid_buffer(draw, render_width, render_height)
        else:
            kind = "tooth_layers"
            _tooth_layers(draw, render_width, render_height)
    else:
        if any(token in text for token in ("wait", "minute", "clock", "30", "60", "കാത്തിര", "മിനിറ്റ്")):
            kind = "clock"
            _clock(draw, render_width, render_height)
        elif any(token in text for token in ("avoid", "warning", "immediately", "ഉടനെ")):
            kind = "pause_brush"
            _pause_brush(draw, render_width, render_height)
        else:
            kind = "simple_steps"
            _simple_steps(draw, render_width, render_height)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((width, height), Image.Resampling.LANCZOS).save(path, format="PNG", optimize=True)
    return {"provider": "local_graphic", "kind": kind, "path": str(path)}


def _background(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), (248, 246, 239))
    pixels = image.load()
    top = (248, 246, 239)
    bottom = (229, 245, 241)
    for y in range(height):
        t = y / max(height - 1, 1)
        row = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(width):
            pixels[x, y] = row
    return image


def _tooth_shape(cx: float, cy: float, scale: float) -> list[tuple[float, float]]:
    return [
        (cx - 0.44 * scale, cy - 0.24 * scale),
        (cx - 0.34 * scale, cy - 0.46 * scale),
        (cx - 0.05 * scale, cy - 0.54 * scale),
        (cx + 0.30 * scale, cy - 0.45 * scale),
        (cx + 0.45 * scale, cy - 0.18 * scale),
        (cx + 0.34 * scale, cy + 0.10 * scale),
        (cx + 0.18 * scale, cy + 0.54 * scale),
        (cx, cy + 0.34 * scale),
        (cx - 0.20 * scale, cy + 0.54 * scale),
        (cx - 0.34 * scale, cy + 0.10 * scale),
    ]


def _clinical_card(draw: ImageDraw.ImageDraw, width: int, height: int) -> tuple[float, float, float, float]:
    """Give split-layout graphics a deliberate lower-panel composition."""
    bounds = (width * 0.08, height * 0.42, width * 0.92, height * 0.90)
    shadow = tuple(value + offset for value, offset in zip(bounds, (0, height * 0.012, 0, height * 0.012)))
    radius = int(min(width, height) * 0.045)
    draw.rounded_rectangle(shadow, radius=radius, fill=(47, 88, 82, 24))
    draw.rounded_rectangle(bounds, radius=radius, fill=(255, 255, 252, 238), outline=(190, 220, 214, 255), width=max(2, int(width * 0.003)))
    return bounds


def _tooth_layers(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    _clinical_card(draw, width, height)
    scale = min(width, height) * 0.31
    cx, cy = width * 0.50, height * 0.66
    outer = _tooth_shape(cx, cy, scale)
    draw.polygon(outer, fill=(255, 255, 252, 255), outline=(36, 109, 105, 255))
    inner = _tooth_shape(cx, cy + scale * 0.03, scale * 0.78)
    draw.polygon(inner, fill=(247, 218, 147, 255), outline=(222, 168, 76, 255))
    pulp = _tooth_shape(cx, cy + scale * 0.08, scale * 0.42)
    draw.polygon(pulp, fill=(239, 126, 119, 210), outline=(194, 78, 76, 255))
    _dots(draw, width, height, [(0.18, 0.25), (0.78, 0.30), (0.22, 0.73), (0.82, 0.70)], (45, 190, 178, 90), 0.022)


def _acid_buffer(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    _clinical_card(draw, width, height)
    scale = min(width, height) * 0.31
    cx, cy = width * 0.50, height * 0.67
    outer = _tooth_shape(cx, cy, scale)
    draw.polygon(outer, fill=(255, 255, 252, 255), outline=(32, 121, 114, 255), width=max(3, int(scale * 0.012)))
    enamel = _tooth_shape(cx, cy + scale * 0.02, scale * 0.82)
    draw.polygon(enamel, fill=(248, 229, 178, 255), outline=(221, 180, 101, 255), width=max(2, int(scale * 0.008)))
    for index in range(9):
        angle = math.pi * (0.15 + 0.07 * index)
        radius = scale * (0.62 + 0.08 * (index % 3))
        x = cx + math.cos(angle) * radius
        y = cy - scale * 0.46 + math.sin(angle) * radius * 0.26
        r = scale * 0.026
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(242, 139, 91, 180))
    arc_box = (cx - scale * 0.72, cy - scale * 0.82, cx + scale * 0.72, cy + scale * 0.46)
    draw.arc(arc_box, 205, 335, fill=(50, 188, 175, 255), width=max(5, int(scale * 0.024)))
    for offset in (-0.25, 0, 0.25):
        x = cx + offset * scale
        y = cy - scale * 0.70
        r = scale * 0.038
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(65, 198, 187, 180))


def _brush_friction(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    _clinical_card(draw, width, height)
    scale = min(width, height) * 0.30
    cx, cy = width * 0.52, height * 0.70
    outer = _tooth_shape(cx, cy, scale)
    draw.polygon(outer, fill=(255, 255, 252, 255), outline=(42, 111, 108, 255), width=max(3, int(scale * 0.012)))
    draw.polygon(_tooth_shape(cx, cy + scale * 0.02, scale * 0.82), fill=(249, 231, 184, 255), outline=(221, 180, 101, 255), width=max(2, int(scale * 0.008)))
    handle_h = max(24, int(scale * 0.12))
    x0, y0 = width * 0.15, height * 0.50
    x1, y1 = width * 0.70, y0 + handle_h
    draw.rounded_rectangle((x0, y0, x1, y1), radius=handle_h // 2, fill=(45, 184, 174, 255))
    head_x0 = width * 0.57
    head_x1 = width * 0.85
    draw.rounded_rectangle((head_x0, y0 - handle_h * 0.30, head_x1, y1 + handle_h * 0.30), radius=handle_h // 2, fill=(235, 239, 240, 255), outline=(45, 111, 108, 255))
    for index in range(9):
        x = head_x0 + (index + 0.7) * (head_x1 - head_x0) / 10
        draw.line((x, y1, x - scale * 0.025, y1 + scale * 0.14), fill=(61, 126, 150, 255), width=max(3, int(scale * 0.012)))
    for offset in (-0.13, 0.0, 0.13):
        x = cx + offset * scale
        y = cy - scale * 0.49
        draw.line((x, y - scale * 0.18, x + scale * 0.06, y - scale * 0.03), fill=(239, 116, 95, 210), width=max(4, int(scale * 0.018)))


def _clock(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    radius = min(width, height) * 0.27
    cx, cy = width * 0.50, height * 0.48
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 255, 252, 255), outline=(45, 126, 120, 255), width=max(7, int(radius * 0.05)))
    for index in range(12):
        angle = math.pi * 2 * index / 12 - math.pi / 2
        inner = radius * 0.82
        outer = radius * 0.94
        draw.line((cx + math.cos(angle) * inner, cy + math.sin(angle) * inner, cx + math.cos(angle) * outer, cy + math.sin(angle) * outer), fill=(45, 126, 120, 220), width=max(3, int(radius * 0.018)))
    draw.line((cx, cy, cx, cy - radius * 0.55), fill=(234, 110, 91, 255), width=max(8, int(radius * 0.045)))
    draw.line((cx, cy, cx + radius * 0.48, cy), fill=(45, 126, 120, 255), width=max(8, int(radius * 0.045)))
    draw.ellipse((cx - radius * 0.055, cy - radius * 0.055, cx + radius * 0.055, cy + radius * 0.055), fill=(45, 126, 120, 255))
    _arrow(draw, width * 0.26, height * 0.82, width * 0.74, height * 0.82, (45, 184, 174, 220), max(8, int(radius * 0.04)))


def _pause_brush(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    scale = min(width, height) * 0.48
    x0, y0 = width * 0.12, height * 0.42
    x1 = width * 0.72
    draw.rounded_rectangle((x0, y0, x1, y0 + scale * 0.13), radius=int(scale * 0.06), fill=(45, 184, 174, 255))
    draw.rounded_rectangle((width * 0.60, y0 - scale * 0.05, width * 0.88, y0 + scale * 0.18), radius=int(scale * 0.05), fill=(240, 244, 244, 255), outline=(45, 126, 120, 255))
    cx, cy = width * 0.50, height * 0.67
    r = scale * 0.19
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 252, 230), outline=(235, 103, 91, 255), width=max(7, int(r * 0.08)))
    bar = r * 0.25
    draw.rounded_rectangle((cx - bar * 1.8, cy - r * 0.52, cx - bar * 0.4, cy + r * 0.52), radius=int(bar * 0.35), fill=(235, 103, 91, 255))
    draw.rounded_rectangle((cx + bar * 0.4, cy - r * 0.52, cx + bar * 1.8, cy + r * 0.52), radius=int(bar * 0.35), fill=(235, 103, 91, 255))


def _simple_steps(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    centres = [(width * 0.22, height * 0.50), (width * 0.50, height * 0.50), (width * 0.78, height * 0.50)]
    colours = [(244, 153, 92, 255), (45, 184, 174, 255), (65, 126, 176, 255)]
    radius = min(width, height) * 0.095
    for index, ((cx, cy), colour) in enumerate(zip(centres, colours)):
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
        if index < len(centres) - 1:
            _arrow(draw, cx + radius * 1.3, cy, centres[index + 1][0] - radius * 1.3, cy, (48, 113, 109, 190), max(6, int(radius * 0.08)))


def _arrow(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, colour: tuple[int, int, int, int], width: int) -> None:
    draw.line((x0, y0, x1, y1), fill=colour, width=width)
    angle = math.atan2(y1 - y0, x1 - x0)
    size = width * 2.2
    left = (x1 - math.cos(angle - 0.6) * size, y1 - math.sin(angle - 0.6) * size)
    right = (x1 - math.cos(angle + 0.6) * size, y1 - math.sin(angle + 0.6) * size)
    draw.polygon([(x1, y1), left, right], fill=colour)


def _dots(draw: ImageDraw.ImageDraw, width: int, height: int, points: list[tuple[float, float]], colour: tuple[int, int, int, int], size: float) -> None:
    radius = min(width, height) * size
    for px, py in points:
        cx, cy = width * px, height * py
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
