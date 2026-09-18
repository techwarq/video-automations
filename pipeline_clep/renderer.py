"""
Renderer: plan -> MP4. Single continuous camera take over a synthetic
(or screenshot) app mock, with cursor, typing, click, loading, result.

No Remotion / browser needed — Pillow frames piped to ffmpeg (same
rawvideo pattern as pipeline_motion/motion_gfx.py).
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from storyboard import phase_at

_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONTS:
        try:
            _FONTS[key] = ImageFont.truetype(path, size)
        except Exception:
            _FONTS[key] = ImageFont.truetype(config.FONT_REGULAR, size)
    return _FONTS[key]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _ease_in_out(t: float) -> float:
    t = _clamp01(t)
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def _ease_out(t: float) -> float:
    t = _clamp01(t)
    return 1 - pow(1 - t, 3)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * _clamp01(t)


def _track(keys: list[dict], f: float, fields: list[str]) -> dict:
    """Piecewise easing interpolation over keyframes with normalized t."""
    if f <= keys[0]["t"]:
        return {k: keys[0][k] for k in fields}
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a["t"] <= f <= b["t"]:
            span = max(b["t"] - a["t"], 1e-6)
            e = _ease_in_out((f - a["t"]) / span)
            return {k: _lerp(a[k], b[k], e) for k in fields}
    return {k: keys[-1][k] for k in fields}


def _text_w(text: str, font) -> int:
    bb = font.getbbox(text)
    return bb[2] - bb[0]


def _rr(draw: ImageDraw.ImageDraw, xy, radius: int, fill=None, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


# ── Base app (synthetic browser mock, resolution-independent) ──────────────

def _render_base_app(plan: dict, W: int, H: int) -> Image.Image:
    """Full app at base resolution. Feature card centered on element center."""
    pal = config.PALETTES[plan["style"]]
    img = Image.new("RGBA", (W, H), pal["page"] + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    wide = W >= H  # sidebar only when landscape-ish

    # Browser chrome
    chrome_h = int(H * 0.075)
    d.rectangle([0, 0, W, chrome_h], fill=pal["chrome"])
    d.line([0, chrome_h, W, chrome_h], fill=pal["card_border"], width=max(1, H // 720))
    # traffic lights
    lr = max(4, int(W * 0.006))
    lx = int(W * 0.018)
    cy = chrome_h // 2
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([lx + i * int(lr * 2.8) - lr, cy - lr, lx + i * int(lr * 2.8) + lr, cy + lr], fill=c)
    # url pill
    url = plan["url"].replace("https://", "").replace("http://", "")[:36]
    pill_w, pill_h = int(W * 0.42), int(chrome_h * 0.56)
    px0, py0 = W // 2 - pill_w // 2, cy - pill_h // 2
    _rr(d, [px0, py0, px0 + pill_w, py0 + pill_h], pill_h // 2, fill=pal["input_bg"], outline=pal["card_border"])
    f_url = _font(config.FONT_REGULAR, max(10, int(H * 0.020)))
    d.text((px0 + int(pill_w * 0.06), cy - (f_url.getbbox(url)[3] - f_url.getbbox(url)[1]) // 2 - 1),
           url, font=f_url, fill=pal["muted"])

    # App header
    hdr_y0, hdr_y1 = chrome_h, int(H * 0.160)
    dot_r = max(6, int(H * 0.014))
    dot_cx, dot_cy = int(W * 0.035), (hdr_y0 + hdr_y1) // 2
    d.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=pal["accent"])
    f_title = _font(config.FONT_BOLD, max(12, int(H * 0.026)))
    title = plan["title"][:28]
    d.text((dot_cx + dot_r + int(W * 0.012), dot_cy - (f_title.getbbox(title)[3]) // 2),
           title, font=f_title, fill=pal["ink"])
    # avatar
    av_r = max(8, int(H * 0.018))
    av_cx = int(W * 0.962)
    d.ellipse([av_cx - av_r, dot_cy - av_r, av_cx + av_r, dot_cy + av_r], fill=pal["card_border"])
    f_av = _font(config.FONT_BOLD, max(10, int(H * 0.020)))
    at = plan["title"][:1].upper() or "A"
    d.text((av_cx - _text_w(at, f_av) // 2, dot_cy - f_av.getbbox(at)[3] // 2),
           at, font=f_av, fill=pal["muted"])

    # Sidebar (wide only)
    main_x0 = 0
    if wide:
        main_x0 = int(W * 0.165)
        nav = ["Dashboard", plan["title"][:14], "Library", "Settings"]
        f_nav = _font(config.FONT_REGULAR, max(10, int(H * 0.021)))
        for i, n in enumerate(nav):
            ry = hdr_y1 + int(H * 0.045) + i * int(H * 0.062)
            active = i == 1
            if active:
                _rr(d, [int(W * 0.014), ry - int(H * 0.020), main_x0 - int(W * 0.014), ry + int(H * 0.020)],
                    int(H * 0.012), fill=pal["result_bg"])
            d.text((int(W * 0.032), ry - f_nav.getbbox(n)[3] // 2), n,
                   font=f_nav, fill=pal["ink"] if active else pal["muted"])

    # Feature card centered on element center (ties SDK bbox -> render)
    ecx, ecy = plan["element_center"]["x"], plan["element_center"]["y"]
    main_cx = (main_x0 + W) / 2 / W
    # Blend element x with main-area center so card stays on-screen in portrait
    ccx = _lerp(main_cx, ecx, 0.55)
    ccy = min(max(ecy, 0.34), 0.72)
    card_w = int((W - main_x0) * (0.66 if wide else 0.86))
    card_h = int(H * (0.46 if wide else 0.40))
    cx0 = int(ccx * W - card_w / 2)
    cy0 = int(ccy * H - card_h / 2)
    # clamp inside body
    cx0 = min(max(cx0, main_x0 + int(W * 0.02)), W - card_w - int(W * 0.02))
    cy0 = min(max(cy0, hdr_y1 + int(H * 0.02)), H - card_h - int(H * 0.025))
    # shadow
    sh = max(2, int(H * 0.008))
    _rr(d, [cx0 + sh, cy0 + sh, cx0 + card_w + sh, cy0 + card_h + sh],
        int(H * 0.018), fill=(0, 0, 0, 28 if plan["style"] != "cinematic" else 90))
    _rr(d, [cx0, cy0, cx0 + card_w, cy0 + card_h],
        int(H * 0.018), fill=pal["card"], outline=pal["card_border"], width=max(1, H // 720))

    # card header: eyebrow + live badge
    pad = int(card_w * 0.055)
    f_eye = _font(config.FONT_BOLD, max(10, int(H * 0.019)))
    eyebrow = ("• " + plan["title"]).upper()[:30]
    d.text((cx0 + pad, cy0 + int(card_h * 0.055)), eyebrow, font=f_eye, fill=pal["muted"])

    # input + button row geometry (stored for cursor sync — cursor uses
    # storyboard waypoints tuned to these fractions; keep in sync)
    row_y = cy0 + int(card_h * 0.30)
    row_h = int(card_h * 0.20)
    gap = int(card_w * 0.025)
    btn_w = int(card_w * 0.30)
    in_x0, in_x1 = cx0 + pad, cx0 + card_w - pad - btn_w - gap
    btn_x0, btn_x1 = in_x1 + gap, cx0 + card_w - pad

    return img, {
        "card": (cx0, cy0, card_w, card_h),
        "input": (in_x0, row_y, in_x1 - in_x0, row_h),
        "button": (btn_x0, row_y, btn_x1 - btn_x0, row_h),
        "pad": pad, "main_x0": main_x0, "chrome_h": chrome_h, "hdr": (hdr_y0, hdr_y1),
    }


def _draw_ui_state(img: Image.Image, plan: dict, geom: dict, t: float, typed: str):
    """Draw input text, button, progress, results for time t (in place, RGBA overlay)."""
    pal = config.PALETTES[plan["style"]]
    W, H = img.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    ui = plan["ui"]
    dur = plan["duration"]
    phase = phase_at(plan, t)

    in_x0, in_y0, in_w, in_h = geom["input"]
    btn_x0, btn_y0, btn_w, btn_h = geom["button"]
    cx0, cy0, card_w, card_h = geom["card"]
    pad = geom["pad"]

    # input box
    _rr(d, [in_x0, in_y0, in_x0 + in_w, in_y0 + in_h], in_h // 3,
        fill=pal["input_bg"] + (255,), outline=pal["card_border"] + (255,))
    f_in = _font(config.FONT_REGULAR, max(11, int(H * 0.023)))
    tx, ty = in_x0 + int(in_w * 0.045), in_y0 + in_h // 2 - f_in.getbbox("Ag")[3] // 2
    if typed:
        d.text((tx, ty), typed[:80], font=f_in, fill=pal["ink"] + (255,))
    else:
        d.text((tx, ty), ui["input_hint"][:30], font=f_in, fill=pal["muted"] + (200,))
    # caret while typing
    if phase in ("interact",) and typed is not None:
        if (int(t * 2.5) % 2) == 0:
            cw = _text_w(typed[:80], f_in)
            d.line([tx + cw + 3, in_y0 + int(in_h * 0.22), tx + cw + 3, in_y0 + int(in_h * 0.78)],
                   fill=pal["accent"] + (255,), width=max(2, H // 480))

    # button (press scale at click)
    press = 0.0
    click_at = plan["click_at"]
    if abs(t - click_at) < 0.22:
        press = 1 - abs(t - click_at) / 0.22
    pw, ph = int(btn_w * (1 - 0.05 * press)), int(btn_h * (1 - 0.08 * press))
    bcx, bcy = btn_x0 + btn_w // 2, btn_y0 + btn_h // 2
    bx0, by0 = bcx - pw // 2, bcy - ph // 2
    _rr(d, [bx0, by0, bx0 + pw, by0 + ph], ph // 2, fill=pal["accent"] + (255,))
    f_btn = _font(config.FONT_BOLD, max(11, int(H * 0.022)))
    bl = ui["button_label"][:18]
    # shrink to fit
    while _text_w(bl, f_btn) > pw * 0.86 and len(bl) > 4:
        bl = bl[:-1]
    d.text((bcx - _text_w(bl, f_btn) // 2, bcy - f_btn.getbbox(bl)[3] // 2),
           bl, font=f_btn, fill=pal["accent_ink"] + (255,))

    # progress (loading phase)
    f_lo = _font(config.FONT_REGULAR, max(10, int(H * 0.020)))
    if phase in ("click", "loading", "result"):
        lp0, lp1 = dur * 0.55, dur * 0.78
        lp = _clamp01((t - lp0) / max(lp1 - lp0, 1e-6))
        if phase == "result":
            lp = 1.0
        bar_y = in_y0 + in_h + int(card_h * 0.055)
        bar_h = max(4, int(H * 0.010))
        bar_x0, bar_x1 = in_x0, btn_x0 + btn_w
        _rr(d, [bar_x0, bar_y, bar_x1, bar_y + bar_h], bar_h // 2, fill=pal["progress_track"] + (255,))
        if lp > 0:
            fw = int((bar_x1 - bar_x0) * _ease_out(lp))
            _rr(d, [bar_x0, bar_y, bar_x0 + max(fw, bar_h), bar_y + bar_h], bar_h // 2,
                fill=pal["progress_fill"] + (255,))
        # status line (loading only — result phase speaks through rows)
        if phase != "result":
            pct = int(100 * _ease_out(lp))
            status = ui["loading_label"][:28] + f"  {pct}%"
            scol = pal["muted"] + (255,)
            d.text((bar_x0, bar_y + bar_h + int(H * 0.008)), status, font=f_lo, fill=scol)

    # results (result phase, staggered) — compact so 3 rows fit inside the card
    if phase == "result":
        rt0 = dur * 0.75
        rows = [
            f'"{ui["query_text"][:26]}" — 3 sources found',
            "Analysis complete · 0 errors",
            "» " + ui["result_label"][:30],
        ]
        rh = int(card_h * 0.105)
        gap_r = int(card_h * 0.018)
        start_y = in_y0 + in_h + int(card_h * 0.10)
        f_r = _font(config.FONT_REGULAR, max(10, int(H * 0.021)))
        for i, row in enumerate(rows):
            rp = _clamp01((t - rt0 - i * 0.12) / 0.30)
            if rp <= 0:
                continue
            e = _ease_out(rp)
            a = int(255 * e)
            dy = int((1 - e) * H * 0.02)
            ry0 = start_y + i * (rh + gap_r) - dy
            # Alternate row tint for the last (hero) row
            fill = pal["result_bg"] + (a,) if i < 2 else pal["accent"] + (int(38 * e),)
            _rr(d, [in_x0, ry0, btn_x0 + btn_w, ry0 + rh], int(rh * 0.28),
                fill=fill, outline=pal["card_border"] + (int(160 * e),))
            d.text((in_x0 + int(in_w * 0.045), ry0 + rh // 2 - f_r.getbbox(row)[3] // 2),
                   row[:44], font=f_r, fill=pal["ink"] + (a,))

    # Feature pill is drawn in OUTPUT space (constant size) — see _draw_pill.

    img.alpha_composite(ov)


def _draw_pill(d: ImageDraw.ImageDraw, plan: dict, W: int, H: int, t: float):
    """Bottom-left feature tag, constant size regardless of camera zoom."""
    dur = plan["duration"]
    pill_p = _clamp01(t / 0.5) * _clamp01((dur - t) / 0.4)
    if pill_p <= 0:
        return
    a = int(210 * _ease_out(pill_p))
    f_p = _font(config.FONT_BOLD, max(10, int(H * 0.019)))
    pt = ("• " + plan["name"])[:26]
    tw = _text_w(pt, f_p)
    pw_, ph_ = tw + int(W * 0.03), int(H * 0.045)
    px0, py0_ = int(W * 0.025), int(H * 0.935) - ph_ // 2
    _rr(d, [px0, py0_, px0 + pw_, py0_ + ph_], ph_ // 2,
        fill=(0, 0, 0, int(110 * pill_p)) if plan["style"] != "cinematic" else (214, 255, 59, int(220 * pill_p)))
    pcol = (255, 255, 255, a) if plan["style"] != "cinematic" else (12, 13, 16, a)
    d.text((px0 + int(W * 0.015), py0_ + ph_ // 2 - f_p.getbbox(pt)[3] // 2), pt, font=f_p, fill=pcol)


# ── Cursor ────────────────────────────────────────────────────────────────

def _draw_cursor(frame: ImageDraw.ImageDraw, x: int, y: int, size: int, click_fx: float):
    """macOS-style arrow, hotspot at tip (x, y)."""
    s = size
    # arrow polygon pointing up-left: tip at (x,y)
    pts = [(x, y), (x, y + s), (x + s * 0.28, y + s * 0.76),
           (x + s * 0.38, y + s * 0.98), (x + s * 0.48, y + s * 0.94),
           (x + s * 0.38, y + s * 0.72), (x + s * 0.58, y + s * 0.72)]
    frame.polygon(pts, fill=(255, 255, 255, 255), outline=(20, 20, 22, 255))
    if click_fx > 0:
        r = int(s * (0.6 + 1.8 * (1 - click_fx)))
        a = int(200 * click_fx)
        frame.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, a), width=max(2, s // 8))
        frame.ellipse([x - r // 2, y - r // 2, x + r // 2, y + r // 2],
                      outline=(255, 255, 255, a // 2), width=max(1, s // 12))


# ── Main render ───────────────────────────────────────────────────────────

def render(plan: dict, out_path: Path, fps: int = 30, crf: int = 18,
           quality: str | None = None, size: str | None = None) -> Path:
    import config as _cfg

    W, H = _cfg.canvas(plan["aspect"], quality, size or plan.get("size"))
    dur, n = plan["duration"], plan["n_frames"]
    SS = 2  # supersample base app for crisp text
    BW, BH = W * SS, H * SS

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Base: screenshot cover-fit, else synthetic mock.
    screenshot = plan.get("screenshot")
    base_static: Image.Image | None = None
    geom = None
    if screenshot and Path(screenshot).exists():
        shot = Image.open(screenshot).convert("RGB")
        # cover-fit to base size
        scale = max(BW / shot.width, BH / shot.height)
        shot = shot.resize((int(shot.width * scale) + 1, int(shot.height * scale) + 1), Image.LANCZOS)
        l = (shot.width - BW) // 2
        t = (shot.height - BH) // 2
        base_static = shot.crop((l, t, l + BW, t + BH))
        print(f"[clep] using screenshot {screenshot} as base")
    else:
        base_static, geom = _render_base_app(plan, BW, BH)
        # Retarget camera + cursor to the ACTUAL card/input/button geometry
        # (storyboard waypoints are approximations; geometry is truth).
        cx0, cy0, card_w, card_h = geom["card"]
        in_x0, in_y0, in_w, in_h = geom["input"]
        btn_x0, btn_y0, btn_w, btn_h = geom["button"]
        card_c = {"x": (cx0 + card_w / 2) / BW, "y": (cy0 + card_h / 2) / BH}
        in_c = {"x": (in_x0 + in_w / 2) / BW, "y": (in_y0 + in_h / 2) / BH}
        btn_c = {"x": (btn_x0 + btn_w / 2) / BW, "y": (btn_y0 + btn_h / 2) / BH}
        plan = {**plan,
                "camera": [{**k, **({"cx": card_c["x"], "cy": card_c["y"]} if k["zoom"] > 1.01 else {})}
                           for k in plan["camera"]],
                "cursor": [
                    plan["cursor"][0],
                    {"t": 0.20, "x": in_c["x"], "y": in_c["y"]},
                    {"t": 0.42, "x": in_c["x"], "y": in_c["y"]},
                    {"t": 0.50, "x": btn_c["x"], "y": btn_c["y"]},
                    {"t": 0.75, "x": btn_c["x"], "y": btn_c["y"]},
                    plan["cursor"][5], plan["cursor"][6],
                ]}

    enc = subprocess.Popen(
        [config.FFMPEG_BIN, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)],
        stdin=subprocess.PIPE)

    type_text = plan["typing"]["text"]
    t0, t1 = plan["typing"]["t0"], plan["typing"]["t1"]

    for fi in range(n):
        t = min(fi / fps, dur - 1e-4)
        f = t / dur

        cam = _track(plan["camera"], f, ["cx", "cy", "zoom"])
        cur = _track(plan["cursor"], f, ["x", "y"])

        # Camera crop in base pixels
        vw, vh = BW / cam["zoom"], BH / cam["zoom"]
        cl = min(max(cam["cx"] * BW - vw / 2, 0), BW - vw)
        ct = min(max(cam["cy"] * BH - vh / 2, 0), BH - vh)
        box = (int(cl), int(ct), int(cl + vw), int(ct + vh))

        if base_static is not None and geom is None:
            view = base_static.crop(box).resize((BW, BH), Image.LANCZOS)
        else:
            # synthetic: re-render states per frame (cheap enough at 30fps/4s)
            frame_base, _ = base_empty_cache(plan, BW, BH)
            typed = type_text[: int(len(type_text) * _ease_out((t - t0) / max(t1 - t0, 1e-6)) + 0.5)] \
                if t >= t0 else ""
            if t >= t1:
                typed = type_text
            _draw_ui_state(frame_base, plan, geom_cache(plan, BW, BH), t, typed)
            view = frame_base.crop(box).resize((BW, BH), Image.LANCZOS)

        # Down to output size, draw cursor in output space
        out = view.resize((W, H), Image.LANCZOS).convert("RGBA")
        d = ImageDraw.Draw(out, "RGBA")
        # cursor app-space -> base -> output: cursor is inside camera view
        # base coords of cursor:
        cur_bx, cur_by = cur["x"] * BW, cur["y"] * BH
        # output coords:
        ox = (cur_bx - box[0]) / max(box[2] - box[0], 1) * W
        oy = (cur_by - box[1]) / max(box[3] - box[1], 1) * H
        # hide cursor when outside view (during fast moves it stays inside by design)
        if -40 <= ox <= W + 40 and -40 <= oy <= H + 40:
            cfx = max(0.0, 1 - abs(t - plan["click_at"]) / 0.30) if abs(t - plan["click_at"]) < 0.30 else 0.0
            _draw_cursor(d, int(ox), int(oy), max(14, int(config.CURSOR_SIZE * W / 1280)), cfx)
        _draw_pill(d, plan, W, H, t)

        enc.stdin.write(out.convert("RGB").tobytes())

    enc.stdin.close()
    enc.wait()
    if enc.returncode != 0:
        raise RuntimeError("ffmpeg clip encode failed")
    print(f"[clep] done: {out_path} ({dur:.1f}s {W}x{H} {plan['style']})")
    return out_path


# Small caches so per-frame synthetic re-render doesn't rebuild geometry.
_GEOM_CACHE: dict = {}
_BASE_CACHE: dict = {}


def geom_cache(plan: dict, W: int, H: int):
    key = (plan["name"], plan["title"], plan["url"], plan["style"], W, H,
           round(plan["element_center"]["x"], 3), round(plan["element_center"]["y"], 3))
    if key not in _GEOM_CACHE:
        _, g = _render_base_app(plan, W, H)
        _GEOM_CACHE[key] = g
    return _GEOM_CACHE[key]


def base_empty_cache(plan: dict, W: int, H: int):
    key = (plan["name"], plan["title"], plan["url"], plan["style"], W, H,
           round(plan["element_center"]["x"], 3), round(plan["element_center"]["y"], 3))
    if key not in _BASE_CACHE:
        img, g = _render_base_app(plan, W, H)
        _GEOM_CACHE[key] = g
        _BASE_CACHE[key] = img
    return _BASE_CACHE[key].copy(), _GEOM_CACHE[key]
