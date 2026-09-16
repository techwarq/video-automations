"""
Motion-graphics engine: renders "diagram" beats as animated hand-drawn
explainer clips — boxes, arrows and labels that draw themselves on, in a
whiteboard ("whiteboard" style: dark ink on off-white) or dark-board
("dark" style: white ink on the brand background) look. No photos needed.

A beat opts in with visual_kind="diagram" and a `diagram` spec (authored by
planner.py's LLM or by hand):

    "diagram": {
      "style": "dark" | "whiteboard",
      "nodes":   [{"id","label","x","y","w","h",          # x,y = center fractions
                   "shape":"rect|oval|plain", "emph":bool}],
      "arrows":  [{"from","to","label", "path":"straight|elbow"}],
      "callouts":[{"text","x","y","big":bool,"emph":bool}]
    }

Animation model: elements appear in listed order across the first
GFX_DRAW_FRACTION of the beat (boxes/arrows literally draw on stroke-by-
stroke, text fades+rises in), then the finished diagram holds. Wobble lines
re-jitter every few frames ("boil") so stills read as hand-drawn, and the
whole frame is rendered at GFX_SUPERSAMPLE x and downscaled for clean
anti-aliased strokes (PIL's line drawing is unaliased otherwise).

Output is an H.264 clip at exact card size / pipeline FPS, cached in
cache/beat_{id}/ — compositor.py swaps it in as the card input instead of a
zoompan'd still.
"""

import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config


# ── small helpers ───────────────────────────────────────────────────────

_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(path, size)
    return _FONTS[key]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _wobble_pts(p0, p1, seed: float, amp: float, n: int = 14) -> list[tuple[float, float]]:
    """Straight line p0->p1 sampled with a deterministic sinusoidal wobble."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length  # unit normal
    pts = []
    for i in range(n + 1):
        t = i / n
        # two out-of-phase sines seeded per-element -> stable, organic wobble
        off = amp * (math.sin(t * 9.0 + seed * 7.3) * 0.6 + math.sin(t * 23.0 + seed * 3.1) * 0.4)
        pts.append((x0 + dx * t + nx * off, y0 + dy * t + ny * off))
    return pts


def _partial(pts: list, p: float) -> list:
    """First p-fraction of a polyline's arc length (for draw-on)."""
    if p >= 1.0 or len(pts) < 2:
        return pts
    seg_lens = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg_lens) or 1.0
    target = total * _clamp01(p)
    out = [pts[0]]
    acc = 0.0
    for i, seg in enumerate(seg_lens):
        if acc + seg >= target:
            t = (target - acc) / (seg or 1.0)
            a, b = pts[i], pts[i + 1]
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        acc += seg
        out.append(pts[i + 1])
    return out


