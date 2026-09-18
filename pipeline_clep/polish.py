"""
Polish: Screen-Studio-style edit of the agent's raw screen recording.

Model (matches the reference): the app window floats on a gradient canvas.
The camera scales + pans the whole window — zoomed out on the backdrop at
open, eased push into the action, pull back out on the backdrop to close.
No cropped-to-pixels framing, no pills/rings: just window, cursor, ripple.

Single-pass stream: Playwright webm -> decode pipe -> Pillow edit ->
encode pipe. Output fps is independent of source fps (default 60: the
window transform + cursor interpolate per output frame, butter-smooth
even when the capture is 25fps).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

import config
import storyboard
from renderer import _draw_cursor, _track


def _load_trace(trace_path: Path) -> dict:
    return json.loads(Path(trace_path).read_text())


# Max window zoom per movement. calm holds the full window on the canvas
# (no text cropped); dynamic keeps the old punchy push for feature demos.
MOVEMENT_MAX_ZOOM = {"calm": 1.10, "standard": 1.35, "dynamic": 1.65}


def _cap_zoom(keys: list[dict], movement: str | None) -> list[dict]:
    if not movement or movement not in MOVEMENT_MAX_ZOOM:
        return keys
    cap = MOVEMENT_MAX_ZOOM[movement]
    return [{**k, "zoom": min(float(k.get("zoom", 1.0)), cap)} for k in keys]


def _plan_from_trace(trace: dict, aspect: str | None, style: str | None,
                     duration: float | None, movement: str | None = None) -> dict:
    vw, vh = trace["viewport"]["w"], trace["viewport"]["h"]
    el = trace["element"]
    ecx = min(max(el["x"] + el["w"] / 2, 0.05), 0.95)
    ecy = min(max(el["y"] + el["h"] / 2, 0.05), 0.95)

    trim = float(trace.get("trim_start", 0.0))
    raw_dur = float(trace["duration"]) - trim
    dur = float(duration or min(max(raw_dur, 2.0), 60.0))

    feature_like = {
        "name": trace["name"], "title": trace.get("title", trace["name"]),
        "url": trace.get("url", ""), "element": el,
        "steps": [
            {"type": "type", "text": trace.get("query", "")},
            {"type": "click", "target": trace.get("button_label", "")},
            {"type": "loading", "label": "Working…"},
            {"type": "result", "label": "Done"},
        ],
    }
    plan = storyboard.build_plan(feature_like, duration=dur, aspect=aspect, style=style)
    movement = movement or ("calm" if trace.get("kind") == "clep-tour" else "standard")

    if trace.get("kind") == "clep-tour":
        # Tour camera: hold the whole window wide. The source footage
        # already scrolls through sections — any push-in would crop text
        # and fight the scroll (the bug in the old portfolio clips).
        # Just a barely-there drift so the frame feels alive.
        drift = {"calm": 1.05, "standard": 1.08, "dynamic": 1.12}.get(movement, 1.05)
        plan["camera"] = [
            {"t": 0.00, "cx": 0.5, "cy": 0.5, "zoom": 1.0},
            {"t": 0.12, "cx": 0.5, "cy": 0.5, "zoom": 1.0},
            {"t": 0.88, "cx": 0.5, "cy": 0.5, "zoom": drift},
            {"t": 1.00, "cx": 0.5, "cy": 0.5, "zoom": drift},
        ]
        plan["movement"] = movement
        plan["is_tour"] = True
    else:
        # Camera follows the ACTION CHAIN, not just the bbox: establish on the
        # page -> one eased key per recorded focus/click (typing, buttons across
        # states) -> settle on the whole element for the result.
        el_c = {"x": ecx, "y": ecy}
        focuses = list(trace.get("focuses") or [])
        if not focuses:
            # legacy traces: fall back to input/button points
            if trace.get("input_xy"):
                focuses.append({"t": dur * 0.30, **trace["input_xy"]})
            if trace.get("button_xy"):
                focuses.append({"t": dur * 0.50, **trace["button_xy"]})
        mids = []
        for fc in focuses:
            t = (float(fc.get("t", 0)) - trim) / dur
            if 0.03 <= t < 0.78:
                mids.append({"t": t, "x": float(fc["x"]), "y": float(fc["y"])})
        mids.sort(key=lambda m: m["t"])
        if len(mids) > 4:  # spread cap: evenly subsample, keep first+last
            idx = [round(i * (len(mids) - 1) / 3) for i in range(4)]
            mids = [mids[i] for i in dict.fromkeys(idx)]
        keys = []
        for m in mids:
            if not keys or m["t"] - keys[-1]["t"] >= 0.05:
                keys.append(m)
        push = min(plan["camera"][1]["zoom"], MOVEMENT_MAX_ZOOM.get(movement, 1.35))
        plan["camera"] = _cap_zoom(
            [{"t": 0.00, "cx": 0.5, "cy": 0.5, "zoom": 1.0}]
            + [{"t": m["t"], "cx": m["x"], "cy": m["y"], "zoom": push} for m in keys]
            + [{"t": 0.86, "cx": ecx, "cy": ecy, "zoom": push},
               {"t": 0.94, "cx": 0.5, "cy": 0.5, "zoom": 1.0},
               {"t": 1.00, "cx": 0.5, "cy": 0.5, "zoom": 1.0}],
            movement,
        )
        plan["movement"] = movement
        plan["is_tour"] = False

    # Cursor: real traced path, shifted by trim, normalized to output duration.
    pts = []
    for c in trace.get("cursor", []):
        t = float(c["t"]) - trim
        if t < 0:
            continue
        pts.append({"t": min(t / dur, 1.0), "x": float(c["x"]), "y": float(c["y"])})
    if not pts:
        pts = [{"t": 0.0, "x": 0.5, "y": 0.5}, {"t": 1.0, "x": 0.5, "y": 0.5}]
    if pts[0]["t"] > 0:
        pts.insert(0, {"t": 0.0, "x": pts[0]["x"], "y": pts[0]["y"]})
    plan["cursor"] = pts

    click = trace.get("click") or {}
    plan["click_at"] = min(max(float(click.get("t", dur * 0.5)) - trim, 0.2), dur - 0.4)
    plan["click_xy"] = {"x": float(click.get("x", 0.5)), "y": float(click.get("y", 0.5))}
    # Every recorded click gets its own ripple.
    plan["clicks"] = [
        {"t": float(c.get("t", 0)) - trim, "x": float(c.get("x", 0.5)), "y": float(c.get("y", 0.5))}
        for c in (trace.get("clicks") or ([click] if click else []))
    ]
    # Tour captions, shifted by trim into output seconds.
    caps = []
    for c in (trace.get("captions") or []):
        try:
            t0, t1 = float(c.get("t0", 0)) - trim, float(c.get("t1", 0)) - trim
        except (TypeError, ValueError):
            continue
        if t1 <= 0.1 or t0 >= dur - 0.1:
            continue
        caps.append({"t0": max(t0, 0.1), "t1": min(t1, dur - 0.1),
                     "text": str(c.get("text", ""))[:70]})
    plan["captions"] = caps
    plan["title"] = trace.get("title") or plan.get("title", "")
    plan["element_center"] = {"x": ecx, "y": ecy}
    plan["trim_start"] = trim
    return plan


def _probe_src_fps(video: str) -> float:
    try:
        out = subprocess.run(
            [config.FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", video],
            capture_output=True, text=True, check=True)
        num, den = out.stdout.strip().split("/")
        return float(num) / float(den) if float(den) else 25.0
    except Exception:
        return 25.0


def _backdrop(W: int, H: int, style: str, bg: str | None = None) -> Image.Image:
    """Static 3-stop diagonal gradient canvas (rendered once per clip).

    bg overrides the style preset: BACKDROPS name, custom "#a,#b[,#c]"
    gradient, or "solid:#hex" (see config.resolve_bg).
    """
    c0, c1, c2 = config.resolve_bg(bg, style)
    tw, th = 48, 27
    tiny = Image.new("RGB", (tw, th))
    px = tiny.load()
    for y in range(th):
        for x in range(tw):
            dd = ((x / (tw - 1)) + (y / (th - 1))) / 2
            if dd < 0.5:
                e = dd * 2
                a, b = c0, c1
            else:
                e = (dd - 0.5) * 2
                a, b = c1, c2
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * e) for i in range(3))
    return tiny.resize((W, H), Image.BILINEAR)


def _draw_caption(d: ImageDraw.ImageDraw, W: int, H: int, text: str, alpha: float):
    """Bottom-center section title, constant size regardless of camera zoom."""
    from PIL import ImageFont
    if not text or alpha <= 0:
        return
    try:
        font = ImageFont.truetype(config.FONT_BOLD, max(12, int(H * 0.024)))
    except Exception:
        font = ImageFont.load_default()
    label = text[:56]
    try:
        bb = font.getbbox(label)
        tw = bb[2] - bb[0]
    except Exception:
        tw = len(label) * 10
    pw_, ph_ = tw + int(W * 0.04), int(H * 0.058)
    px0 = W // 2 - pw_ // 2
    py0 = int(H * 0.895) - ph_ // 2
    a = max(0.0, min(1.0, alpha))
    _pill = max(2, ph_ // 2)
    d.rounded_rectangle([px0, py0, px0 + pw_, py0 + ph_], radius=_pill,
                        fill=(10, 10, 14, int(165 * a)))
    d.rounded_rectangle([px0, py0, px0 + pw_, py0 + ph_], radius=_pill,
                        outline=(255, 255, 255, int(70 * a)), width=1)
    try:
        tb = font.getbbox(label)
        d.text((px0 + (pw_ - tw) // 2, py0 + ph_ // 2 - (tb[3] + tb[1]) // 2),
               label, font=font, fill=(255, 255, 255, int(235 * a)))
    except Exception:
        pass


def polish(trace_path: Path, out_path: Path, aspect: str | None = None,
           style: str | None = None, duration: float | None = None,
           fps: int = 60, crf: int = 18, quality: str | None = None,
           movement: str | None = None, captions: bool = True,
           size: str | None = None, bg: str | None = None) -> Path:
    trace = _load_trace(trace_path)
    video = trace.get("video")
    if not video or not Path(video).exists():
        raise RuntimeError(f"[polish] raw video missing: {video} — re-run agent.record()")
    plan = _plan_from_trace(trace, aspect, style, duration, movement=movement)
    dur = plan["duration"]
    n = int(round(dur * fps))

    W, H = config.canvas(plan["aspect"], quality, size)
    vw, vh = trace["viewport"]["w"], trace["viewport"]["h"]
    src_fps = _probe_src_fps(video)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Single-pass stream: Playwright webm -> decode pipe -> Pillow edit ->
    # encode pipe. Source decoded at its native rate; output frames sample
    # the latest source frame, so 60fps output stays smooth.
    frame_bytes = vw * vh * 3
    dec = subprocess.Popen(
        [config.FFMPEG_BIN, "-v", "error",
         "-ss", f"{plan['trim_start']:.3f}", "-i", video,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{vw}x{vh}",
         "-r", f"{src_fps:.3f}", "-t", f"{dur + 0.4:.3f}", "-"],
        stdout=subprocess.PIPE)

    def _read_frame() -> bytes | None:
        buf = bytearray()
        while len(buf) < frame_bytes:
            chunk = dec.stdout.read(frame_bytes - len(buf))
            if not chunk:
                break
            buf += chunk
        return bytes(buf) if len(buf) == frame_bytes else None

    # Fail fast: prove the decode flows before starting the encoder.
    first = _read_frame()
    if first is None:
        dec.terminate()
        raise RuntimeError("[polish] decoder produced no frames — is the raw webm valid?")

    enc = subprocess.Popen(
        [config.FFMPEG_BIN, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)],
        stdin=subprocess.PIPE)

    from PIL import Image as _I
    bg_img = _backdrop(W, H, plan["style"], bg).convert("RGBA")
    padX, padY = int(W * config.WINDOW_PAD_X_FRAC), int(H * config.WINDOW_PAD_Y_FRAC)
    s0 = min((W - 2 * padX) / vw, (H - 2 * padY) / vh)
    rad = max(8, int(config.WINDOW_RADIUS * W / 1280))
    pending = [first]  # first frame already pulled to prove the decode flows
    last = None
    have = -1  # index of latest consumed source frame
    for fi in range(n):
        t = min(fi / fps, dur - 1e-4)
        f = t / dur
        # Advance the source to the latest frame at time t (hold on EOF).
        need = int(t * src_fps)
        while have < need:
            raw = pending.pop(0) if pending else _read_frame()
            have += 1
            if raw is None:
                break
            last = _I.frombytes("RGB", (vw, vh), raw)
        if last is None:
            raise RuntimeError("[polish] no frames decoded from raw video")
        frame = last

        cam = _track(plan["camera"], f, ["cx", "cy", "zoom"])
        cur = _track(plan["cursor"], f, ["x", "y"])

        # Floating window: scale whole capture, pin the focus to canvas center.
        s = s0 * cam["zoom"]
        sw, sh = max(2, int(vw * s)), max(2, int(vh * s))
        win = frame.resize((sw, sh), Image.BICUBIC)
        mask = Image.new("L", (sw, sh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw, sh], radius=rad, fill=255)
        ox, oy = W / 2 - cam["cx"] * vw * s, H / 2 - cam["cy"] * vh * s

        canvas = bg_img.copy()
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shd = ImageDraw.Draw(shade)
        for dy, al in ((30, 46), (16, 66), (7, 88)):
            shd.rounded_rectangle([ox, oy + dy, ox + sw, oy + sh + dy],
                                  radius=rad, fill=(12, 10, 24, al))
        canvas = Image.alpha_composite(canvas, shade)
        canvas.paste(win, (int(ox), int(oy)), mask)
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([ox, oy, ox + sw, oy + sh], radius=rad,
                            outline=(255, 255, 255, 110), width=max(2, int(W * 0.002)))

        # Cursor in canvas space (constant size, like the reference).
        # Tours park the cursor: a visible arrow chasing a scroll is noise.
        if not plan.get("is_tour"):
            px_, py_ = ox + cur["x"] * vw * s, oy + cur["y"] * vh * s
            if -60 <= px_ <= W + 60 and -60 <= py_ <= H + 60:
                cfx = 0.0
                for ck in plan.get("clicks", []):
                    if abs(t - ck["t"]) < 0.30:
                        cfx = max(cfx, 1 - abs(t - ck["t"]) / 0.30)
                _draw_cursor(d, int(px_), int(py_), max(14, int(config.CURSOR_SIZE * W / 1280)), cfx)

        if captions:
            for cap in plan.get("captions", []):
                fade = 0.35
                a_in = max(0.0, min(1.0, (t - cap["t0"]) / fade))
                a_out = max(0.0, min(1.0, (cap["t1"] - t) / fade))
                a = min(a_in, a_out)
                if a > 0:
                    _draw_caption(d, W, H, cap["text"], a)
                    break

        enc.stdin.write(canvas.convert("RGB").tobytes())

    try:
        dec.stdout.close()
    except Exception:
        pass
    dec.wait(timeout=30)
    enc.stdin.close()
    enc.wait()
    if enc.returncode != 0:
        raise RuntimeError("[polish] encode failed")
    print(f"[polish] done: {out_path} ({dur:.1f}s {W}x{H} {plan['style']}, from real capture)")
    return out_path