def _roundrect_perimeter(x0, y0, x1, y1, r, seed, amp) -> list:
    """Wobbly rounded-rectangle perimeter as one polyline (for draw-on)."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    # Build: top edge L->R, TR corner arc, right edge T->B, BR arc,
    # bottom edge R->L, BL arc, left edge B->T, TL arc.
    pts: list[tuple[float, float]] = []

    def arc(cx, cy, a_from, a_to):
        for i in range(1, 7):
            a = a_from + (a_to - a_from) * i / 6
            pts.append((cx + r * math.cos(a) + amp * 0.4 * math.sin(a * 3 + seed),
                        cy + r * math.sin(a) + amp * 0.4 * math.cos(a * 2 + seed)))

    pts.extend(_wobble_pts((x0 + r, y0), (x1 - r, y0), seed, amp))
    arc(x1 - r, y0 + r, -math.pi / 2, 0)
    pts.extend(_wobble_pts((x1, y0 + r), (x1, y1 - r), seed + 1, amp))
    arc(x1 - r, y1 - r, 0, math.pi / 2)
    pts.extend(_wobble_pts((x1 - r, y1), (x0 + r, y1), seed + 2, amp))
    arc(x0 + r, y1 - r, math.pi / 2, math.pi)
    pts.extend(_wobble_pts((x0, y1 - r), (x0, y0 + r), seed + 3, amp))
    arc(x0 + r, y0 + r, math.pi, 1.5 * math.pi)
    return pts


def _oval_perimeter(cx, cy, rx, ry, seed, amp, n: int = 48) -> list:
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        w = 1 + amp / max(rx, 1) * math.sin(a * 5 + seed * 5.0) * 2
        pts.append((cx + rx * w * math.cos(a), cy + ry * w * math.sin(a)))
    pts.append(pts[0])
    return pts


def _arrow_head(draw, tip, direction, size, color, width):
    x, y = tip
    a = direction
    for sign in (-1, 1):
        b = a + sign * math.radians(152)
        draw.line([tip, (x + size * math.cos(b), y + size * math.sin(b))],
                  fill=color, width=width, joint="curve")


def _fit_label(text: str, font_path: str, base_size: int, max_w: int, max_h: int, line_spacing: int = 4):
    """Shrink/wrap a node label to fit its box. Returns (text, fontsize)."""
    def width_of(s, size):
        f = _font(font_path, size)
        return max(f.getbbox(l)[2] - f.getbbox(l)[0] for l in s.split("\n"))

    def height_of(s, size):
        f = _font(font_path, size)
        return len(s.split("\n")) * (f.getbbox("Ag")[3] - f.getbbox("Ag")[1] + f.size // 3) + line_spacing

    size = base_size
    while size > 14:
        if width_of(text, size) <= max_w and height_of(text, size) <= max_h:
            return text, size
        if "\n" not in text and width_of(text, size) > max_w and len(text.split()) > 1:
            words = text.split()
            mid = len(words) // 2
            text = f"{' '.join(words[:mid])}\n{' '.join(words[mid:])}"
            continue
        size -= 2
    return text, max(size, 14)


# ── element scheduling ──────────────────────────────────────────────────

_COST = {"node": 1.0, "arrow": 0.85, "callout": 0.55}


def _schedule(elements: list[dict], duration: float) -> None:
    """Assigns each element a (start, end) draw window within the beat."""
    draw_window = duration * config.GFX_DRAW_FRACTION
    total_cost = sum(_COST[e["kind"]] for e in elements) or 1.0
    cursor = 0.0
    for e in elements:
        slot = _COST[e["kind"]] / total_cost * draw_window
        # start where the previous element began its last 40% — keeps the
        # hand moving in a continuous flow rather than stop-start.
        e["t0"] = max(0.0, cursor - slot * 0.25)
        e["t1"] = min(duration, e["t0"] + slot * 1.45)
        cursor += slot
        e["t1"] = min(e["t1"], draw_window * 1.05)


# ── per-frame drawing ───────────────────────────────────────────────────

def _draw_node(draw, node, p: float, colors, S: dict):
    x, y = node["x"] * S["w"], node["y"] * S["h"]
    w, h = node["w"] * S["w"], node["h"] * S["h"]
    ink = colors["accent"] if node.get("emph") else colors["ink"]
    lw = S["stroke"]
    seed = node["_seed"]

    if node["shape"] != "plain":
        box_p = _clamp01(p / 0.7)  # box draws on during first 70% of the slot
        if node["shape"] == "oval":
            perim = _oval_perimeter(x, y, w / 2, h / 2, seed, S["wobble"])
        else:
            perim = _roundrect_perimeter(x - w / 2, y - h / 2, x + w / 2, y + h / 2,
                                         r=min(w, h) * 0.22, seed=seed, amp=S["wobble"])
        seg = _partial(perim, box_p)
        if len(seg) > 1:
            draw.line(seg, fill=ink, width=lw, joint="curve")

        text_p = _clamp01((p - 0.5) / 0.5)
        if text_p > 0 and node["label"]:
            label, fs = _fit_label(node["label"], config.GFX_NODE_FONT,
                                   S["node_fontsize"], int(w * 0.82), int(h * 0.8))
            f = _font(config.GFX_NODE_FONT, fs)
            alpha = int(255 * text_p)
            lines = label.split("\n")
            bbox = f.getbbox("Ag")
            line_h = bbox[3] - bbox[1] + f.size // 3
            total_h = line_h * len(lines)
            for li, line in enumerate(lines):
                lw_px = f.getbbox(line)[2] - f.getbbox(line)[0]
                tx = x - lw_px / 2
                ty = y - total_h / 2 + li * line_h
                draw.text((tx, ty), line, font=f,
                          fill=ink + (alpha,), stroke_width=max(1, lw // 3),
                          stroke_fill=ink + (0,))
    else:
        # plain node = borderless label
        text_p = _clamp01(p / 0.8)
        if text_p > 0 and node["label"]:
            label, fs = _fit_label(node["label"], config.GFX_NODE_FONT,
                                   S["node_fontsize"], int(w * 0.9), int(h * 0.9))
            f = _font(config.GFX_NODE_FONT, fs)
            alpha = int(255 * text_p)
            bbox = f.getbbox(node["label"])
            draw.text((x - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) / 2),
                      node["label"], font=f, fill=ink + (alpha,))


def _node_anchor(node, toward_xy, S: dict) -> tuple[tuple[float, float], str]:
    """
    Edge midpoint of a node facing the target point (card fractions).
    Returns ((x, y), edge) where edge is "v" (exited via left/right edge)
    or "h" (exited via top/bottom edge).
    """
    cx, cy = node["x"], node["y"]
    tx, ty = toward_xy
    dx, dy = tx - cx, ty - cy
    if node["shape"] == "plain":
        return (cx, cy), "h"
    hw, hh = node["w"] / 2, node["h"] / 2
    if abs(dx) * S["h"] > abs(dy) * S["w"]:
        return (cx + (hw if dx > 0 else -hw), cy), "v"
    return (cx, cy + (hh if dy > 0 else -hh)), "h"


def _spread_along_edge(pt, edge, spread, node):
    """Slide an anchor point along its node edge by `spread` (fraction of the
    edge length, clamped) so parallel arrows don't share one endpoint."""
    frac_w, frac_h = node["w"], node["h"]
    if edge == "v":
        return (pt[0], min(max(pt[1] + spread * frac_h, node["y"] - frac_h * 0.35),
                           node["y"] + frac_h * 0.35))
    return (min(max(pt[0] + spread * frac_w, node["x"] - frac_w * 0.35),
                node["x"] + frac_w * 0.35), pt[1])


def _draw_arrow(draw, arrow, nodes_by_id, p: float, colors, S: dict):
    src = nodes_by_id.get(arrow["from"])
    dst = nodes_by_id.get(arrow["to"])
    if not src or not dst:
        return
    spread_out = arrow.get("_spread_out", 0.0)
    spread_in = arrow.get("_spread_in", 0.0)
    a_frac, a_edge = _node_anchor(src, (dst["x"], dst["y"]), S)
    b_frac, b_edge = _node_anchor(dst, (src["x"], src["y"]), S)
    a_frac = _spread_along_edge(a_frac, a_edge, spread_out, src)
    b_frac = _spread_along_edge(b_frac, b_edge, spread_in, dst)
    a = (a_frac[0] * S["w"], a_frac[1] * S["h"])
    b = (b_frac[0] * S["w"], b_frac[1] * S["h"])
    seed = arrow["_seed"]
    ink = colors["ink"]
    lw = S["stroke"]

    if arrow.get("path") == "elbow":
        # Bend x biased off-center so mirrored arrows don't overlap.
        midx = a[0] + (b[0] - a[0]) * arrow.get("_bias", 0.5)
        pts = (_wobble_pts(a, (midx, a[1]), seed, S["wobble"])
               + _wobble_pts((midx, a[1]), (midx, b[1]), seed + 1, S["wobble"])[1:]
               + _wobble_pts((midx, b[1]), b, seed + 2, S["wobble"])[1:])
    else:
        pts = _wobble_pts(a, b, seed, S["wobble"])

    shaft_p = _clamp01(p / 0.85)
    seg = _partial(pts, shaft_p)
    if len(seg) > 1:
        draw.line(seg, fill=ink, width=lw, joint="curve")
    if p > 0.85:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        _arrow_head(draw, (x1, y1), math.atan2(y1 - y0, x1 - x0),
                    size=S["stroke"] * 3.4, color=ink, width=lw)

    label = arrow.get("label")
    if label and p > 0.6:
        text_p = _clamp01((p - 0.6) / 0.4)
        mid = pts[len(pts) // 2]
        # When this arrow shares its node with a sibling, nudge the label
        # AWAY from the canvas center so mirrored labels don't collide.
        spread = arrow.get("_spread_out") or arrow.get("_spread_in") or 0.0
        if spread:
            away = 1.0 if mid[0] >= S["w"] / 2 else -1.0
            mid = (mid[0] + away * abs(spread) * S["w"] * 0.35, mid[1])
        fs = max(14, int(S["node_fontsize"] * 0.72))
        f = _font(config.GFX_NODE_FONT, fs)
        bbox = f.getbbox(label)
        alpha = int(255 * text_p)
        draw.text((mid[0] - (bbox[2] - bbox[0]) / 2, mid[1] - (bbox[3] - bbox[1]) / 2 - S["stroke"] * 2),
                  label, font=f, fill=colors["secondary"] + (alpha,),
                  stroke_width=S["stroke"] + 2, stroke_fill=colors["bg"] + (alpha,))


def _draw_callout(draw, callout, p: float, colors, S: dict):
    text_p = _clamp01(p / 0.8)
    if text_p <= 0:
        return
    fs = int(S["card_h"] * (config.GFX_STAT_FONTSIZE_FRAC if callout.get("big") else config.GFX_CALLOUT_FONTSIZE_FRAC))
    fs = max(16, fs)
    f = _font(config.GFX_STAT_FONT if callout.get("big") else config.GFX_NODE_FONT, fs)
    text = callout["text"]
    bbox = f.getbbox(text)
    x = callout["x"] * S["w"] - (bbox[2] - bbox[0]) / 2
    y = callout["y"] * S["h"] - (bbox[3] - bbox[1]) / 2 - (1 - text_p) * S["card_h"] * 0.02
    color = colors["accent"] if callout.get("emph") else colors["ink"]
    alpha = int(255 * text_p)
    draw.text((x, y), text, font=f, fill=color + (alpha,),
              stroke_width=max(1, S["stroke"] // 2), stroke_fill=color + (0,))


# ── main renderer ───────────────────────────────────────────────────────

def render_diagram_clip(beat: dict, card_w: int, card_h: int, duration: float,
                        out_dir: Path | None = None) -> Path:
    """Renders (cached) the diagram beat to an H.264 clip at card size."""
    spec = beat["diagram"]
    fps = config.FPS
    key_src = json.dumps(spec, sort_keys=True) + f"|{card_w}x{card_h}|{duration:.3f}|{fps}"
    clip_hash = hashlib.md5(key_src.encode()).hexdigest()[:10]
    out_dir = out_dir or (config.CACHE_DIR / f"beat_{beat['id']}")
    out_path = out_dir / f"gfx_{clip_hash}.mp4"
    if out_path.exists():
        return out_path
    out_dir.mkdir(parents=True, exist_ok=True)

    style = config.GFX_STYLES.get(spec.get("style"), config.GFX_STYLES[config.GFX_DEFAULT_STYLE])
    ss = config.GFX_SUPERSAMPLE
    W, H = card_w * ss, card_h * ss
    S = {
        "w": W, "h": H, "card_h": H,
        "stroke": max(2, round(card_w * config.GFX_STROKE_W_FRAC)) * ss,
        "node_fontsize": max(14, int(card_h * config.GFX_NODE_FONTSIZE_FRAC)) * ss,
        "wobble": card_w * config.GFX_WOBBLE_AMP_FRAC * ss,
    }

    # Flatten + validate elements into draw order.
    elements: list[dict] = []
    nodes_by_id: dict[str, dict] = {}
    for i, n in enumerate(spec.get("nodes", [])):
        node = {
            "kind": "node", "id": n.get("id", f"n{i}"),
            "label": str(n.get("label", "")),
            "x": _clamp01(float(n.get("x", 0.5))), "y": _clamp01(float(n.get("y", 0.5))),
            "w": _clamp01(float(n.get("w", 0.26))), "h": _clamp01(float(n.get("h", 0.2))),
            "shape": n.get("shape", "rect") if n.get("shape") in ("rect", "oval", "plain") else "rect",
            "emph": bool(n.get("emph")), "_seed": i * 1.7 + 0.3,
        }
        nodes_by_id[node["id"]] = node
        elements.append(node)
    for j, a in enumerate(spec.get("arrows", [])):
        elements.append({
            "kind": "arrow", "from": a.get("from"), "to": a.get("to"),
            "label": (str(a["label"]) if a.get("label") else None),
            "path": a.get("path", "straight"),
            # Parallel arrows on one node get offset anchor points so they
            # don't stack; single-connection nodes stay centered.
            "_bias": 0.5 + (0.18 if j % 2 == 0 else -0.18),
            "_seed": j * 2.3 + 11.0,
        })
    # Degree counts -> per-arrow spread flags (only spread when needed).
    out_deg: dict[str, int] = {}
    in_deg: dict[str, int] = {}
    for e in elements:
        if e["kind"] == "arrow":
            out_deg[e["from"]] = out_deg.get(e["from"], 0) + 1
            in_deg[e["to"]] = in_deg.get(e["to"], 0) + 1
    for e in elements:
        if e["kind"] == "arrow":
            bias = e["_bias"] - 0.5
            e["_spread_out"] = bias if out_deg.get(e["from"], 0) > 1 else 0.0
            e["_spread_in"] = bias if in_deg.get(e["to"], 0) > 1 else 0.0
    for k, c in enumerate(spec.get("callouts", [])):
        elements.append({
            "kind": "callout", "text": str(c.get("text", "")),
            "x": _clamp01(float(c.get("x", 0.5))), "y": _clamp01(float(c.get("y", 0.85))),
            "big": bool(c.get("big")), "emph": bool(c.get("emph")),
            "_seed": k,
        })
    if not elements:
        raise ValueError(f"beat {beat['id']}: diagram spec has no nodes/arrows/callouts")
    _schedule(elements, duration)

    frames = max(2, round(duration * fps))
    bg = Image.new("RGB", (W, H), style["bg"])
    boil = config.GFX_BOIL and config.GFX_BOIL_EVERY_N_FRAMES

    enc = subprocess.Popen(
        [config.FFMPEG_BIN, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{card_w}x{card_h}",
         "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "fast", "-crf", "17",
         "-pix_fmt", "yuv420p", str(out_path)],
        stdin=subprocess.PIPE,
    )
    for fi in range(frames):
        t = fi / fps
        boil_seed_shift = (fi // config.GFX_BOIL_EVERY_N_FRAMES) * 13.7 if boil else 0.0
        frame = bg.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for e in elements:
            p = _clamp01((t - e["t0"]) / max(e["t1"] - e["t0"], 1e-6))
            if p <= 0:
                continue
            if e["kind"] == "node":
                _draw_node(draw, {**e, "_seed": e["_seed"] + boil_seed_shift}, p, style, S)
            elif e["kind"] == "arrow":
                _draw_arrow(draw, {**e, "_seed": e["_seed"] + boil_seed_shift}, nodes_by_id, p, style, S)
            else:
                _draw_callout(draw, e, p, style, S)
        frame = Image.alpha_composite(frame, overlay).convert("RGB")
        enc.stdin.write(frame.resize((card_w, card_h), Image.LANCZOS).tobytes())

    enc.stdin.close()
    enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"motion_gfx: ffmpeg encode failed for beat {beat['id']} ({out_path})")
    print(f"[motion_gfx] beat {beat['id']}: rendered {frames}f diagram -> {out_path}")
    return out_path
