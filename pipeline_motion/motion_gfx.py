"""
Clean motion-graphics engine for pipeline_motion — white/blue kinetic typography
matching the reference (1920x1080, white bg, vivid blue #010CCA).

Supports:
  - Legacy "diagram" beats (rendered crisp, no wobble if style in light/blue/gray)
  - New "kinetic" / "clean" beats with explicit layouts:
      headline, search_typing, badge_cards, stat_blue, grid_cards, bars_skeleton

Each beat spec is rendered frame-by-frame via PIL at 2x supersample and piped
to ffmpeg as an H.264 clip. The compositor treats the clip as a fullscreen
card (no Ken Burns) — the motion is inside the clip itself.

Beat shape (new):
  "kinetic": {
    "style": "light" | "blue" | "gray",
    "layout": "headline" | "search_typing" | "badge_cards" | "stat_blue" | "grid_cards" | "bars" | "diagram",
    # layout-specific keys
  }

Or legacy:
  "diagram": { "style": "light", "nodes": [...], ... }  -> rendered crisp

If both present, "kinetic" wins.
"""

import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config

# ── font cache ───────────────────────────────────────────────────────────
_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONTS:
        try:
            _FONTS[key] = ImageFont.truetype(path, size)
        except Exception:
            # fallback to regular if black not found
            _FONTS[key] = ImageFont.truetype(config.FONT_REGULAR, size)
    return _FONTS[key]

def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))

def _ease_out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 1 - pow(1 - t, 3)

def _ease_in_out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 4*t*t*t if t < 0.5 else 1 - pow(-2*t+2, 3)/2

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * _clamp01(t)

def _ease_out_expo(t: float) -> float:
    t = _clamp01(t)
    return 1 - pow(2, -10*t) if t < 1 else 1

def _ease_out_back(t: float) -> float:
    t = _clamp01(t)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3*pow(t-1,3) + c1*pow(t-1,2)

# ── watermark / paper helpers ────────────────────────────────────────────
_WATERMARK_CACHE: dict[tuple, Image.Image] = {}

def _get_watermark_image(SS_W: int, SS_H: int, is_dark_bg: bool) -> Image.Image | None:
    if not getattr(config, "TALO_WATERMARK_ENABLED", True):
        return None
    key = (SS_W, SS_H, is_dark_bg, getattr(config, "TALO_WATERMARK_SCALE", 0.72), getattr(config, "TALO_WATERMARK_ROT", -6.0), getattr(config, "TALO_WATERMARK_OPACITY", 0.045))
    if key in _WATERMARK_CACHE:
        return _WATERMARK_CACHE[key]
    try:
        src_path = Path(config.TALO_LOGO_WHITE_PATH) if Path(config.TALO_LOGO_WHITE_PATH).exists() else Path(config.TALO_LOGO_PATH)
        if not src_path.exists():
            return None
        wm_src = Image.open(src_path).convert("RGBA")
        # crop to content bbox (where alpha>10)
        alpha = wm_src.split()[3]
        bbox = alpha.getbbox()
        if bbox:
            wm_src = wm_src.crop(bbox)
        # tint: for paper (light) we want dark logo; for black bg we want white logo
        # wm_src is white letters on transparent — for light bg convert white->near-black
        if not is_dark_bg:
            # create dark version
            # need to keep alpha but change RGB to fg near-black (10,10,11)
            # simple: create solid color image multiplied by alpha
            fg = Image.new("RGBA", wm_src.size, (10, 10, 11, 255))
            # use alpha as mask
            fg.putalpha(wm_src.split()[3])
            wm_src = fg
        # else keep white as is
        # scale to target letters width = scale * canvas width
        target_letters_w = int(SS_W * float(getattr(config, "TALO_WATERMARK_SCALE", 0.72)))
        # keep aspect
        scale = target_letters_w / wm_src.size[0]
        new_h = int(wm_src.size[1] * scale)
        wm_scaled = wm_src.resize((target_letters_w, new_h), Image.LANCZOS)
        # apply opacity
        op = float(getattr(config, "TALO_WATERMARK_OPACITY", 0.045))
        if op < 1.0:
            # multiply alpha
            a = wm_scaled.split()[3]
            a = a.point(lambda v: int(v * op))
            wm_scaled.putalpha(a)
        # rotate
        rot = float(getattr(config, "TALO_WATERMARK_ROT", -6.0))
        if abs(rot) > 0.01:
            wm_scaled = wm_scaled.rotate(rot, resample=Image.BICUBIC, expand=True)
        _WATERMARK_CACHE[key] = wm_scaled
        return wm_scaled
    except Exception as e:
        print(f"[motion_gfx] watermark failed: {e}")
        return None

def _paste_watermark(canvas_overlay: Image.Image, S: dict, is_dark_bg: bool):
    try:
        wm = _get_watermark_image(S["w"], S["h"], is_dark_bg)
        if wm is None:
            return
        cw, ch = S["w"], S["h"]
        # center with slight offset for editorial (a bit lower)
        x = (cw - wm.size[0])//2
        y = (ch - wm.size[1])//2 + int(ch*0.04)  # nudge down like reference
        # clamp
        canvas_overlay.alpha_composite(wm, dest=(x, y))
    except Exception:
        pass

def _draw_paper_grain(draw: ImageDraw.ImageDraw, S: dict, intensity: float = 0.10):
    # ultra-subtle paper grain dots for editorial paper (off-white) — vintage film feel without blocking text
    # draw ~600 tiny dots at low alpha; seeded so consistent per frame (animate slowly)
    import random
    # use deterministic seed per frame via S hash? We'll just skip grain if intensity 0; caller can pass frame index
    return  # placeholder — keep bg clean per brief; grain could be added via ffmpeg later if needed

def _draw_word_stagger(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, cx: int, y: int,
                       p: float, S: dict, base_color: tuple, accent_word: str | None = None, accent_color: tuple | None = None,
                       stagger: float = 0.045, word_duration: float = 0.32):
    words = text.split()
    if not words:
        return 0
    # measure total width including spaces
    space_w,_ = _text_bbox(" ", font)
    word_ws = []
    for w in words:
        tw,_ = _text_bbox(w, font)
        word_ws.append(tw)
    total_w = sum(word_ws) + space_w*(len(words)-1)
    cur_x = cx - total_w//2
    for idx, w in enumerate(words):
        delay = idx * stagger
        dur = word_duration
        wp = _clamp01((p - delay)/dur)
        if wp <= 0:
            cur_x += word_ws[idx] + space_w
            continue
        e = _ease_out_cubic(wp)
        alpha = int(255 * e)
        # y lift: 18*ss at start
        y_off = int((1-e) * S["h"]*0.016)
        # subtle scale simulated via alpha already; add tiny x slide for lateral energy on early words
        x_off = int((1-e) * S["w"]*0.004 * (1 if idx%2==0 else -1))
        col = base_color
        is_accent = accent_word and w.strip(".,!—") in accent_word or (accent_word and accent_word in w)
        if is_accent and accent_color:
            col = accent_color
        # shadow for accent pop (tiny)
        if is_accent and e>0.6:
            # faint bloom behind accent word after it lands
            pass
        draw.text((cur_x + x_off, y + y_off), w, font=font, fill=(*col[:3], alpha) if len(col)==3 else col[:3]+(alpha,))
        cur_x += word_ws[idx] + space_w
    return total_w

# ── legacy wobble helpers (kept for dark/whiteboard diagrams) ───────────
def _wobble_pts(p0, p1, seed: float, amp: float, n: int = 14):
    x0, y0 = p0; x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy/length, dx/length
    pts = []
    for i in range(n+1):
        t = i/n
        off = amp * (math.sin(t*9.0+seed*7.3)*0.6 + math.sin(t*23.0+seed*3.1)*0.4)
        pts.append((x0+dx*t+nx*off, y0+dy*t+ny*off))
    return pts

def _partial(pts: list, p: float) -> list:
    if p >= 1.0 or len(pts) < 2:
        return pts
    seg_lens = [math.dist(pts[i], pts[i+1]) for i in range(len(pts)-1)]
    total = sum(seg_lens) or 1.0
    target = total * _clamp01(p)
    out=[pts[0]]; acc=0.0
    for i, seg in enumerate(seg_lens):
        if acc+seg >= target:
            t = (target-acc)/(seg or 1.0)
            a,b = pts[i], pts[i+1]
            out.append((a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t))
            return out
        acc+=seg; out.append(pts[i+1])
    return out

def _roundrect_perimeter(x0,y0,x1,y1,r,seed,amp):
    r = min(r, (x1-x0)/2, (y1-y0)/2)
    pts=[]
    def arc(cx,cy,a_from,a_to):
        for i in range(1,7):
            a = a_from+(a_to-a_from)*i/6
            pts.append((cx+r*math.cos(a)+amp*0.4*math.sin(a*3+seed),
                        cy+r*math.sin(a)+amp*0.4*math.cos(a*2+seed)))
    pts.extend(_wobble_pts((x0+r,y0),(x1-r,y0),seed,amp))
    arc(x1-r,y0+r,-math.pi/2,0)
    pts.extend(_wobble_pts((x1,y0+r),(x1,y1-r),seed+1,amp))
    arc(x1-r,y1-r,0,math.pi/2)
    pts.extend(_wobble_pts((x1-r,y1),(x0+r,y1),seed+2,amp))
    arc(x0+r,y1-r,math.pi/2,math.pi)
    pts.extend(_wobble_pts((x0,y1-r),(x0,y0+r),seed+3,amp))
    arc(x0+r,y0+r,math.pi,1.5*math.pi)
    return pts

def _oval_perimeter(cx,cy,rx,ry,seed,amp,n=48):
    pts=[]
    for i in range(n+1):
        a=2*math.pi*i/n
        w=1+amp/max(rx,1)*math.sin(a*5+seed*5.0)*2
        pts.append((cx+rx*w*math.cos(a), cy+ry*w*math.sin(a)))
    pts.append(pts[0]); return pts

def _arrow_head(draw, tip, direction, size, color, width):
    x,y=tip; a=direction
    for sign in (-1,1):
        b=a+sign*math.radians(152)
        draw.line([tip,(x+size*math.cos(b), y+size*math.sin(b))], fill=color, width=width, joint="curve")

# ── text helpers ─────────────────────────────────────────────────────────
def _text_bbox(text: str, font: ImageFont.FreeTypeFont):
    # returns (w,h)
    bbox = font.getbbox(text)
    return bbox[2]-bbox[0], bbox[3]-bbox[1]

def _fit_fontsize_to_width(text: str, font_path: str, max_w: int, start_size: int, min_size: int = 20) -> int:
    size = start_size
    while size > min_size:
        f = _font(font_path, size)
        w,_ = _text_bbox(text, f)
        if w <= max_w:
            return size
        size -= 2
    return min_size

def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int):
    words = text.split()
    lines=[]
    cur=""
    for w in words:
        test = (cur+" "+w).strip()
        tw,_ = _text_bbox(test, font)
        if tw <= max_w or not cur:
            cur=test
        else:
            lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines

# ── clean style resolution ───────────────────────────────────────────────
def _style(spec_style: str | None) -> dict:
    return config.MGFX_STYLES.get(spec_style or config.MGFX_DEFAULT_STYLE,
                                  config.MGFX_STYLES[config.MGFX_DEFAULT_STYLE])

# ── rounded rect helper (clean) ──────────────────────────────────────────
def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width=1):
    x0,y0,x1,y1 = xy
    # PIL's rounded_rectangle is aliased but fine at supersample
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

# ── clean layouts ────────────────────────────────────────────────────────

def _draw_headline(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # upgraded: word-stagger kinetic with subtle watermark + per-line ease + accent pop
    text = spec.get("text") or spec.get("headline") or "We give your agent\nsearch & fetch 100% FREE."
    accent = spec.get("accent") or spec.get("accent_phrase") or None
    accents = accent if isinstance(accent, list) else ([accent] if accent else [])
    # watermark behind text for editorial paper (subtle)
    is_dark = style.get("bg", (255,255,255))[:3] == (6,6,7) or style.get("bg", (255,255,255))[0] < 30
    if not is_dark and p > 0.02:
        try:
            _paste_watermark(draw._image if hasattr(draw, '_image') else draw, S, is_dark_bg=False)
        except: pass
    # prepare lines
    lines = text.split("\n") if "\n" in text else None
    max_w = int(S["w"] * 0.82)
    base_fs = max(28, int(S["h"] * 0.072))
    if lines is None:
        f_test = _font(config.MGFX_FONT_BLACK, base_fs)
        tw,_ = _text_bbox(text, f_test)
        if tw > max_w and len(text.split()) > 4:
            words = text.split()
            mid = len(words)//2
            lines = [" ".join(words[:mid]), " ".join(words[mid:])]
        else:
            lines = [text]
    f = _font(config.MGFX_FONT_BLACK, base_fs)
    for _ in range(10):
        widest = max(_text_bbox(l, f)[0] for l in lines)
        if widest <= max_w:
            break
        base_fs -= 4
        f = _font(config.MGFX_FONT_BLACK, base_fs)
        if base_fs <= 24:
            break
    ascent = f.getbbox("Ag")[3]-f.getbbox("Ag")[1]
    line_h = ascent + int(base_fs*0.22)
    total_h = line_h*len(lines)
    cx, cy = S["w"]//2, S["h"]//2
    for li, line in enumerate(lines):
        lp = _clamp01((p - li*0.09)/0.42)
        if lp <= 0: continue
        # per-line reveal via word stagger inside line
        # detect accent substring
        found = None
        for acc in accents:
            if acc and acc.strip() and acc in line:
                found = acc
                break
        # font for this line (already f)
        ly = cy - total_h//2 + li*line_h
        # y offset for whole line landing
        e_line = _ease_out_cubic(lp)
        ly += int((1-e_line) * S["h"]*0.018)
        if found:
            # split and draw words with stagger but color pieces differently
            # we draw word by word spanning the whole line, tracking accent state
            words = line.split()
            space_w,_ = _text_bbox(" ", f)
            # compute total width to center
            word_ws = [_text_bbox(w, f)[0] for w in words]
            total_w = sum(word_ws) + space_w*(len(words)-1)
            cur_x = cx - total_w//2
            acc_words = set(found.split())
            for wi, w in enumerate(words):
                wp = _clamp01((p - li*0.09 - wi*0.045)/0.38)
                if wp <=0:
                    cur_x += word_ws[wi] + space_w
                    continue
                e = _ease_out_expo(wp) if wi%2==0 else _ease_out_cubic(wp)
                alpha = int(255 * e)
                y_off = int((1-e) * S["h"]*0.014)
                x_off = int((1-e) * S["w"]*0.003 * (1 if wi%2==0 else -1))
                is_acc = w.strip(".,!—") in acc_words or w in acc_words or found.strip() == w
                # also check multi-word accent spanning: if found has space, color any word in found
                if found and " " in found:
                    is_acc = w in found.split() or found in line and words[wi] in found
                col = style["accent"] if is_acc else style["fg"]
                # add subtle scale via font size pulse for accent: draw slightly larger on landing (simulate with shadow)
                if is_acc and e>0.85:
                    # faint glow behind accent word
                    glow_col = (*style["accent"][:3], int(30*alpha/255))
                    draw.text((cur_x+x_off, ly+y_off), w, font=f, fill=glow_col)
                draw.text((cur_x+x_off, ly+y_off), w, font=f, fill=(*col[:3], alpha) if len(col)==3 else col+(alpha,))
                cur_x += word_ws[wi] + space_w
        else:
            # no accent: word stagger monochrome
            _draw_word_stagger(draw, line, f, cx, ly, lp, S, style["fg"], None, None, stagger=0.05, word_duration=0.34)


def _draw_search_typing(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Search box centered, typing animation
    query = spec.get("query") or spec.get("text") or "What happened in AI today?"
    # container dims
    box_w = int(S["w"] * 0.62)
    box_h = int(S["h"] * 0.11)
    cx, cy = S["w"]//2, S["h"]//2
    x0, y0 = cx - box_w//2, cy - box_h//2
    x1, y1 = cx + box_w//2, cy + box_h//2
    rad = box_h//2  # fully rounded ends but spec shows slight rounded rect

    # entry animation: box scales in
    entry = _ease_out_cubic(_clamp01(p/0.35))
    # slight scale from 0.94
    scale = _lerp(0.92, 1.0, entry)
    # adjust box to scale about center
    bw = int(box_w * scale); bh = int(box_h * scale)
    cx0, cy0 = S["w"]//2, S["h"]//2
    x0s, y0s = cx0 - bw//2, cy0 - bh//2
    x1s, y1s = cx0 + bw//2, cy0 + bh//2

    # outer soft shadow / light gray backdrop (as in reference: very light pill behind)
    # draw outer backdrop larger by 14*ss
    bg_pad = int(S["h"]*0.018)
    _rounded_rect(draw, (x0s-bg_pad, y0s-bg_pad, x1s+bg_pad, y1s+bg_pad),
                  radius=(bh+bg_pad*2)//2, fill=(*style["card_bg"][:3], int(255*entry)) if len(style["card_bg"])==3 else style["card_bg"]+(255,),
                  outline=None)

    # main search box: white with light border
    # border color #E0E0F0
    border_col = style["card_border"]
    if len(border_col)==3:
        border_col = border_col + (255,)
    # alpha based on entry
    _rounded_rect(draw, (x0s, y0s, x1s, y1s), radius=bh//2,
                  fill=(255,255,255, int(255*entry)),
                  outline=(*border_col[:3], int(180*entry)), width=max(1, S["stroke"]//2))

    # blue submit button on right
    btn_d = int(bh * 0.72)
    btn_cx = x1s - btn_d//2 - int(S["w"]*0.012)
    btn_cy = cy0
    btn_alpha = int(255 * _clamp01((p-0.10)/0.18))
    if btn_alpha>0:
        draw.ellipse((btn_cx-btn_d//2, btn_cy-btn_d//2, btn_cx+btn_d//2, btn_cy+btn_d//2),
                     fill=(*style["accent"][:3], btn_alpha) if len(style["accent"])==3 else style["accent"]+(btn_alpha,))
        # arrow up (reference shows up arrow inside blue circle)
        # draw simple arrow: vertical line + chevron
        arr_col = (255,255,255, btn_alpha)
        # arrow parameters relative to btn
        # center arrow
        aw = btn_d*0.28; ah = btn_d*0.28
        # vertical stem
        stem_w = max(2, int(S["stroke"]*0.9))
        # arrow up shape: line + head
        # stem from y+ah/2 to y - ah/2
        sy0, sy1 = btn_cy+ah*0.35, btn_cy - ah*0.45
        draw.line((btn_cx, sy0, btn_cx, sy1), fill=arr_col, width=stem_w)
        # head
        head_sz = int(ah*0.45)
        draw.line((btn_cx-head_sz, sy1+head_sz, btn_cx, sy1), fill=arr_col, width=stem_w)
        draw.line((btn_cx+head_sz, sy1+head_sz, btn_cx, sy1), fill=arr_col, width=stem_w)

    # typing text inside box, left aligned
    # typing progress: 0->60% of duration types characters
    type_p = _clamp01((p - 0.15)/0.55)
    # number of chars to show
    nchars = int(len(query) * _ease_out_cubic(type_p) + 0.5) if type_p>0 else 0
    visible = query[:nchars]
    # text font size ~ 3.2% of canvas height
    fs = max(18, int(S["h"]*0.032))
    f = _font(config.MGFX_FONT_REGULAR, fs)
    tx = x0s + int(S["w"]*0.022)
    # vertical center
    _, th = _text_bbox("Ag", f)
    ty = cy0 - th//2 - f.getbbox("Ag")[1]//2
    if entry>0 and visible:
        alpha = int(255*entry)
        draw.text((tx, ty), visible, font=f, fill=(*style["fg"][:3], alpha) if len(style["fg"])==3 else style["fg"][:3]+(alpha,))
        # blinking cursor
        # cursor after last char, blink every 0.53s
        tw,_ = _text_bbox(visible, f)
        cursor_on = (int(p*10) % 10) < 6  # crude blink
        if cursor_on and type_p < 0.99 and nchars < len(query):
            cx_cur = tx + tw + int(S["w"]*0.003)
            # cursor is 2px wide line
            draw.line((cx_cur, ty+int(th*0.12), cx_cur, ty+th), fill=(*style["fg"][:3], alpha), width=max(2, S["stroke"]//2))

def _draw_badge_cards(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Top badge + 2 cards stacked
    badge_text = spec.get("badge") or spec.get("title") or "Searching live news • • •"
    cards = spec.get("cards") or [
        {"source":"GatesNotes","title":"The turbulent AI era is here. The choices we make now are critical.","time":"29 minutes ago"},
        {"source":"The Washington Post","title":"Over 1,000 AI agents worked together in OpenAI hack, report reveals","time":"1 hour ago"},
    ]
    # badge dimensions
    bw = int(S["w"]*0.30)
    bh = int(S["h"]*0.062)
    badge_cx, badge_cy = S["w"]//2, int(S["h"]*0.24)
    # entry: badge pops in first
    badge_p = _ease_out_cubic(_clamp01(p/0.30))
    if badge_p>0:
        # badge pill
        rad = bh//2
        # scale slightly
        sc = _lerp(0.92, 1.0, badge_p)
        bww, bhh = int(bw*sc), int(bh*sc)
        x0, y0 = badge_cx - bww//2, badge_cy - bhh//2
        x1, y1 = badge_cx + bww//2, badge_cy + bhh//2
        alpha = int(255*badge_p)
        _rounded_rect(draw, (x0,y0,x1,y1), radius=rad,
                      fill=(*style["pill_bg"][:3], alpha) if len(style["pill_bg"])==3 else style["pill_bg"][:3]+(alpha,),
                      outline=(*style["card_border"][:3], int(120*alpha/255)) if style["pill_bg"] != style["accent"] else None,
                      width=1)
        # badge text and icon (asterisk)
        fs = max(16, int(S["h"]*0.022))
        f = _font(config.MGFX_FONT_BOLD, fs)
        icon = "✳ " if "Searching" in badge_text else ""
        txt = icon + badge_text
        tw,th = _text_bbox(txt, f)
        draw.text((badge_cx - tw//2, badge_cy - th//2 - f.getbbox("Ag")[1]//2), txt, font=f,
                  fill=(*style["pill_fg"][:3], alpha) if len(style["pill_fg"])==3 else style["pill_fg"]+(alpha,))

    # cards
    # layout: each card is rounded rect, light gray bg, with source bold, title gray, time pill on right
    card_w = int(S["w"]*0.68)
    card_h = int(S["h"]*0.16)
    gap = int(S["h"]*0.022)
    start_y = int(S["h"]*0.36)
    for i, c in enumerate(cards[:4]):
        card_p = _clamp01((p - 0.22 - i*0.12)/0.35)
        if card_p <=0: continue
        e = _ease_out_cubic(card_p)
        alpha = int(255*e)
        # slide up
        dy = int((1-e) * S["h"]*0.025)
        y0 = start_y + i*(card_h+gap) - dy
        y1 = y0 + card_h
        x0 = S["w"]//2 - card_w//2
        x1 = S["w"]//2 + card_w//2
        rad = int(S["h"]*0.018)
        _rounded_rect(draw, (x0,y0,x1,y1), radius=rad,
                      fill=(*style["card_bg"][:3], alpha) if len(style["card_bg"])==3 else style["card_bg"][:3]+(alpha,),
                      outline=(*style["card_border"][:3], int(180*alpha/255)),
                      width=max(1, S["stroke"]//2))
        # source line
        pad = int(S["w"]*0.022)
        src = str(c.get("source") or c.get("title", "")[:20])
        title = str(c.get("title") or c.get("text") or "")
        time_ago = str(c.get("time") or c.get("time_ago") or "")
        # source font
        fs_src = max(16, int(S["h"]*0.021))
        f_src = _font(config.MGFX_FONT_BOLD, fs_src)
        fs_body = max(14, int(S["h"]*0.018))
        f_body = _font(config.MGFX_FONT_REGULAR, fs_body)
        fs_time = max(12, int(S["h"]*0.016))
        f_time = _font(config.MGFX_FONT_REGULAR, fs_time)
        # draw source at top-left
        sx, sy = x0+pad, y0+int(S["h"]*0.018)
        draw.text((sx,sy), src, font=f_src, fill=(*style["fg"][:3], alpha))
        # time pill on right if exists
        if time_ago:
            tw,_ = _text_bbox(time_ago, f_time)
            pill_pad_x = int(S["w"]*0.012)
            pill_pad_y = int(S["h"]*0.006)
            pill_w = tw + pill_pad_x*2
            pill_h = int(S["h"]*0.032)
            px1 = x1 - pad
            px0 = px1 - pill_w
            py0 = sy - pill_pad_y//2
            py1 = py0 + pill_h
            _rounded_rect(draw, (px0,py0,px1,py1), radius=pill_h//2,
                          fill=(*style["pill_bg"][:3], int(240*alpha/255)),
                          outline=None)
            draw.text((px0+pill_pad_x, py0+ pill_h//2 - f_time.getbbox(time_ago)[3]//2 + int(S["h"]*0.001)),
                      time_ago, font=f_time, fill=(*style["pill_fg"][:3], alpha))
        # title wrapped under source
        # available width minus pad
        max_tw = card_w - pad*2
        # wrap title into 1-2 lines
        words = title.split()
        lines=[]
        cur=""
        for w in words:
            test = (cur+" "+w).strip()
            tw,_ = _text_bbox(test, f_body)
            if tw <= max_tw or not cur:
                cur=test
            else:
                lines.append(cur); cur=w
                if len(lines)>=2: break
        if cur: lines.append(cur)
        lines = lines[:2]
        lh = f_body.getbbox("Ag")[3]-f_body.getbbox("Ag")[1] + int(S["h"]*0.006)
        for li, line in enumerate(lines):
            ly = sy + int(S["h"]*0.032) + li*lh
            draw.text((sx, ly), line, font=f_body, fill=(100,100,115, alpha) if style["fg"]==(16,16,16) else (*style["muted"][:3], alpha))

def _draw_stat_blue(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Full blue bg, left big stat with check, right stacked lines
    # style should be blue already; bg already blue
    # layout: left 42% width for stat, right for lines
    # center vertically
    bg_style = style  # caller sets bg already; this ensures correct colors
    # entry progress
    e = _ease_out_cubic(_clamp01(p/0.35))
    alpha = int(255*e)
    # left stat
    stat = spec.get("stat") or spec.get("value") or "$0"
    check = spec.get("check", True)
    lines = spec.get("lines") or spec.get("text") or ["No subscriptions.", "No quotas."]
    if isinstance(lines, str):
        lines = [lines]
    # positions
    # stat centered at 28% x
    stat_cx = int(S["w"]*0.28)
    stat_cy = S["h"]//2
    fs_stat = max(80, int(S["h"]*0.22))
    # adjust if portrait
    if config.PORTRAIT:
        fs_stat = max(80, int(S["h"]*0.10))
    f_stat = _font(config.MGFX_FONT_BLACK, fs_stat)
    tw,_ = _text_bbox(stat, f_stat)
    # slight scale in
    sc = _lerp(0.88, 1.0, e)
    # draw stat with sc? we just alpha
    sx = stat_cx - tw//2 + int((1-e)*S["w"]*0.015)
    sy = stat_cy - (f_stat.getbbox(stat)[3]-f_stat.getbbox(stat)[1])//2
    draw.text((sx, sy), stat, font=f_stat, fill=(255,255,255, alpha))
    if check:
        # checkmark next to stat
        # offset to the right of stat
        chk_x = sx + tw + int(S["w"]*0.018)
        chk_y = stat_cy - int(S["h"]*0.015)
        chk_sz = int(S["h"]*0.05)
        # check drawn as two lines
        # down-right then up-right
        p1 = (chk_x, chk_y)
        p2 = (chk_x + chk_sz*0.35, chk_y + chk_sz*0.30)
        p3 = (chk_x + chk_sz*0.90, chk_y - chk_sz*0.40)
        w = max(3, int(S["h"]*0.009))
        if alpha>0:
            draw.line((p1, p2), fill=(255,255,255,alpha), width=w, joint="curve")
            draw.line((p2, p3), fill=(255,255,255,alpha), width=w, joint="curve")
    # right lines
    # right block at 68% x
    rx = int(S["w"]*0.58)
    line_fs = max(22, int(S["h"]*0.045))
    if config.PORTRAIT:
        line_fs = max(20, int(S["w"]*0.06))
        rx = int(S["w"]*0.50)
    f_line = _font(config.MGFX_FONT_BOLD, line_fs)
    # stack from center
    total_h = len(lines)*(f_line.getbbox("Ag")[3]-f_line.getbbox("Ag")[1] + int(S["h"]*0.012))
    base_y = stat_cy - total_h//2
    for i, line in enumerate(lines):
        lp = _clamp01((p - 0.18 - i*0.09)/0.30)
        if lp <=0: continue
        le = _ease_out_cubic(lp)
        la = int(255*le)
        ly = base_y + i*(f_line.getbbox("Ag")[3]-f_line.getbbox("Ag")[1] + int(S["h"]*0.012)) + int((1-le)*S["h"]*0.015)
        draw.text((rx, ly), line, font=f_line, fill=(255,255,255, la))

def _draw_grid_cards(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 4 cards in a row (or 2x2 on portrait), blurred placeholder style + bottom pill
    count = int(spec.get("count") or spec.get("cards") or 4)
    pill_text = spec.get("pill") or spec.get("badge") or "1 search · 4 pages · $0.00"
    # layout grid
    is_portrait = config.PORTRAIT
    if is_portrait:
        cols = 2; rows = (count+1)//2
    else:
        cols = min(count, 4); rows = 1 if count<=4 else 2

    card_w = int(S["w"]* (0.20 if not is_portrait else 0.42))
    card_h = int(S["h"]* (0.44 if not is_portrait else 0.28))
    gap_x = int(S["w"]*0.02)
    gap_y = int(S["h"]*0.02)
    total_w = cols*card_w + (cols-1)*gap_x
    start_x = S["w"]//2 - total_w//2
    start_y = int(S["h"]*0.26) if not is_portrait else int(S["h"]*0.18)

    for idx in range(count):
        col = idx % cols
        row = idx // cols
        x0 = start_x + col*(card_w+gap_x)
        y0 = start_y + row*(card_h+gap_y)
        x1 = x0 + card_w; y1 = y0 + card_h
        ent = _clamp01((p - 0.15 - idx*0.08)/0.35)
        if ent<=0: continue
        e = _ease_out_cubic(ent)
        alpha = int(255*e)
        dy = int((1-e)*S["h"]*0.02)
        # card
        rad = int(S["h"]*0.016)
        _rounded_rect(draw, (x0, y0-dy, x1, y1-dy), radius=rad,
                      fill=(255,255,255, alpha),
                      outline=(*style["card_border"][:3], int(180*alpha/255)), width=1)
        # inside placeholder lines (light gray bars)
        # simulate blurred text lines as in reference (f18)
        # draw 6-7 gray lines
        pad = int(card_w*0.07)
        line_h = int(S["h"]*0.012)
        line_gap = int(S["h"]*0.011)
        # header shorter
        # blurred effect: alpha lower for lines
        for li in range(6):
            ly = y0 - dy + int(card_h*0.08) + li*(line_h+line_gap)
            # width varies
            if li==0:
                lw = int(card_w*0.62)
            elif li==3:
                lw = int(card_w*0.45)
            elif li==5:
                lw = int(card_w*0.35)
            else:
                lw = int(card_w*0.78) - (li%2)*int(card_w*0.10)
            # draw rect simulating line
            _rounded_rect(draw, (x0+pad, ly, x0+pad+lw, ly+line_h), radius=line_h//2,
                          fill=(230,232,238, int(220*alpha/255)))
    # bottom pill centered near bottom
    pill_p = _clamp01((p-0.32)/0.22)
    if pill_p>0:
        e = _ease_out_cubic(pill_p)
        alpha = int(255*e)
        pill_h = int(S["h"]*0.062)
        fs = max(14, int(S["h"]*0.020))
        f = _font(config.MGFX_FONT_REGULAR, fs)
        # estimate pill width
        pill_txt = pill_text
        # highlight $0.00 part in blue if present
        # We'll render whole pill bg light then draw text with parts
        tw,_ = _text_bbox(pill_txt, f)
        pad_x = int(S["w"]*0.022)
        pill_w = tw + pad_x*2 + int(S["w"]*0.02)
        pill_cx = S["w"]//2
        pill_cy = int(S["h"]*0.82)
        dy = int((1-e)*S["h"]*0.015)
        x0, y0 = pill_cx - pill_w//2, pill_cy - pill_h//2 - dy
        x1, y1 = pill_cx + pill_w//2, pill_cy + pill_h//2 - dy
        # pill bg white with gray border
        _rounded_rect(draw, (x0,y0,x1,y1), radius=pill_h//2,
                      fill=(245,245,255, alpha),
                      outline=(220,224,245, int(200*alpha/255)), width=1)
        # inside text: split by "·"
        # rough: draw left part gray, right price blue
        # Find last "·" split for price
        parts = [s.strip() for s in pill_txt.split("·")]
        # if last part contains $, accent it
        if parts and "$" in parts[-1]:
            # draw all but last gray, last blue
            # measure
            left_txt = " · ".join(parts[:-1]) + (" · " if len(parts)>1 else "")
            price = parts[-1]
            f_bold = _font(config.MGFX_FONT_BOLD, int(fs*1.15))
            lw,_ = _text_bbox(left_txt, f)
            pw,_ = _text_bbox(price, f_bold)
            total = lw+pw
            sx = pill_cx - total//2
            draw.text((sx, pill_cy - f.getbbox(left_txt)[3]//2 - dy), left_txt, font=f, fill=(110,110,125, alpha))
            draw.text((sx+lw, pill_cy - f_bold.getbbox(price)[3]//2 - dy), price, font=f_bold,
                      fill=(*style["accent"][:3], alpha))
        else:
            draw.text((pill_cx - tw//2, pill_cy - f.getbbox(pill_txt)[3]//2 - dy), pill_txt, font=f, fill=(110,110,125, alpha))

def _draw_bars_skeleton(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 4 horizontal light bars centered, like transition frames in reference (f2)
    count = int(spec.get("count") or 4)
    bar_w = int(S["w"]*0.66)
    bar_h = int(S["h"]*0.055)
    gap = int(S["h"]*0.038)
    total_h = count*bar_h + (count-1)*gap
    start_y = S["h"]//2 - total_h//2
    cx = S["w"]//2
    for i in range(count):
        y0 = start_y + i*(bar_h+gap)
        y1 = y0 + bar_h
        x0 = cx - bar_w//2; x1 = cx + bar_w//2
        ent = _clamp01((p - i*0.07)/0.30)
        if ent<=0: continue
        e = _ease_out_cubic(ent)
        alpha = int(255*e)
        # scale x from 0
        scale = e
        bw = int(bar_w * scale)
        cx0 = cx - bw//2; cx1 = cx + bw//2
        rad = int(bar_h*0.22)
        _rounded_rect(draw, (cx0, y0, cx1, y1), radius=rad,
                      fill=(*style["card_bg"][:3], alpha),
                      outline=(*style["card_border"][:3], int(180*alpha/255)), width=1)

def _draw_checklist(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Talo-style progress list: title + vertical items with ✓ / ● / ○
    title = spec.get("title") or spec.get("header") or "TALO IS WORKING"
    items = spec.get("items") or spec.get("steps") or [
        {"label":"Finding companies","status":"done"},
        {"label":"Checking websites","status":"done"},
        {"label":"Finding founders","status":"done"},
        {"label":"Verifying LinkedIn profiles","status":"active"},
        {"label":"Enriching company data","status":"pending"},
        {"label":"Checking results","status":"pending"},
    ]
    # layout metrics
    is_portrait = config.PORTRAIT
    box_w = int(S["w"] * (0.56 if not is_portrait else 0.84))
    box_h = int(S["h"] * (0.58 if not is_portrait else 0.62))
    cx, cy = S["w"]//2, S["h"]//2
    x0, y0 = cx - box_w//2, cy - box_h//2
    x1, y1 = cx + box_w//2, cy + box_h//2
    # card bg
    entry = _ease_out_cubic(_clamp01(p/0.28))
    if entry<=0:
        return
    alpha = int(255*entry)
    rad = int(S["h"]*0.018)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=rad,
                  fill=(*style["card_bg"][:3], int(255*entry)) if len(style["card_bg"])==3 else style["card_bg"]+(int(255*entry),),
                  outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
    # title
    fs_title = max(18, int(S["h"]*0.024))
    f_title = _font(config.MGFX_FONT_BOLD, fs_title)
    title_alpha = int(255*_clamp01(p/0.25))
    # title centered, uppercase, muted small
    tw,_ = _text_bbox(title, f_title)
    draw.text((cx - tw//2, y0 + int(S["h"]*0.028)), title, font=f_title, fill=(*style["muted"][:3], title_alpha))
    # divider line
    div_y = y0 + int(S["h"]*0.072)
    draw.line((x0+int(S["w"]*0.025), div_y, x1-int(S["w"]*0.025), div_y), fill=(*style["card_border"][:3], int(160*alpha/255)), width=max(1,S["stroke"]//3))
    # items
    start_y = div_y + int(S["h"]*0.028)
    row_h = int(S["h"]*0.062)
    fs_item = max(16, int(S["h"]*0.022))
    f_item = _font(config.MGFX_FONT_REGULAR, fs_item)
    # status colors
    col_done = style["accent"]
    col_active = style["accent"]
    col_pending = style["muted"]
    for i, it in enumerate(items[:8]):
        label = str(it.get("label") or it.get("text") or "")
        status = str(it.get("status") or "pending").lower()
        # stagger
        ip = _clamp01((p - 0.18 - i*0.08)/0.32)
        if ip<=0:
            continue
        e = _ease_out_cubic(ip)
        ia = int(255*e)
        # row bg subtle highlight for active
        ry0 = start_y + i*row_h
        ry1 = ry0 + row_h - int(S["h"]*0.010)
        # row container
        rx0 = x0 + int(S["w"]*0.022)
        rx1 = x1 - int(S["w"]*0.022)
        # faint row bg for active item
        if status == "active" and ia>0:
            _rounded_rect(draw, (rx0, ry0, rx1, ry1), radius=int(row_h*0.22),
                          fill=(*style["pill_bg"][:3], int(110*ia/255)) if len(style["pill_bg"])==3 else style["pill_bg"][:3]+(int(110*ia/255),),
                          outline=None)
        # status icon: circle with symbol
        icon_cx = rx0 + int(S["h"]*0.022)
        icon_cy = (ry0+ry1)//2
        icon_r = int(S["h"]*0.018)
        if status == "done":
            # filled blue circle with white check
            draw.ellipse((icon_cx-icon_r, icon_cy-icon_r, icon_cx+icon_r, icon_cy+icon_r),
                         fill=(*col_done[:3], ia) if len(col_done)==3 else col_done[:3]+(ia,))
            # check mark
            # two segments
            p1=(icon_cx-int(icon_r*0.55), icon_cy)
            p2=(icon_cx-int(icon_r*0.10), icon_cy+int(icon_r*0.45))
            p3=(icon_cx+int(icon_r*0.65), icon_cy-int(icon_r*0.45))
            w = max(2, int(S["h"]*0.0045))
            draw.line((p1,p2), fill=(255,255,255,ia), width=w, joint="curve")
            draw.line((p2,p3), fill=(255,255,255,ia), width=w, joint="curve")
        elif status == "active":
            # blue ring with pulsating dot
            # outer ring
            draw.ellipse((icon_cx-icon_r, icon_cy-icon_r, icon_cx+icon_r, icon_cy+icon_r),
                         fill=None, outline=(*col_active[:3], ia), width=max(2,S["stroke"]//2))
            # inner dot with pulse scale
            pulse = 0.85 + 0.15*math.sin(p*12)
            pr = int(icon_r*0.55*pulse)
            draw.ellipse((icon_cx-pr, icon_cy-pr, icon_cx+pr, icon_cy+pr),
                         fill=(*col_active[:3], ia))
        else:  # pending
            draw.ellipse((icon_cx-icon_r, icon_cy-icon_r, icon_cx+icon_r, icon_cy+icon_r),
                         fill=None, outline=(*col_pending[:3], int(140*ia/255)), width=max(1,S["stroke"]//3))
            # inner empty dot
            pr = int(icon_r*0.35)
            draw.ellipse((icon_cx-pr, icon_cy-pr, icon_cx+pr, icon_cy+pr),
                         fill=(*col_pending[:3], int(70*ia/255)))
        # label
        tx = icon_cx + icon_r + int(S["w"]*0.016)
        # vertical center
        _, th = _text_bbox(label, f_item)
        ty = icon_cy - th//2 - f_item.getbbox(label)[1]//2
        # color: pending is muted, others fg
        col = style["fg"] if status != "pending" else style["muted"]
        # active is emphasized
        if status == "active":
            f_item_b = _font(config.MGFX_FONT_BOLD, fs_item)
            draw.text((tx, ty), label, font=f_item_b, fill=(*col[:3], ia))
        else:
            draw.text((tx, ty), label, font=f_item, fill=(*col[:3], ia))


def _draw_task_box(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Upgraded slick chat: glass card, inner prompt with gradient, logo watermark, animated pills
    query = spec.get("query") or spec.get("text") or "Find 500 US SaaS companies, their founders, funding and LinkedIn."
    estimate = spec.get("estimate") or "ESTIMATE: 6\u20138 HOURS"
    maximum = spec.get("maximum") or "MAXIMUM: $80"
    badge = spec.get("badge") or "TASK ACCEPTED"
    is_portrait = config.PORTRAIT
    card_w = int(S["w"] * (0.66 if not is_portrait else 0.86))
    card_h = int(S["h"] * (0.48 if not is_portrait else 0.44))
    cx, cy = S["w"]//2, S["h"]//2 - int(S["h"]*0.02)
    x0, y0 = cx - card_w//2, cy - card_h//2
    x1, y1 = cx + card_w//2, cy + card_h//2
    entry = _ease_out_cubic(_clamp01(p/0.30))
    if entry<=0:
        return
    alpha = int(255*entry)
    # soft drop shadow behind card (offset)
    shadow_off = int(S["h"]*0.012)
    _rounded_rect(draw, (x0+shadow_off, y0+shadow_off, x1+shadow_off, y1+shadow_off), radius=int(S["h"]*0.020),
                  fill=(0,0,0, int(18*alpha/255)), outline=None)
    rad = int(S["h"]*0.020)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=rad,
                  fill=(255,255,255, alpha),
                  outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
    # watermark overlay inside card but behind content — faint talo giant clipped to card
    # We'll draw cropped watermark centered inside card bounds at 0.06 opacity, clipped by card rect (simplified: just paste with low alpha)
    # Use temp watermark: draw big "talo" text faint diagonally across card center
    # To simulate overlay of logo in better way (per brief), place huge ghost logo centered behind chat
    wm_p = _clamp01((p-0.05)/0.35)
    if wm_p>0:
        we = _ease_out_cubic(wm_p)
        wa = int(14*we*alpha/255)
        f_wm = _font(config.FONT_REGULAR, max(60, int(card_h*0.42)))
        wmt = "talo"
        tw_wm,_ = _text_bbox(wmt, f_wm)
        # place at card center, rotated -12 deg simulated via image rotate for authenticity would need extra; keep straight but offset
        draw.text((cx - tw_wm//2 + int(S["w"]*0.01), cy - int(S["h"]*0.02) + int((1-we)*S["h"]*0.01)), wmt, font=f_wm, fill=(10,10,11, wa))
    # query area: inner glass box top
    inner_h = int(card_h*0.50)
    ix0, iy0 = x0+int(S["w"]*0.022), y0+int(S["h"]*0.022)
    ix1, iy1 = x1-int(S["w"]*0.022), y0+inner_h
    i_rad = int(inner_h*0.18)
    # inner gradient subtle: draw base pill_bg then highlight top edge
    _rounded_rect(draw, (ix0,iy0,ix1,iy1), radius=i_rad,
                  fill=(*style["pill_bg"][:3], int(220*alpha/255)) if len(style["pill_bg"])==3 else style["pill_bg"][:3]+(int(220*alpha/255),),
                  outline=(*style["card_border"][:3], int(140*alpha/255)), width=1)
    # highlight line at top of inner
    if entry>0.5:
        ha = int(60*(entry-0.5)/0.5)
        draw.line((ix0+int(S["w"]*0.02), iy0+int(S["h"]*0.004), ix1-int(S["w"]*0.02), iy0+int(S["h"]*0.004)), fill=(255,255,255, ha), width=1)
    # avatar + query text with typing
    type_p = _clamp01((p - 0.10)/0.55)
    nchars = int(len(query) * _ease_out_cubic(type_p) + 0.5) if type_p>0 else 0
    visible = query[:nchars]
    fs_q = max(16, int(S["h"]*0.024))
    f_q = _font(config.MGFX_FONT_REGULAR, fs_q)
    max_w = (ix1-ix0) - int(S["w"]*0.04) - int(S["h"]*0.045)  # leave avatar
    lines = []
    if visible:
        words = visible.split()
        cur=""
        for w in words:
            test = (cur+" "+w).strip()
            tw,_ = _text_bbox(test, f_q)
            if tw <= max_w or not cur:
                cur=test
            else:
                lines.append(cur); cur=w
        if cur:
            lines.append(cur)
        lh = f_q.getbbox("Ag")[3]-f_q.getbbox("Ag")[1] + int(S["h"]*0.010)
        total_h = len(lines)*lh
        base_y = (iy0+iy1)//2 - total_h//2
        # avatar circle left
        av_cx = ix0 + int(S["w"]*0.028)
        av_cy = (iy0+iy1)//2 - int(S["h"]*0.045)
        av_r = int(S["h"]*0.016)
        # online dot
        draw.ellipse((av_cx-av_r, av_cy-av_r, av_cx+av_r, av_cy+av_r), fill=(*style["fg"][:3], alpha))
        draw.text((av_cx - _text_bbox("U", _font(config.MGFX_FONT_BOLD, int(av_r*1.1)))[0]//2, av_cy - av_r//2 -1), "U", font=_font(config.MGFX_FONT_BOLD, int(av_r*1.1)), fill=(255,255,255, alpha))
        for li, ln in enumerate(lines[:2]):
            tw,_ = _text_bbox(ln, f_q)
            tx = ix0 + int(S["w"]*0.038) + int(S["h"]*0.035)
            ty = base_y + li*lh - f_q.getbbox(ln)[1]//2
            draw.text((tx, ty), ln, font=f_q, fill=(*style["fg"][:3], alpha))
        if nchars < len(query) and type_p < 0.99:
            last = lines[-1] if lines else ""
            tw_last,_ = _text_bbox(last, f_q)
            cur_on = (int(p*10) % 10) < 6
            if cur_on:
                cx_cur = ix0 + int(S["w"]*0.038) + int(S["h"]*0.035) + tw_last + int(S["w"]*0.004)
                cy_cur = base_y + (len(lines)-1)*lh
                _, th = _text_bbox("Ag", f_q)
                draw.line((cx_cur, cy_cur, cx_cur, cy_cur+th), fill=(*style["fg"][:3], alpha), width=max(2,S["stroke"]//2))
    # START WORK button — slick with glow and slide
    btn_p = _clamp01((p - 0.42)/0.28)
    if btn_p>0:
        e = _ease_out_back(btn_p) if btn_p<0.9 else _ease_out_cubic(btn_p)
        ba = int(255*e)
        btn_w = int(card_w*0.44)
        btn_h = int(S["h"]*0.068)
        bx0 = cx - btn_w//2
        by0 = iy1 + int(S["h"]*0.018)
        bx1 = cx + btn_w//2
        by1 = by0 + btn_h
        # glow shadow under button
        _rounded_rect(draw, (bx0, by0+int(S["h"]*0.008), bx1, by1+int(S["h"]*0.008)), radius=btn_h//2, fill=(*style["accent"][:3], int(30*ba/255)), outline=None)
        btn_rad = btn_h//2
        _rounded_rect(draw, (bx0,by0,bx1,by1), radius=btn_rad,
                      fill=(*style["accent"][:3], ba),
                      outline=None)
        # hover sheen (diagonal gradient band)
        if btn_p>0.6:
            sheen_p = _clamp01((p-0.60)/0.25)
            sheen_x = int(bx0 + (bx1-bx0)*_ease_out_cubic(sheen_p))
            draw.line((sheen_x, by0+int(btn_h*0.15), sheen_x-int(btn_h*0.3), by1-int(btn_h*0.15)), fill=(255,255,255, int(55*ba/255)), width=int(btn_h*0.18))
        fs_btn = max(14, int(S["h"]*0.020))
        f_btn = _font(config.MGFX_FONT_BOLD, fs_btn)
        txt = "START WORK \u2192"
        tw,_ = _text_bbox(txt, f_btn)
        draw.text((cx - tw//2, (by0+by1)//2 - f_btn.getbbox(txt)[3]//2), txt, font=f_btn, fill=(255,255,255,ba))
    # bottom badge row: TASK ACCEPTED + ESTIMATE + MAXIMUM pills
    badge_p = _clamp01((p - 0.60)/0.32)
    if badge_p>0:
        e = _ease_out_cubic(badge_p)
        ba = int(255*e)
        pill_y = y1 - int(S["h"]*0.042)
        fs_pill = max(12, int(S["h"]*0.017))
        f_pill = _font(config.MGFX_FONT_BOLD, fs_pill)
        texts = [badge, estimate, maximum]
        gap = int(S["w"]*0.012)
        pill_ws = []
        for t in texts:
            tw,_ = _text_bbox(t, f_pill)
            pill_ws.append(tw + int(S["w"]*0.028))
        total_w = sum(pill_ws) + gap*(len(texts)-1)
        cur_x = cx - total_w//2 + int((1-e)*S["w"]*0.008)
        for idx, t in enumerate(texts):
            pw = pill_ws[idx]
            px0 = cur_x
            px1 = cur_x + pw
            py0 = pill_y - int(S["h"]*0.022)
            py1 = pill_y + int(S["h"]*0.022)
            rad_p = int(S["h"]*0.022)
            if idx == 0:
                _rounded_rect(draw, (px0,py0,px1,py1), radius=rad_p,
                              fill=(*style["accent"][:3], ba),
                              outline=None)
                # pulse dot for live
                dot_r = int(S["h"]*0.006)
                dot_cx = px0 + int(S["w"]*0.010)
                dot_cy = (py0+py1)//2
                pulse = 0.7 + 0.3*math.sin(p*10)
                pr = int(dot_r*pulse)
                draw.ellipse((dot_cx-pr, dot_cy-pr, dot_cx+pr, dot_cy+pr), fill=(255,255,255, ba))
                draw.text((px0 + int(S["w"]*0.020), (py0+py1)//2 - f_pill.getbbox(t)[3]//2), t, font=f_pill, fill=(255,255,255,ba))
            else:
                _rounded_rect(draw, (px0,py0,px1,py1), radius=rad_p,
                              fill=(255,255,255, int(220*ba/255)),
                              outline=(*style["card_border"][:3], int(180*ba/255)), width=1)
                draw.text((px0 + int(S["w"]*0.014), (py0+py1)//2 - f_pill.getbbox(t)[3]//2), t, font=f_pill, fill=(*style["muted"][:3], ba))
            cur_x += pw + gap


def _draw_talo_hero(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Talo premium editorial lockup: line1 italic serif (muted), line2 bold sans with periwinkle accent
    # Upgraded: word-stagger, mask-type reveal, subtle paper watermark, acid-green dot for emphasis
    line1 = spec.get("line1") or spec.get("top") or "Have work to do?"
    line2 = spec.get("line2") or spec.get("bottom") or spec.get("text") or "Hire an AI Freelancer."
    if not spec.get("line1") and not spec.get("line2") and "\n" in line2:
        parts = line2.split("\n")
        if len(parts) >= 2:
            line1, line2 = parts[0], parts[1]
    accent = spec.get("accent") or "Freelancer."
    if isinstance(accent, list) and accent:
        accent = accent[0]
    is_portrait = config.PORTRAIT
    fs1 = max(22, int(S["h"] * (0.042 if not is_portrait else 0.038)))
    fs2 = max(28, int(S["h"] * (0.058 if not is_portrait else 0.048)))
    f1 = _font(config.MGFX_FONT_SERIF_ITALIC, fs1)
    f2 = _font(config.MGFX_FONT_BOLD, fs2)
    if style.get("accent") == config.TALO_ACCENT_RGB:
        col1 = style["muted"]
        col2_base = style["fg"]
        col_accent = style["accent"]
    else:
        col1 = (72, 72, 85)
        col2_base = (10, 10, 11)
        col_accent = config.TALO_ACCENT_RGB
    cx, cy = S["w"]//2, S["h"]//2
    # watermark behind (paper only)
    is_dark = style.get("bg", (255,255,255))[0] < 30
    if not is_dark and p>0.02:
        try:
            # we need overlay image, but draw is ImageDraw for overlay transparent; paste directly on overlay image
            # draw._image is private but we can get via overlay reference in caller; fallback to global watermark composited earlier in frame
            # Instead, rely on frame-level watermark already pasted before calling layouts (see render loop)
            pass
        except: pass
    # stagger entry
    e1 = _ease_out_cubic(_clamp01(p/0.32))
    e2 = _ease_out_cubic(_clamp01((p-0.16)/0.36))
    if e1 <= 0 and e2 <= 0:
        return
    gap = int(S["h"]*0.014)
    w1, h1 = _text_bbox(line1, f1) if e1>0 else (0,0)
    w2_full, h2 = _text_bbox(line2, f2) if e2>0 else (0,0)
    total_h = (h1 if e1>0 else 0) + (gap if e1>0 and e2>0 else 0) + (h2 if e2>0 else 0)
    base_y = cy - total_h//2
    if e1>0:
        # word stagger for line1 (italic muted)
        y1 = base_y + int((1-e1)*S["h"]*0.018)
        # use word stagger helper but muted color
        _draw_word_stagger(draw, line1, f1, cx, y1, _clamp01(p/0.36), S, col1, None, None, stagger=0.055, word_duration=0.32)
        # tiny acid-green rule underneath line1 that draws left->right after line1 lands
        rule_p = _clamp01((p-0.28)/0.28)
        if rule_p>0 and e1>0.7:
            rw = int(w1 * _ease_out_cubic(rule_p))
            rx0 = cx - w1//2
            ry = y1 + h1 + int(S["h"]*0.008)
            draw.line((rx0, ry, rx0+rw, ry), fill=(*config.TALO_ACID_RGB[:3], int(120*rule_p)), width=max(2, S["stroke"]//3))
    if e2>0:
        y2 = base_y + (h1 + gap if e1>0 else 0) + int((1-e2)*S["h"]*0.018)
        # handle accent per-word coloring
        words2 = line2.split()
        # accent may be phrase with spaces
        acc_words = set(accent.split()) if accent else set()
        space_w,_ = _text_bbox(" ", f2)
        word_ws = [_text_bbox(w, f2)[0] for w in words2]
        total_w = sum(word_ws) + space_w*(len(words2)-1)
        cur_x = cx - total_w//2
        for wi, w in enumerate(words2):
            wp = _clamp01((p - 0.16 - wi*0.052)/0.38)
            if wp<=0:
                cur_x += word_ws[wi] + space_w
                continue
            e = _ease_out_expo(wp)
            alpha = int(255*e)
            y_off = int((1-e)*S["h"]*0.015)
            is_acc = w.strip(".,!—") in acc_words or w in acc_words or (accent and accent.strip() == w) or (accent and w in accent and len(accent.split())==1 and accent.strip(".,") in w)
            # multi-word accent check: if line2 contains accent string and word is inside it
            if accent and accent in line2:
                is_acc = w in accent.split()
            col = col_accent if is_acc else col2_base
            # for accent, add subtle underline wipe
            draw.text((cur_x, y2+y_off), w, font=f2, fill=(*col[:3], alpha) if len(col)==3 else col+(alpha,))
            if is_acc and wp>0.72:
                uw = word_ws[wi]
                uy = y2 + y_off + h2 + int(S["h"]*0.004)
                up = _clamp01((wp-0.72)/0.28)
                ux1 = cur_x + int(uw * _ease_out_cubic(up))
                draw.line((cur_x, uy, ux1, uy), fill=(*col_accent[:3], int(160*alpha/255)), width=max(2, S["stroke"]//2))
            cur_x += word_ws[wi] + space_w



def _draw_talo_logo(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Black screen with white "talo" logotype — exact replica of Image 2
    # Now renders the *real* thin geometric logo from assets/talo_logo_white.png instead of font approximation
    bg_black = (0, 0, 0)
    e = _ease_out_cubic(_clamp01(p/0.28))
    if e <= 0:
        return
    a = int(255*e)
    draw.rectangle((0, 0, S["w"], S["h"]), fill=(0, 0, 0, a))
    # faint background talo watermark behind sharp logo (editorial overlay you requested)
    # Draw huge faint thin logo centered behind, 0.06 opacity, 0.72×W, before sharp
    try:
        wm_path = Path(config.TALO_LOGO_WHITE_PATH)
        if wm_path.exists():
            wm_src = Image.open(wm_path).convert("RGBA")
            # scale to 0.72×W for watermark (much larger than sharp 0.38×W)
            wm_target_w = int(S["w"] * 0.72)
            wm_scale = wm_target_w / wm_src.size[0]
            wm_target_h = int(wm_src.size[1] * wm_scale)
            wm_resized = wm_src.resize((wm_target_w, wm_target_h), Image.LANCZOS)
            # opacity 0.055 = editorial subtle (0.035 was too faint on black, 0.08 too strong)
            wm_op = 0.18 if not config.PORTRAIT else 0.12
            # combine with fade-in e
            eff_op = wm_op * _clamp01(p/0.45) * e
            if eff_op > 0.001:
                a = wm_resized.split()[3]
                a = a.point(lambda v: int(v * eff_op))
                wm_resized.putalpha(a)
                # rotate slightly -6deg like paper watermark for consistency
                if abs(float(getattr(config, "TALO_WATERMARK_ROT", -6.0))) > 0.5:
                    wm_resized = wm_resized.rotate(float(getattr(config, "TALO_WATERMARK_ROT", -6.0)), resample=Image.BICUBIC, expand=True)
                wm_cx, wm_cy = S["w"]//2, S["h"]//2 + int(S["h"]*0.02)
                wmx = wm_cx - wm_resized.size[0]//2
                wmy = wm_cy - wm_resized.size[1]//2
                draw._image.alpha_composite(wm_resized, dest=(wmx, wmy))
    except Exception as ex:
        pass  # watermark is optional
    # Try to composite the actual image logo (thin geometric) centred
    try:
        logo_path = Path(config.TALO_LOGO_WHITE_PATH) if Path(config.TALO_LOGO_WHITE_PATH).exists() else Path(config.TALO_LOGO_PATH)
        if logo_path.exists():
            logo_im = Image.open(logo_path).convert("RGBA")
            # logo_im is already tight-cropped transparent white; resize to target width
            is_portrait = config.PORTRAIT
            target_w = int(S["w"] * (0.38 if not is_portrait else 0.58))
            scale = target_w / logo_im.size[0]
            target_h = int(logo_im.size[1] * scale)
            logo_resized = logo_im.resize((target_w, target_h), Image.LANCZOS)
            # apply fade-in alpha
            if e < 1.0:
                # multiply alpha channel
                alpha = logo_resized.split()[3]
                alpha = alpha.point(lambda v: int(v * e))
                logo_resized.putalpha(alpha)
            # subtle rise: slide up 0.015H
            rise = int((1-e) * S["h"]*0.015)
            cx, cy = S["w"]//2, S["h"]//2
            lx = cx - target_w//2
            ly = cy - target_h//2 - int(S["h"]*0.04) - rise
            # need overlay image reference
            overlay_img = draw._image  # PIL ImageDraw holds _image
            overlay_img.alpha_composite(logo_resized, dest=(lx, ly))
        else:
            raise FileNotFoundError(logo_path)
        # we rendered via image, skip the font fallback below
        logo_rendered_via_image = True
    except Exception as ex:
        # fallback to font rendering if image missing
        logo_rendered_via_image = False
        # print(f"[talo_logo] image failed {ex}, fallback to font")
        logo = spec.get("text") or spec.get("logo") or "talo"
        logo = logo.lower()
        is_portrait = config.PORTRAIT
        fs_logo = max(80, int(S["h"] * (0.18 if not is_portrait else 0.12)))
        f_logo = _font(config.FONT_REGULAR, fs_logo)
        gap = int(fs_logo * 0.08)
        glyph_ws = []
        for ch in logo:
            w, _ = _text_bbox(ch, f_logo)
            glyph_ws.append(w)
        total_w = sum(glyph_ws) + gap*(len(logo)-1)
        cx, cy = S["w"]//2, S["h"]//2
        cur_x = cx - total_w//2
        rise = int((1-e)*S["h"]*0.015)
        y = cy - f_logo.getbbox("Ag")[3]//2 - rise
        for idx, ch in enumerate(logo):
            gp = _clamp01((p - idx*0.04)/0.32)
            if gp <= 0:
                cur_x += glyph_ws[idx] + gap
                continue
            ge = _ease_out_cubic(gp)
            ga = int(255*ge)
            draw.text((cur_x, y + int((1-ge)*S["h"]*0.010)), ch, font=f_logo, fill=(255, 255, 255, ga))
            cur_x += glyph_ws[idx] + gap
    # tagline handling continues below; need to handle both paths (image vs font) — we set cx,cy for tagline anchor
    # For image path, cx,cy already defined above; for font path they are too. So reuse.
    # Ensure cx,cy defined if image succeeded but early return not taken
    try:
        cx
    except:
        cx, cy = S["w"]//2, S["h"]//2
    tag = spec.get("tagline") or spec.get("sub")
    if tag and p > 0.42:
        tp = _clamp01((p-0.42)/0.34)
        if tp>0:
            te = _ease_out_cubic(tp)
            ta = int(255*te)
            fs_tag = max(12, int(S["h"]*0.016))
            f_tag = _font(config.MGFX_FONT_BOLD, fs_tag)
            # typewriter for tagline
            nchars = int(len(tag) * _ease_out_cubic(tp) + 0.5)
            vis = tag[:nchars]
            tw,_ = _text_bbox(vis, f_tag)
            ty = cy + int(S["h"]*0.12) + int((1-te)*S["h"]*0.012)
            # mono pill behind tagline like editorial footer
            pill_pad = int(S["w"]*0.014)
            pill_h = int(S["h"]*0.036)
            pill_w = tw + pill_pad*2 + int(S["w"]*0.01)
            px0 = S["w"]//2 - pill_w//2
            py0 = ty - int(S["h"]*0.012)
            px1 = px0 + pill_w
            py1 = py0 + pill_h
            # faint pill stroke
            _rounded_rect(draw, (px0,py0,px1,py1), radius=pill_h//2, fill=(255,255,255, int(12*ta/255)), outline=(255,255,255, int(30*ta/255)), width=1)
            draw.text((S["w"]//2 - tw//2, ty), vis, font=f_tag, fill=(210,210,215, ta))
            if nchars < len(tag):
                cur_on = (int(p*10) % 10) < 6
                if cur_on:
                    cx_cur = S["w"]//2 - tw//2 + tw + int(S["w"]*0.004)
                    draw.line((cx_cur, ty, cx_cur, ty+int(S["h"]*0.018)), fill=(255,255,255, ta), width=2)


def _clean_roundrect_perimeter(x0,y0,x1,y1,r):
    # sharp, no wobble
    r = min(r, (x1-x0)/2, (y1-y0)/2)
    pts=[]
    # approximate with 8 points per corner
    # we'll just use PIL rounded_rect but for stroke animation we need perimeter
    # Build perimeter as polyline of straight + arcs
    import math
    pts.append((x0+r, y0))
    pts.append((x1-r, y0))
    # arc TR
    for i in range(1,5):
        a = -math.pi/2 + math.pi/2 * i/4
        pts.append((x1-r + r*math.cos(a), y0+r + r*math.sin(a)))
    pts.append((x1, y0+r))
    pts.append((x1, y1-r))
    for i in range(1,5):
        a = 0 + math.pi/2 * i/4
        pts.append((x1-r + r*math.cos(a), y1-r + r*math.sin(a)))
    pts.append((x1-r, y1))
    pts.append((x0+r, y1))
    for i in range(1,5):
        a = math.pi/2 + math.pi/2 * i/4
        pts.append((x0+r + r*math.cos(a), y1-r + r*math.sin(a)))
    pts.append((x0, y1-r))
    pts.append((x0, y0+r))
    for i in range(1,5):
        a = math.pi + math.pi/2 * i/4
        pts.append((x0+r + r*math.cos(a), y0+r + r*math.sin(a)))
    pts.append((x0+r, y0))
    return pts

def _draw_node_clean(draw, node, p: float, style, S):
    x, y = node["x"]*S["w"], node["y"]*S["h"]
    w, h = node["w"]*S["w"], node["h"]*S["h"]
    # pick colors
    fg = style["accent"] if node.get("emph") else style["fg"]
    lw = S["stroke"]
    if node["shape"] != "plain":
        box_p = _clamp01(p/0.65)
        if node["shape"] == "oval":
            # draw oval perimeter animated
            perim = _oval_perimeter(x,y,w/2,h/2,0,0)
            seg = _partial(perim, box_p)
            if len(seg)>1:
                # fill background for card
                # we have to draw outline only, but for clean we fill slightly
                draw.line(seg, fill=(*fg[:3], int(255*box_p)) if len(fg)==3 else fg, width=lw, joint="curve")
        else:
            perim = _clean_roundrect_perimeter(x-w/2, y-h/2, x+w/2, y+h/2, r=min(w,h)*0.18)
            seg = _partial(perim, box_p)
            if len(seg)>1:
                # fill optional card bg behind
                # draw card bg first if p>0.2
                if box_p>0.2:
                    _rounded_rect(draw, (x-w/2, y-h/2, x+w/2, y+h/2), radius=min(w,h)*0.18,
                                  fill=(*style["card_bg"][:3], int(200*box_p)) if len(style["card_bg"])==3 else style["card_bg"]+(200,),
                                  outline=None)
                draw.line(seg, fill=(*fg[:3], int(255*box_p)) if len(fg)==3 else fg, width=lw, joint="curve")
        text_p = _clamp01((p-0.45)/0.45)
        if text_p>0 and node["label"]:
            label = node["label"]
            max_w = int(w*0.82); max_h = int(h*0.8)
            # fit fontsize
            base = S["node_fontsize"]
            fs = base
            while fs>12:
                f = _font(config.MGFX_FONT_BOLD, fs)
                tw,th = _text_bbox(label, f)
                if tw <= max_w and th <= max_h:
                    break
                if " " in label and tw > max_w and "\n" not in label:
                    # wrap
                    words = label.split()
                    mid=len(words)//2
                    label = " ".join(words[:mid])+"\n"+" ".join(words[mid:])
                    continue
                fs-=2
            f = _font(config.MGFX_FONT_BOLD, fs)
            alpha = int(255*text_p)
            lines = label.split("\n")
            line_h = f.getbbox("Ag")[3]-f.getbbox("Ag")[1] + 4
            total_h = line_h*len(lines)
            for li, ln in enumerate(lines):
                tw,_ = _text_bbox(ln, f)
                tx = x - tw/2
                ty = y - total_h/2 + li*line_h
                draw.text((tx,ty), ln, font=f, fill=(*fg[:3], alpha) if len(fg)==3 else fg+(alpha,))
    else:
        text_p = _clamp01(p/0.75)
        if text_p>0 and node["label"]:
            label = node["label"]
            fs = S["node_fontsize"]
            f = _font(config.MGFX_FONT_BOLD, fs)
            alpha=int(255*text_p)
            tw,th = _text_bbox(label,f)
            draw.text((x-tw/2, y-th/2), label, font=f, fill=(*fg[:3], alpha))

def _draw_arrow_clean(draw, arrow, nodes_by_id, p: float, style, S):
    src = nodes_by_id.get(arrow["from"]); dst=nodes_by_id.get(arrow["to"])
    if not src or not dst: return
    # anchors: edge center
    def anchor(node, toward):
        cx,cy=node["x"],node["y"]
        tx,ty=toward
        dx,dy=tx-cx, ty-cy
        if node["shape"]=="plain": return cx,cy
        hw,hh=node["w"]/2, node["h"]/2
        if abs(dx)*S["h"] > abs(dy)*S["w"]:
            return cx+(hw if dx>0 else -hw), cy
        return cx, cy+(hh if dy>0 else -hh)
    a_frac = anchor(src, (dst["x"], dst["y"]))
    b_frac = anchor(dst, (src["x"], src["y"]))
    a = (a_frac[0]*S["w"], a_frac[1]*S["h"])
    b = (b_frac[0]*S["w"], b_frac[1]*S["h"])
    # elbow support
    if arrow.get("path")=="elbow":
        midx = a[0] + (b[0]-a[0])*0.5
        pts = [a, (midx,a[1]), (midx,b[1]), b]
        # expand wobble-free: interpolate straight segments
        # convert to polyline with intermediate points for smooth partial
        full=[]
        for i in range(len(pts)-1):
            full.extend(_wobble_pts(pts[i], pts[i+1], 0, 0, n=4)[:-1])
        full.append(b)
        pts=full
    else:
        pts = _wobble_pts(a,b,0,0,n=12)
    shaft_p = _clamp01(p/0.82)
    seg = _partial(pts, shaft_p)
    if len(seg)>1:
        draw.line(seg, fill=(*style["fg"][:3],255) if len(style["fg"])==3 else style["fg"], width=S["stroke"], joint="curve")
    if p>0.82:
        (x0,y0),(x1,y1)=pts[-2],pts[-1]
        _arrow_head(draw,(x1,y1), math.atan2(y1-y0,x1-x0), size=S["stroke"]*3.2, color=style["fg"], width=S["stroke"])
    label=arrow.get("label")
    if label and p>0.55:
        mid=pts[len(pts)//2]
        fs=max(12, int(S["node_fontsize"]*0.62))
        f=_font(config.MGFX_FONT_BOLD, fs)
        tw,_=_text_bbox(label,f)
        # draw label with bg pill
        bg_pad=6
        _rounded_rect(draw,(mid[0]-tw//2-bg_pad, mid[1]-10, mid[0]+tw//2+bg_pad, mid[1]+10), radius=6,
                      fill=(*style["card_bg"][:3],220))
        alpha=int(255*_clamp01((p-0.55)/0.3))
        draw.text((mid[0]-tw//2, mid[1]-f.getbbox(label)[3]//2), label, font=f, fill=(*style["muted"][:3],alpha))

def _draw_callout_clean(draw, callout, p: float, style, S):
    text_p=_clamp01(p/0.75)
    if text_p<=0: return
    fs=int(S["card_h"]*(config.GFX_STAT_FONTSIZE_FRAC if callout.get("big") else config.GFX_CALLOUT_FONTSIZE_FRAC))
    fs=max(14,fs)
    f=_font(config.GFX_STAT_FONT if callout.get("big") else config.MGFX_FONT_BOLD, fs)
    text=callout["text"]
    tw,th=_text_bbox(text,f)
    x=callout["x"]*S["w"] - tw/2
    y=callout["y"]*S["h"] - th/2 - (1-text_p)*S["card_h"]*0.02
    color=style["accent"] if callout.get("emph") else style["fg"]
    alpha=int(255*text_p)
    draw.text((x,y), text, font=f, fill=(*color[:3],alpha) if len(color)==3 else color+(alpha,))

# ── legacy crisp diagram renderer (wrapper) ──────────────────────────────
def _render_diagram_clean_frames(spec, style, S, elements, nodes_by_id, duration, fps):
    # returns frames generator logic used by renderer; we implement per-frame in main loop
    pass

# ── main dispatcher ──────────────────────────────────────────────────────

# ── editorial + infographic layouts (Talo film) ──────────────────────────

def _draw_chat_slick(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Alias to task_box slick but with chat bubble history
    # spec keys: query, response, badge, estimate, maximum
    # We'll reuse _draw_task_box logic via delegation with extra response bubble
    _draw_task_box(draw, spec, p, style, S)
    # additional response bubble below pills if spec has response
    resp = spec.get("response") or spec.get("talo_response")
    if resp and p>0.62:
        rp = _clamp01((p-0.62)/0.32)
        if rp>0:
            e = _ease_out_cubic(rp)
            ra = int(255*e)
            is_portrait = config.PORTRAIT
            card_w = int(S["w"] * (0.66 if not is_portrait else 0.86))
            cx, cy = S["w"]//2, S["h"]//2
            # response bubble just above card? Actually inside card bottom - we draw floating pill overlapping
            bw = int(S["w"]*0.42)
            bh = int(S["h"]*0.055)
            bx0 = cx - bw//2
            by0 = int(S["h"]*0.78)
            bx1, by1 = bx0+bw, by0+bh
            _rounded_rect(draw, (bx0, by0-int((1-e)*S["h"]*0.015), bx1, by1-int((1-e)*S["h"]*0.015)), radius=bh//2,
                          fill=(*config.TALO_ACID_RGB[:3], ra) if not config.PORTRAIT else (*style["accent"][:3], ra),
                          outline=None)
            fs = max(14, int(S["h"]*0.018))
            f = _font(config.MGFX_FONT_BOLD, fs)
            tw,_ = _text_bbox(resp, f)
            draw.text((cx - tw//2, (by0+by1)//2 - f.getbbox(resp)[3]//2 - int((1-e)*S["h"]*0.015)), resp, font=f, fill=(6,6,7, ra) if not config.PORTRAIT else (255,255,255, ra))

def _draw_browser_work(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Browser mock + checklist overlay + ticker counter + runtime/cost pills
    # Style: light paper bg with watermark, browser window centered (66% width, 62% height)
    # spec: title, items, ticker fields: count_start/end, pill, runtime, cost
    is_portrait = config.PORTRAIT
    bw = int(S["w"] * (0.68 if not is_portrait else 0.88))
    bh = int(S["h"] * (0.62 if not is_portrait else 0.66))
    cx, cy = S["w"]//2, S["h"]//2
    x0, y0 = cx - bw//2, cy - bh//2 + int(S["h"]*0.02)
    x1, y1 = cx + bw//2, cy + bh//2 + int(S["h"]*0.02)
    entry = _ease_out_cubic(_clamp01(p/0.28))
    if entry<=0:
        return
    alpha = int(255*entry)
    # browser shadow
    _rounded_rect(draw, (x0+int(S["h"]*0.012), y0+int(S["h"]*0.012), x1+int(S["h"]*0.012), y1+int(S["h"]*0.012)), radius=int(S["h"]*0.016), fill=(0,0,0, int(16*alpha/255)), outline=None)
    # browser chrome
    rad = int(S["h"]*0.016)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=rad, fill=(255,255,255, alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
    # top bar (32px)
    bar_h = int(S["h"]*0.055)
    _rounded_rect(draw, (x0, y0, x1, y0+bar_h), radius=rad, fill=(*style["pill_bg"][:3], alpha), outline=None)
    # mask bottom corners of bar so only top rounded
    draw.rectangle((x0, y0+bar_h//2, x1, y0+bar_h), fill=(*style["pill_bg"][:3], alpha))
    # traffic lights
    dot_r = int(S["h"]*0.007)
    gap = int(S["w"]*0.008)
    sx = x0 + int(S["w"]*0.016)
    sy = y0 + bar_h//2
    for i, col in enumerate([(255,95,87),(255,189,46),(40,200,64)]):
        cxd = sx + i*(dot_r*2+gap)
        draw.ellipse((cxd-dot_r, sy-dot_r, cxd+dot_r, sy+dot_r), fill=(*col, alpha))
    # address pill
    pill_w = int(bw*0.42)
    pill_h = int(bar_h*0.58)
    px0 = cx - pill_w//2
    py0 = y0 + (bar_h - pill_h)//2
    px1, py1 = px0+pill_w, py0+pill_h
    _rounded_rect(draw, (px0,py0,px1,py1), radius=pill_h//2, fill=(255,255,255, int(220*alpha/255)), outline=(*style["card_border"][:3], int(140*alpha/255)), width=1)
    fs_addr = max(11, int(S["h"]*0.015))
    f_addr = _font(config.MGFX_FONT_REGULAR, fs_addr)
    addr = spec.get("url") or "talo.abstraklabs.com  \u2022  Working..."
    tw,_ = _text_bbox(addr, f_addr)
    draw.text((cx - tw//2, py0 + pill_h//2 - f_addr.getbbox(addr)[3]//2), addr, font=f_addr, fill=(*style["muted"][:3], int(180*alpha/255)))
    # viewport: subtle grid of spreadsheet rows blurred behind overlay
    vp_y0 = y0+bar_h + int(S["h"]*0.008)
    vp_y1 = y1 - int(S["h"]*0.015)
    # draw faint rows
    rows = 7
    row_h = (vp_y1 - vp_y0) // rows
    for ri in range(rows):
        ry0 = vp_y0 + ri*row_h + int(S["h"]*0.006)
        ry1 = ry0 + row_h - int(S["h"]*0.008)
        # opacity fades with progress (rows appear)
        rp = _clamp01((p - 0.18 - ri*0.04)/0.30)
        if rp<=0:
            continue
        ra = int(90* _ease_out_cubic(rp) * alpha/255)
        _rounded_rect(draw, (x0+int(S["w"]*0.012), ry0, x1-int(S["w"]*0.012), ry1), radius=int(S["h"]*0.006), fill=(245,245,248, ra), outline=None)
        # lines inside row (3 columns)
        cols = [int(bw*0.32), int(bw*0.22), int(bw*0.28)]
        cxr = x0+int(S["w"]*0.020)
        for ci, cw in enumerate(cols):
            lw = int(cw * (0.6 + 0.2*(ri%2)))
            ly = (ry0+ry1)//2 - int(S["h"]*0.006)
            _rounded_rect(draw, (cxr, ly, cxr+lw, ly+int(S["h"]*0.012)), radius=int(S["h"]*0.006), fill=(225,227,235, int(180*ra/255)), outline=None)
            cxr += cw + int(S["w"]*0.012)
    # overlay typography: left checklist (mini) + center ticker + right runtime/cost
    # checklist on left inside browser (vertical)
    items = spec.get("items") or [
        {"label":"SEARCHING COMPANIES","status":"done"},
        {"label":"VERIFYING WEBSITES","status":"done"},
        {"label":"MATCHING FOUNDERS","status":"done"},
        {"label":"ENRICHING DATA","status":"active"},
    ]
    # checklist pill container on left top of viewport
    chk_x0 = x0 + int(S["w"]*0.016)
    chk_y0 = vp_y0 + int(S["h"]*0.015)
    fs_chk = max(10, int(S["h"]*0.014))
    f_chk = _font(config.MGFX_FONT_BOLD, fs_chk)
    f_chk_reg = _font(config.MGFX_FONT_REGULAR, fs_chk)
    for idx, it in enumerate(items[:4]):
        ip = _clamp01((p - 0.20 - idx*0.07)/0.28)
        if ip<=0: continue
        e = _ease_out_cubic(ip)
        ia = int(220*e*alpha/255)
        y = chk_y0 + idx*int(S["h"]*0.028)
        lab = it.get("label","")
        status = it.get("status","pending")
        tw,_ = _text_bbox(lab, f_chk)
        # icon
        ic_r = int(S["h"]*0.008)
        icx = chk_x0 + ic_r
        icy = y + int(S["h"]*0.007)
        if status=="done":
            draw.ellipse((icx-ic_r, icy-ic_r, icx+ic_r, icy+ic_r), fill=(*config.TALO_ACID_RGB[:3], ia))
            # check small
            p1 = (icx-int(ic_r*0.45), icy)
            p2 = (icx-int(ic_r*0.05), icy+int(ic_r*0.35))
            p3 = (icx+int(ic_r*0.55), icy-int(ic_r*0.35))
            draw.line((p1,p2), fill=(6,6,7, ia), width=1)
            draw.line((p2,p3), fill=(6,6,7, ia), width=1)
        elif status=="active":
            # pulsing outline
            draw.ellipse((icx-ic_r, icy-ic_r, icx+ic_r, icy+ic_r), fill=None, outline=(*config.TALO_ACCENT_RGB[:3], ia), width=1)
            pr = int(ic_r*0.55* (0.8+0.2*math.sin(p*10)))
            draw.ellipse((icx-pr, icy-pr, icx+pr, icy+pr), fill=(*config.TALO_ACCENT_RGB[:3], ia))
        else:
            draw.ellipse((icx-ic_r, icy-ic_r, icx+ic_r, icy+ic_r), fill=None, outline=(*style["muted"][:3], int(120*ia/255)), width=1)
        tx = chk_x0 + ic_r*2 + int(S["w"]*0.008)
        draw.text((tx, y), lab, font=f_chk if status!="pending" else f_chk_reg, fill=(*style["fg"][:3], ia) if status!="pending" else (*style["muted"][:3], ia))
        if status=="done":
            # tiny check after label
            pass
    # center ticker: huge number counting 127 -> 1000 etc
    ticker_start = int(spec.get("count_start") or 127)
    ticker_end = int(spec.get("count_end") or spec.get("count") or 1000)
    ticker_label = spec.get("ticker_label") or "companies"
    # interpolate with ease
    tick_p = _clamp01((p - 0.24)/0.60)
    if tick_p>0:
        e = _ease_out_cubic(tick_p)
        # stepped count (snap to integer)
        cur_val = int(ticker_start + (ticker_end - ticker_start) * e)
        # choose font huge
        fs_big = max(40, int(S["h"]*0.085))
        if is_portrait:
            fs_big = max(32, int(S["h"]*0.055))
        f_big = _font(config.MGFX_FONT_BLACK, fs_big)
        txt = f"{cur_val:,}"
        tw,_ = _text_bbox(txt, f_big)
        tx = cx - tw//2
        ty = int((vp_y0+vp_y1)//2 - int(S["h"]*0.04))
        # shadow glow behind number
        # draw faint acid pill behind
        pill_pad = int(S["w"]*0.02)
        pill_h2 = int(S["h"]*0.052)
        _rounded_rect(draw, (tx-pill_pad, ty-int(S["h"]*0.012), tx+tw+pill_pad, ty+int(S["h"]*0.085)+int(S["h"]*0.008)), radius=pill_h2//2, fill=(255,255,255, int(170*alpha/255)), outline=(*config.TALO_ACID_RGB[:3], int(90*alpha/255)), width=1)
        draw.text((tx, ty), txt, font=f_big, fill=(*style["fg"][:3], alpha))
        # label below
        fs_lab = max(13, int(S["h"]*0.017))
        f_lab = _font(config.MGFX_FONT_BOLD, fs_lab)
        lab_txt = ticker_label.upper()
        tw2,_ = _text_bbox(lab_txt, f_lab)
        draw.text((cx - tw2//2, ty + int(S["h"]*0.095)), lab_txt, font=f_lab, fill=(*style["muted"][:3], int(200*alpha/255)))
        # tick marks sequence dots below (like 127 284 516 ... animated)
        seq = spec.get("sequence") or [127,284,516,793,1000]
        seq_p = tick_p
        # show dots fading
        dot_y = ty + int(S["h"]*0.125)
        for si, val in enumerate(seq):
            sp = _clamp01((tick_p - si*0.16)/0.20)
            if sp<=0: continue
            sa = int(160* _ease_out_cubic(sp) * alpha/255)
            dot_x = cx - int((len(seq)-1)*int(S["w"]*0.012))//2 + si*int(S["w"]*0.024)
            dr = int(S["h"]*0.004) if val!=cur_val else int(S["h"]*0.006)
            col = config.TALO_ACCENT_RGB if val==cur_val else (180,180,190)
            draw.ellipse((dot_x-dr, dot_y-dr, dot_x+dr, dot_y+dr), fill=(*col[:3], sa))
    # runtime + cost pills top-right inside browser
    if p>0.28:
        rp = _clamp01((p-0.28)/0.30)
        e = _ease_out_cubic(rp)
        ra = int(255*e)
        pill_h3 = int(S["h"]*0.036)
        # runtime
        rt = spec.get("runtime") or "03h 47m"
        cost = spec.get("cost") or "CURRENT COST $37.83"
        fs_p = max(11, int(S["h"]*0.014))
        f_p = _font(config.MGFX_FONT_BOLD, fs_p)
        f_m = _font(config.MGFX_FONT_REGULAR, fs_p)
        # runtime pill (dark with white text and acid dot)
        rtw,_ = _text_bbox(rt, f_p)
        rpw = rtw + int(S["w"]*0.028)
        rpx1 = x1 - int(S["w"]*0.016)
        rpx0 = rpx1 - rpw
        rpy0 = vp_y0 + int(S["h"]*0.008)
        rpy1 = rpy0 + pill_h3
        _rounded_rect(draw, (rpx0, rpy0-int((1-e)*S["h"]*0.01), rpx1, rpy1-int((1-e)*S["h"]*0.01)), radius=pill_h3//2,
                      fill=(*config.TALO_OLIVE_RGB[:3], ra), outline=None)
        dot_r2 = int(S["h"]*0.005)
        draw.ellipse((rpx0+int(S["w"]*0.008)-dot_r2, (rpy0+rpy1)//2-dot_r2, rpx0+int(S["w"]*0.008)+dot_r2, (rpy0+rpy1)//2+dot_r2), fill=(*config.TALO_ACID_RGB[:3], ra))
        draw.text((rpx0+int(S["w"]*0.016), (rpy0+rpy1)//2 - f_p.getbbox(rt)[3]//2), rt, font=f_p, fill=(255,255,255, ra))
        # cost pill below runtime
        if cost and p>0.34:
            cp = _clamp01((p-0.34)/0.28)
            ce = _ease_out_cubic(cp)
            ca = int(255*ce)
            ctw,_ = _text_bbox(cost, f_m)
            cpw = ctw + int(S["w"]*0.022)
            cpx1 = rpx1
            cpx0 = cpx1 - cpw
            cpy0 = rpy1 + int(S["h"]*0.008)
            cpy1 = cpy0 + pill_h3
            _rounded_rect(draw, (cpx0, cpy0-int((1-ce)*S["h"]*0.01), cpx1, cpy1-int((1-ce)*S["h"]*0.01)), radius=pill_h3//2,
                          fill=(255,255,255, ca), outline=(*style["card_border"][:3], int(160*ca/255)), width=1)
            # acid price highlight if contains $
            if "$" in cost:
                idx = cost.index("$")
                before = cost[:idx]
                price = cost[idx:]
                bw2,_ = _text_bbox(before, f_m)
                draw.text((cpx0+int(S["w"]*0.010), (cpy0+cpy1)//2 - f_m.getbbox(cost)[3]//2 - int((1-ce)*S["h"]*0.01)), before, font=f_m, fill=(*style["muted"][:3], ca))
                draw.text((cpx0+int(S["w"]*0.010)+bw2, (cpy0+cpy1)//2 - f_m.getbbox(price)[3]//2 - int((1-ce)*S["h"]*0.01)), price, font=_font(config.MGFX_FONT_BOLD, fs_p), fill=(*style["fg"][:3], ca))
            else:
                draw.text((cpx0+int(S["w"]*0.010), (cpy0+cpy1)//2 - f_m.getbbox(cost)[3]//2), cost, font=f_m, fill=(*style["muted"][:3], ca))

def _draw_spreadsheet_result(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Spreadsheet infographic: header + rows animate, then DONE stamp, stats 4h18m $43
    is_portrait = config.PORTRAIT
    sheet_w = int(S["w"] * (0.66 if not is_portrait else 0.88))
    sheet_h = int(S["h"] * (0.52 if not is_portrait else 0.48))
    cx, cy = S["w"]//2, S["h"]//2 - int(S["h"]*0.04)
    x0, y0 = cx - sheet_w//2, cy - sheet_h//2
    x1, y1 = cx + sheet_w//2, cy + sheet_h//2
    entry = _ease_out_cubic(_clamp01(p/0.30))
    if entry<=0: return
    alpha = int(255*entry)
    # sheet shadow
    _rounded_rect(draw, (x0+int(S["h"]*0.010), y0+int(S["h"]*0.010), x1+int(S["h"]*0.010), y1+int(S["h"]*0.010)), radius=int(S["h"]*0.014), fill=(0,0,0, int(14*alpha/255)), outline=None)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.014), fill=(255,255,255, alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
    # header row (dark olive or accent)
    hdr_h = int(sheet_h*0.14)
    _rounded_rect(draw, (x0, y0, x1, y0+hdr_h), radius=int(S["h"]*0.014), fill=(*config.TALO_OLIVE_RGB[:3], alpha), outline=None)
    draw.rectangle((x0, y0+hdr_h//2, x1, y0+hdr_h), fill=(*config.TALO_OLIVE_RGB[:3], alpha))
    # header cols
    headers = spec.get("headers") or ["COMPANY","FOUNDER","FUNDING","LINKEDIN"]
    cols = len(headers)
    col_w = sheet_w // cols
    fs_hdr = max(10, int(S["h"]*0.014))
    f_hdr = _font(config.MGFX_FONT_BOLD, fs_hdr)
    for i, h in enumerate(headers):
        tx0 = x0 + i*col_w + int(S["w"]*0.012)
        tw,_ = _text_bbox(h, f_hdr)
        ty = y0 + hdr_h//2 - f_hdr.getbbox(h)[3]//2
        draw.text((tx0, ty), h, font=f_hdr, fill=(255,255,255, int(220*alpha/255)))
        if i < cols-1:
            # divider
            draw.line((x0+(i+1)*col_w, y0+int(S["h"]*0.008), x0+(i+1)*col_w, y0+hdr_h-int(S["h"]*0.008)), fill=(255,255,255, int(30*alpha/255)), width=1)
    # rows
    rows = spec.get("rows") or 6
    row_h = (sheet_h - hdr_h) // rows
    row_vals = spec.get("row_data") or [
        ["Linear","Karri Saarinen","$ 52M","in/karri"],
        ["Perplexity","Aravind Srinivas","$ 73M","in/aravind"],
        ["Retool","David Hsu","$ 50M","in/david"],
        ["Vercel","Guillermo Rauch","$ 163M","in/guillermo"],
        ["Supabase","Paul Copplestone","$ 116M","in/paul"],
        ["Raycast","Thomas Paul","$ 15M","in/thomas"],
    ]
    fs_row = max(11, int(S["h"]*0.015))
    f_row = _font(config.MGFX_FONT_REGULAR, fs_row)
    f_row_b = _font(config.MGFX_FONT_BOLD, fs_row)
    for ri in range(rows):
        rp = _clamp01((p - 0.18 - ri*0.06)/0.28)
        if rp<=0: continue
        e = _ease_out_cubic(rp)
        ra = int(255*e)
        ry0 = y0 + hdr_h + ri*row_h
        ry1 = ry0 + row_h
        # zebra
        if ri%2==1:
            _rounded_rect(draw, (x0+1, ry0, x1-1, ry1), radius=0, fill=(248,248,252, int(180*ra/255)), outline=None)
        else:
            draw.line((x0, ry1-1, x1, ry1-1), fill=(*style["card_border"][:3], int(40*ra/255)), width=1)
        # row slide in from right
        x_off = int((1-e)* S["w"]*0.015)
        for ci in range(cols):
            tx0 = x0 + ci*col_w + int(S["w"]*0.012) + x_off
            txt = row_vals[ri % len(row_vals)][ci] if ri < len(row_vals) else ("\u2022 \u2022 \u2022")
            # funding accent in acid if $ ?
            is_money = "$" in txt
            ty = (ry0+ry1)//2 - f_row.getbbox(txt)[3]//2
            if is_money:
                draw.text((tx0, ty), txt, font=f_row_b, fill=(*config.TALO_OLIVE_RGB[:3], ra))
            else:
                col = style["fg"] if ci==0 else style["muted"]
                draw.text((tx0, ty), txt, font=f_row if ci!=0 else f_row_b, fill=(*col[:3], ra))
    # DONE stamp — large rotated acid-green badge that slams in
    done_p = _clamp01((p-0.42)/0.24)
    if done_p>0:
        e = _ease_out_back(done_p) if done_p<0.85 else _ease_out_cubic(done_p)
        da = int(255*e)
        # stamp centered over sheet, rotated -8 deg
        stamp_w = int(sheet_w*0.42)
        stamp_h = int(S["h"]*0.088)
        sx0 = cx - stamp_w//2 + int((1-e)*S["w"]*0.01)
        sy0 = cy - stamp_h//2 + int((1-e)*S["h"]*0.01)
        sx1, sy1 = sx0+stamp_w, sy0+stamp_h
        # need rotate: create temp image, rotate, paste
        stamp_img = Image.new("RGBA", (stamp_w, stamp_h), (0,0,0,0))
        sd = ImageDraw.Draw(stamp_img)
        _rounded_rect(sd, (0,0,stamp_w,stamp_h), radius=stamp_h//2, fill=(*config.TALO_ACID_RGB[:3], da), outline=None)
        f_done = _font(config.MGFX_FONT_BLACK, int(stamp_h*0.52))
        tw,_ = _text_bbox("DONE \u2022", f_done)
        sd.text(((stamp_w-tw)//2, (stamp_h - f_done.getbbox("DONE")[3])//2 - int(stamp_h*0.04)), "DONE \u2022", font=f_done, fill=(6,6,7, da))
        # rotate
        stamp_rot = stamp_img.rotate(-8, resample=Image.BICUBIC, expand=True)
        # paste centered
        px = cx - stamp_rot.size[0]//2
        py = cy - stamp_rot.size[1]//2
        # draw onto overlay directly via composite? We are drawing on overlay ImageDraw; need to alpha_composite temp
        # Get overlay image reference
        try:
            overlay_img = draw._image  # PIL ImageDraw stores _image
            overlay_img.alpha_composite(stamp_rot, dest=(px, py))
        except:
            # fallback blit via paste
            pass
    # stats below sheet: 4h 18m  $43.00  "You paid for the work actually performed."
    stats_p = _clamp01((p-0.60)/0.32)
    if stats_p>0:
        e = _ease_out_cubic(stats_p)
        sa = int(255*e)
        fs_stat = max(22, int(S["h"]*0.038))
        f_stat = _font(config.MGFX_FONT_BLACK, fs_stat)
        f_stat_acid = _font(config.MGFX_FONT_BLACK, int(fs_stat*1.05))
        # two stats side by side: time and price
        time_txt = spec.get("time") or "4h 18m"
        price_txt = spec.get("price") or "$43.00"
        # measure
        tw_t,_ = _text_bbox(time_txt, f_stat)
        tw_p,_ = _text_bbox(price_txt, f_stat_acid)
        gap = int(S["w"]*0.03)
        total = tw_t+tw_p+gap + int(S["w"]*0.02)  # plus divider
        sx = cx - total//2
        sy = y1 + int(S["h"]*0.04) + int((1-e)*S["h"]*0.012)
        draw.text((sx, sy), time_txt, font=f_stat, fill=(*style["fg"][:3], sa))
        # divider dot acid
        dot_x = sx+tw_t+gap//2
        dot_y = sy + int(S["h"]*0.018)
        draw.ellipse((dot_x-int(S["h"]*0.005), dot_y-int(S["h"]*0.005), dot_x+int(S["h"]*0.005), dot_y+int(S["h"]*0.005)), fill=(*config.TALO_ACID_RGB[:3], sa))
        draw.text((sx+tw_t+gap, sy), price_txt, font=f_stat_acid, fill=(*config.TALO_ACID_RGB[:3], sa) if style["bg"][:3]!=(6,6,7) else (214,255,44, sa))
        # subline
        sub = spec.get("sub") or "You paid for the work actually performed."
        fs_sub = max(12, int(S["h"]*0.016))
        f_sub = _font(config.MGFX_FONT_REGULAR, fs_sub)
        tw3,_ = _text_bbox(sub, f_sub)
        draw.text((cx - tw3//2, sy + int(S["h"]*0.05)), sub, font=f_sub, fill=(*style["muted"][:3], int(180*sa/255)))
        # small tally above stats: "1,000 QUALIFIED COMPANIES ..."
        tally = spec.get("tally") or "1,000 COMPANIES  \u2022  1,000 FOUNDERS  \u2022  FUNDING  \u2022  LINKEDIN"
        fs_t = max(11, int(S["h"]*0.014))
        f_t = _font(config.MGFX_FONT_BOLD, fs_t)
        twt,_ = _text_bbox(tally, f_t)
        draw.text((cx - twt//2, y1 + int(S["h"]*0.015) + int((1-e)*S["h"]*0.008)), tally, font=f_t, fill=(*style["muted"][:3], int(160*sa/255)))

def _draw_chaos_cards(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Three chaotic cards: HIRE SOMEONE / DO IT YOURSELF / USE AN AI TOOL
    # They fly in with rotation, then freeze with "THERE'S ANOTHER WAY." overlay
    cards = spec.get("cards") or [
        {"title":"HIRE SOMEONE","price":"$18\u201330/hr","foot":"WAITING..."},
        {"title":"DO IT YOURSELF","price":"4 HOURS","foot":"YOUR TIME"},
        {"title":"USE AN AI TOOL","price":"YOU STILL HAVE TO","foot":"OPERATE IT"},
    ]
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2 - int(S["h"]*0.04)
    card_w = int(S["w"] * (0.26 if not is_portrait else 0.42))
    card_h = int(S["h"] * (0.28 if not is_portrait else 0.22))
    gap = int(S["w"]*0.03)
    total_w = len(cards)*card_w + (len(cards)-1)*gap
    start_x = cx - total_w//2
    for idx, c in enumerate(cards):
        # chaotic entry: each card from different angle
        delay = idx*0.08
        cp = _clamp01((p - 0.05 - delay)/0.42)
        if cp<=0: continue
        e = _ease_out_back(cp) if cp<0.88 else _ease_out_cubic(cp)
        alpha = int(255* _clamp01(cp*1.2))
        # rotation: chaotic tilt that settles to 0
        rot_deg = _lerp((-8 if idx==0 else 7 if idx==1 else -6), 0, e) + (1-e)* (5 if idx%2==0 else -5)
        # position
        base_x0 = start_x + idx*(card_w+gap)
        base_y0 = cy - card_h//2
        # y jitter while flying
        y_off = int((1-e) * S["h"]*0.06 * (1 if idx%2==0 else -1))
        x_off = int((1-e) * S["w"]*0.04 * ( -1 if idx==0 else 1 if idx==2 else 0))
        x0, y0 = base_x0 + x_off, base_y0 + y_off
        x1, y1 = x0+card_w, y0+card_h
        # shadow
        _rounded_rect(draw, (x0+int(S["h"]*0.008), y0+int(S["h"]*0.008), x1+int(S["h"]*0.008), y1+int(S["h"]*0.008)), radius=int(S["h"]*0.014), fill=(0,0,0, int(12*alpha/255)), outline=None)
        # card bg: white with subtle border, tilted? For now axis-aligned (rotate would need image rotate — keep straight but offset gives chaos)
        # To simulate rotation, we could create temp image rotated; simplified keep axis-aligned but with shear offset
        _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.012), fill=(255,255,255, alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
        # top accent bar
        _rounded_rect(draw, (x0, y0, x1, y0+int(S["h"]*0.012)), radius=int(S["h"]*0.012), fill=(*style["accent"][:3], alpha), outline=None)
        draw.rectangle((x0, y0+int(S["h"]*0.006), x1, y0+int(S["h"]*0.012)), fill=(*style["accent"][:3], alpha))
        # title
        fs_t = max(13, int(S["h"]*0.019))
        f_t = _font(config.MGFX_FONT_BOLD, fs_t)
        title = c.get("title","")
        tw,_ = _text_bbox(title, f_t)
        # wrap if needed (AI TOOL line)
        lines = _wrap_lines(title, f_t, card_w - int(S["w"]*0.04))
        lh = f_t.getbbox("Ag")[3]-f_t.getbbox("Ag")[1] + int(S["h"]*0.004)
        base_ty = y0 + int(S["h"]*0.042)
        for li, ln in enumerate(lines[:2]):
            tww,_ = _text_bbox(ln, f_t)
            draw.text((x0 + (card_w - tww)//2, base_ty + li*lh), ln, font=f_t, fill=(*style["fg"][:3], alpha))
        # price big
        price = c.get("price","")
        fs_p = max(16, int(S["h"]*0.024))
        f_p = _font(config.MGFX_FONT_BLACK, fs_p)
        # if price contains $ or HOURS, accent
        is_accent_price = "$" in price or "HOURS" in price or "YOU STILL" in price
        col_p = style["accent"] if is_accent_price else style["fg"]
        # center price area
        py = y0 + int(card_h*0.52)
        # handle multi-line price (YOU STILL HAVE TO)
        if "\n" in price or len(price.split())>3 and "YOU STILL" in price:
            pw_lines = _wrap_lines(price, f_p, card_w - int(S["w"]*0.04))
            for li, pl in enumerate(pw_lines[:2]):
                tww,_ = _text_bbox(pl, f_p)
                draw.text((x0+(card_w-tww)//2, py+li*(f_p.getbbox("Ag")[3]-f_p.getbbox("Ag")[1]+4)), pl, font=_font(config.MGFX_FONT_BOLD, fs_p if li==0 else int(fs_p*0.75)), fill=(*col_p[:3], alpha))
        else:
            tww,_ = _text_bbox(price, f_p)
            draw.text((x0+(card_w-tww)//2, py), price, font=f_p, fill=(*col_p[:3], alpha))
        # foot small muted
        foot = c.get("foot","")
        if foot:
            fs_f = max(11, int(S["h"]*0.014))
            f_f = _font(config.MGFX_FONT_BOLD, fs_f)
            tww,_ = _text_bbox(foot, f_f)
            draw.text((x0+(card_w-tww)//2, y1 - int(S["h"]*0.028)), foot, font=f_f, fill=(*style["muted"][:3], int(180*alpha/255)))
    # freeze overlay: "THERE\'S ANOTHER WAY." at bottom after 0.72
    freeze_p = _clamp01((p-0.50)/0.28)
    if freeze_p>0:
        e = _ease_out_cubic(freeze_p)
        fa = int(255*e)
        # dark overlay vignette top?
        # small pill centered bottom
        txt = spec.get("freeze_text") or "THERE\'S ANOTHER WAY."
        fs_fz = max(14, int(S["h"]*0.018))
        f_fz = _font(config.MGFX_FONT_BOLD, fs_fz)
        tw,_ = _text_bbox(txt, f_fz)
        fy = int(S["h"]*0.82) + int((1-e)*S["h"]*0.015)
        # pill bg
        pad = int(S["w"]*0.018)
        ph = int(S["h"]*0.042)
        px0 = S["w"]//2 - tw//2 - pad
        px1 = S["w"]//2 + tw//2 + pad
        py0, py1 = fy - int(S["h"]*0.012), fy + ph - int(S["h"]*0.012)
        _rounded_rect(draw, (px0,py0,px1,py1), radius=ph//2, fill=(*config.TALO_OLIVE_RGB[:3], fa), outline=None)
        draw.text((S["w"]//2 - tw//2, fy), txt, font=f_fz, fill=(255,255,255, fa))
        # freeze line across
        # horizontal rule with acid dot stops?
        # draw subtle freeze scanline

def _draw_editorial_black(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # Black editorial with flashes: YOU HAVE WORK TO DO. -> 4 flashes -> AND YOU DON\'T WANT TO DO IT.
    # spec: main, flashes list, final
    main = spec.get("main") or spec.get("line1") or "YOU HAVE WORK TO DO."
    flashes = spec.get("flashes") or spec.get("items") or [
        "2,431 leads to research",
        "18,000 records to clean",
        "500 invoices to process",
        "7,200 products to upload",
    ]
    final = spec.get("final") or spec.get("line2") or "AND YOU DON\'T WANT TO DO IT."
    # bg black already (style talo_dark)
    # phase timings: 0-0.28 main, 0.28-0.72 flashes, 0.72-1.0 final
    # main line huge italic serif, staggered
    e_main = _clamp01(p/0.30)
    if e_main>0:
        mp = _ease_out_cubic(e_main)
        # main fades out after 0.32
        fade_out = 1.0
        if p>0.30:
            fade_out = 1 - _clamp01((p-0.30)/0.12)
        ma = int(255*mp*fade_out)
        if ma>0:
            fs_m = max(36, int(S["h"]*0.088))
            if config.PORTRAIT: fs_m = max(28, int(S["h"]*0.055))
            f_m = _font(config.MGFX_FONT_SERIF_ITALIC, fs_m)
            # word stagger for main (but main is short, draw as whole with y lift)
            y = S["h"]//2 - int(S["h"]*0.08) - int((1-mp)*S["h"]*0.02)
            _draw_word_stagger(draw, main, f_m, S["w"]//2, y, e_main, S, (255,255,255), None, None, stagger=0.06, word_duration=0.30)
            # large ghost talo watermark faint behind main (like editorial)
            if p<0.30:
                ga = int(12*mp)
                f_ghost = _font(config.FONT_REGULAR, max(80, int(S["h"]*0.22)))
                tw,_ = _text_bbox("talo", f_ghost)
                draw.text((S["w"]//2 - tw//2, S["h"]//2 + int(S["h"]*0.18)), "talo", font=f_ghost, fill=(255,255,255, ga))
    # flashes: rapid strobe each 0.10s
    if 0.28 < p < 0.74:
        # flash index
        flash_phase = (p - 0.28) / 0.44  # 0..1 across 4 flashes
        idx = int(flash_phase * len(flashes))
        idx = min(max(idx,0), len(flashes)-1)
        fp = (flash_phase * len(flashes)) - idx  # 0..1 within flash
        # strobe alpha: quick in, hold, cut
        fa = int(255 * _clamp01(fp/0.22) * (1 - _clamp01((fp-0.75)/0.25)))
        if fa>0:
            txt = flashes[idx]
            fs_f = max(20, int(S["h"]*0.034))
            f_f = _font(config.MGFX_FONT_REGULAR, fs_f)
            # mono style for numbers? keep regular but numbers accent? draw numbers in acid
            # split number and rest
            parts = txt.split(" ",1)
            num = parts[0] if len(parts)>0 else txt
            rest = parts[1] if len(parts)>1 else ""
            num_w,_ = _text_bbox(num+" ", f_f)
            rest_w,_ = _text_bbox(rest, f_f)
            total_w = num_w + rest_w
            y = S["h"]//2 + int(S["h"]*0.06) + int((1-_clamp01(fp/0.22))*S["h"]*0.01)
            sx = S["w"]//2 - total_w//2
            # shake
            shake = int(math.sin(p*60)* S["w"]*0.002 * fa/255)
            draw.text((sx+shake, y), num, font=_font(config.MGFX_FONT_BOLD, fs_f), fill=(*config.TALO_ACID_RGB[:3], fa))
            draw.text((sx+num_w+shake, y), " "+rest, font=f_f, fill=(255,255,255, fa))
            # cursor blink at end of line
            if fp>0.35 and fp<0.85:
                cur_on = (int(p*12) % 2)==0
                if cur_on:
                    cx = sx+total_w+int(S["w"]*0.006)
                    draw.line((cx, y, cx, y+int(S["h"]*0.028)), fill=(255,255,255, fa), width=2)
    # final line after flashes
    if p>0.68:
        fp = _clamp01((p-0.68)/0.32)
        if fp>0:
            e = _ease_out_expo(fp)
            fa = int(255*e)
            fs_fn = max(28, int(S["h"]*0.052))
            if config.PORTRAIT: fs_fn = max(22, int(S["h"]*0.036))
            f_fn = _font(config.MGFX_FONT_SERIF_ITALIC, fs_fn)
            # draw final with acid underline wipe
            y = S["h"]//2 + int(S["h"]*0.14) + int((1-e)*S["h"]*0.018)
            tw,_ = _text_bbox(final, f_fn)
            draw.text((S["w"]//2 - tw//2, y), final, font=f_fn, fill=(255,255,255, fa))
            # notification pings dots flying (small acid dots)
            if fp>0.4:
                for i in range(3):
                    dp = _clamp01((fp - 0.4 - i*0.08)/0.25)
                    if dp<=0: continue
                    de = _ease_out_cubic(dp)
                    da = int(180*de*(1-de*0.5))
                    dx = S["w"]//2 + tw//2 + int(S["w"]*0.04) + i*int(S["w"]*0.018) + int((1-de)*S["w"]*0.02)
                    dy = y + int(S["h"]*0.012) - int(de*S["h"]*0.015)
                    r = int(S["h"]*0.005)
                    draw.ellipse((dx-r, dy-r, dx+r, dy+r), fill=(*config.TALO_ACID_RGB[:3], da))
    # cursor blinking constant at bottom? done

def _draw_editorial_statement(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # YOU DON'T NEED ANOTHER AI TOOL. / YOU NEED THE WORK DONE. over paper grain, vintage film border
    # spec: line1, line2
    line1 = spec.get("line1") or "YOU DON\'T NEED ANOTHER AI TOOL."
    line2 = spec.get("line2") or "YOU NEED THE WORK DONE."
    is_portrait = config.PORTRAIT
    fs1 = max(32, int(S["h"]*0.062)) if not is_portrait else max(26, int(S["h"]*0.042))
    fs2 = max(40, int(S["h"]*0.072)) if not is_portrait else max(32, int(S["h"]*0.052))
    f1 = _font(config.MGFX_FONT_REGULAR, fs1)
    f1i = _font(config.MGFX_FONT_SERIF_ITALIC, fs1)
    f2 = _font(config.MGFX_FONT_BLACK, fs2)
    # paper watermark
    # (frame watermark already added at render level)
    # subtle vignette via border
    # line1 appears first, then crossfades to line2 with pause
    p1 = _clamp01(p/0.42)
    p2 = _clamp01((p-0.40)/0.42)
    cx, cy = S["w"]//2, S["h"]//2
    if p1>0:
        e1 = _ease_out_cubic(p1)
        # fade out after 0.38
        fade1 = 1.0
        if p>0.38:
            fade1 = 1 - _clamp01((p-0.38)/0.20)
        a1 = int(255*e1*fade1)
        if a1>0:
            y1 = cy - int(S["h"]*0.04) + int((1-e1)*S["h"]*0.018)
            # line1 italic, muted dark, with word stagger
            _draw_word_stagger(draw, line1, f1i, cx, y1, p1, S, style["muted"] if p<0.45 else (60,60,66), None, None, stagger=0.05, word_duration=0.32)
            # tiny rule below
            if p1>0.55:
                rp = _clamp01((p1-0.55)/0.30)
                tw,_ = _text_bbox(line1, f1i)
                rx0 = cx - tw//2
                ry = y1 + int(S["h"]*0.09)
                draw.line((rx0, ry, rx0+int(tw*rp), ry), fill=(*config.TALO_ACCENT_RGB[:3], int(120*a1/255)), width=max(2,S["stroke"]//3))
    if p2>0:
        e2 = _ease_out_expo(p2)
        a2 = int(255*e2)
        y2 = cy + int(S["h"]*0.06) + int((1-e2)*S["h"]*0.018)
        if a2>0:
            # line2 bold, with periwinkle or acid accent on "WORK DONE."
            accent = spec.get("accent") or "WORK DONE."
            words = line2.split()
            acc_words = set(accent.strip(".").split())
            fs_use = f2
            space_w,_ = _text_bbox(" ", fs_use)
            word_ws = [_text_bbox(w, fs_use)[0] for w in words]
            total_w = sum(word_ws) + space_w*(len(words)-1)
            cur_x = cx - total_w//2
            for wi, w in enumerate(words):
                wp = _clamp01((p2 - wi*0.055)/0.36)
                if wp<=0:
                    cur_x += word_ws[wi] + space_w
                    continue
                ec = _ease_out_cubic(wp)
                wa = int(255*ec)
                y_off = int((1-ec)*S["h"]*0.014)
                is_acc = w.strip(".,") in acc_words
                col = config.TALO_ACCENT_RGB if is_acc else style["fg"]
                # for dark olive bg, accent should be acid
                if style["bg"][:3]==config.TALO_OLIVE_RGB:
                    col = config.TALO_ACID_RGB if is_acc else (248,248,242)
                draw.text((cur_x, y2+y_off), w, font=fs_use, fill=(*col[:3], wa) if len(col)==3 else col+(wa,))
                cur_x += word_ws[wi] + space_w
            # grain dots
            # add faint grain via 80 dots after landing
            if p2>0.6:
                import random
                random.seed(42)
                for _ in range(60):
                    gx = random.randint(0, S["w"])
                    gy = random.randint(0, S["h"])
                    ga = int(random.randint(0, 10) * _clamp01((p2-0.6)/0.3))
                    if ga>0:
                        draw.ellipse((gx,gy,gx+1,gy+1), fill=(0,0,0, ga))


# ── Clep layouts — lime + cream editorial (30s) ───────────────────────────

def _draw_clep_chaos(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:00-0:03 fast montage: paper desk, blurry PDF, red error cells, cursor copy-paste
    # style should be clep (cream bg, ink fg, lime accent)
    # Use 3 floating doc cards with shake/glitch
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2 - int(S["h"]*0.02)
    # background tint: subtle lime radial at top
    if p>0.02:
        lime_a = int(18 * _clamp01(p/0.4))
        # draw soft lime wash at top edge
        wash_h = int(S["h"]*0.28)
        overlay = Image.new("RGBA", (S["w"], wash_h), (*config.CLEP_LIME_RGB[:3], lime_a))
        # composite via draw? use rectangle with alpha
        draw.rectangle((0, 0, S["w"], wash_h), fill=(*config.CLEP_LIME_RGB[:3], lime_a))
    # three cards
    cards = [
        {"title":"Paper pile", "detail":"Scanned PDF", "accent": False},
        {"title":"Blurry scan", "detail":"2,431 rows", "accent": False},
        {"title":"Red errors", "detail":"Copy-paste", "accent": True},
    ]
    card_w = int(S["w"] * (0.26 if not is_portrait else 0.42))
    card_h = int(S["h"] * (0.32 if not is_portrait else 0.24))
    gap = int(S["w"]*0.03)
    total_w = len(cards)*card_w + (len(cards)-1)*gap
    start_x = cx - total_w//2
    base_y = cy - card_h//2
    for idx, c in enumerate(cards):
        # staggered glitch entry
        delay = idx*0.12
        cp = _clamp01((p - delay)/0.38)
        if cp<=0: continue
        e = _ease_out_cubic(cp)
        # shake
        shake_x = int(math.sin(p*38 + idx*1.7) * S["w"]*0.004 * (1-_clamp01(cp/0.6)))
        shake_y = int(math.cos(p*42 + idx*2.1) * S["h"]*0.006 * (1-_clamp01(cp/0.6)))
        x0 = start_x + idx*(card_w+gap) + shake_x
        y0 = base_y + shake_y
        x1, y1 = x0+card_w, y0+card_h
        alpha = int(255*e)
        # card shadow
        _rounded_rect(draw, (x0+int(S["h"]*0.008), y0+int(S["h"]*0.008), x1+int(S["h"]*0.008), y1+int(S["h"]*0.008)), radius=int(S["h"]*0.012), fill=(0,0,0,int(10*alpha/255)), outline=None)
        _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.012), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
        # top bar like browser dot
        bar_h = int(card_h*0.18)
        _rounded_rect(draw, (x0, y0, x1, y0+bar_h), radius=int(S["h"]*0.012), fill=(250,250,248,alpha), outline=None)
        draw.rectangle((x0, y0+bar_h//2, x1, y0+bar_h), fill=(250,250,248,alpha))
        # icon region: document lines
        inner_y = y0+bar_h + int(S["h"]*0.015)
        # lines
        for li in range(5):
            lw = int(card_w * (0.70 - li*0.08)) if li<4 else int(card_w*0.45)
            lx0 = x0 + int(S["w"]*0.015)
            ly = inner_y + li*int(S["h"]*0.022)
            # for red error card, 3rd line is red
            if idx==2 and li==2:
                _rounded_rect(draw, (lx0, ly, lx0+lw, ly+int(S["h"]*0.014)), radius=int(S["h"]*0.004), fill=(*config.CLEP_RED_RGB[:3], int(180*alpha/255)), outline=None)
                # error dot
                er = int(S["h"]*0.008)
                draw.ellipse((lx0+lw+int(S["w"]*0.008)-er, ly-er, lx0+lw+int(S["w"]*0.008)+er, ly+er), fill=(*config.CLEP_RED_RGB[:3], alpha))
            elif idx==1 and li==1:
                # blur effect: lower alpha
                _rounded_rect(draw, (lx0, ly, lx0+lw, ly+int(S["h"]*0.014)), radius=int(S["h"]*0.004), fill=(180,180,175,int(70*alpha/255)), outline=None)
            else:
                _rounded_rect(draw, (lx0, ly, lx0+lw, ly+int(S["h"]*0.014)), radius=int(S["h"]*0.004), fill=(230,230,228,int(180*alpha/255)), outline=None)
        # cursor for last card
        if idx==2 and p>0.45:
            cp2 = _clamp01((p-0.45)/0.35)
            if cp2>0:
                ce = _ease_out_cubic(cp2)
                cur_x = x0 + int(card_w*0.55) + int((1-ce)*S["w"]*0.02)
                cur_y = inner_y + int(S["h"]*0.045)
                # cursor arrow
                draw.polygon([(cur_x, cur_y), (cur_x, cur_y+int(S["h"]*0.022)), (cur_x+int(S["w"]*0.012), cur_y+int(S["h"]*0.015))], fill=(30,30,28,int(220*ce*alpha/255)), outline=None)
        # title small at bottom
        fs_t = max(11, int(S["h"]*0.015))
        f_t = _font(config.MGFX_FONT_BOLD, fs_t)
        tw,_ = _text_bbox(c["title"], f_t)
        draw.text((x0+(card_w-tw)//2, y1 - int(S["h"]*0.028)), c["title"], font=f_t, fill=(*style["muted"][:3], int(180*alpha/255)))

def _draw_clep_false(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:03-0:07 chat with hallucinated number + template loader desaturated
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2
    card_w = int(S["w"] * (0.62 if not is_portrait else 0.86))
    card_h = int(S["h"] * (0.52 if not is_portrait else 0.58))
    x0, y0 = cx - card_w//2, cy - card_h//2 + int(S["h"]*0.02)
    x1, y1 = cx + card_w//2, cy + card_h//2 + int(S["h"]*0.02)
    e = _ease_out_cubic(_clamp01(p/0.32))
    if e<=0: return
    alpha = int(255*e)
    # desaturate feel: overlay gray wash
    draw.rectangle((0,0,S["w"],S["h"]), fill=(240,240,238,int(18*alpha/255)))
    # card
    _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.016), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(160*alpha/255)), width=max(1,S["stroke"]//2))
    # top chat header
    bar_h = int(card_h*0.13)
    _rounded_rect(draw, (x0, y0, x1, y0+bar_h), radius=int(S["h"]*0.016), fill=(245,245,240,alpha), outline=None)
    draw.rectangle((x0, y0+bar_h//2, x1, y0+bar_h), fill=(245,245,240,alpha))
    # chat bubble wrong number
    bubble_pad = int(S["w"]*0.018)
    b_x0 = x0 + bubble_pad
    b_y0 = y0 + bar_h + int(S["h"]*0.025)
    b_x1 = x1 - bubble_pad
    b_y1 = b_y0 + int(S["h"]*0.16)
    _rounded_rect(draw, (b_x0,b_y0,b_x1,b_y1), radius=int(S["h"]*0.012), fill=(249,249,245,int(220*alpha/255)), outline=(*style["card_border"][:3], int(120*alpha/255)), width=1)
    # icon
    fs_s = max(11, int(S["h"]*0.015))
    f_s = _font(config.MGFX_FONT_REGULAR, fs_s)
    doc_txt = "invoice_2024.pdf"
    draw.text((b_x0+int(S["w"]*0.015), b_y0+int(S["h"]*0.015)), doc_txt, font=f_s, fill=(*style["muted"][:3], int(160*alpha/255)))
    # wrong number
    num_txt = "$ 23,450.00  →  $ 12,340.00"
    fs_n = max(14, int(S["h"]*0.021))
    f_n = _font(config.MGFX_FONT_BOLD, fs_n)
    # draw number with red highlight on second half
    tw1,_ = _text_bbox("$ 23,450.00  →  ", f_n)
    tw2,_ = _text_bbox("$ 12,340.00", f_n)
    nx = b_x0 + int(S["w"]*0.015)
    ny = b_y0 + int(S["h"]*0.075)
    draw.text((nx, ny), "$ 23,450.00  →  ", font=f_n, fill=(*style["fg"][:3], int(120*alpha/255)))
    # red pill behind wrong
    rx0 = nx + tw1 - int(S["w"]*0.004)
    ry0 = ny - int(S["h"]*0.008)
    rx1 = rx0 + tw2 + int(S["w"]*0.012)
    ry1 = ry0 + int(S["h"]*0.036)
    _rounded_rect(draw, (rx0,ry0,rx1,ry1), radius=int(S["h"]*0.008), fill=(*config.CLEP_RED_RGB[:3], int(255*alpha/255)), outline=None)
    draw.text((rx0+int(S["w"]*0.006), ny), "$ 12,340.00", font=f_n, fill=(255,255,255,alpha))
    # ✗ wrong tag
    tag = "✗ wrong"
    fs_t = max(11, int(S["h"]*0.014))
    f_t = _font(config.MGFX_FONT_BOLD, fs_t)
    ttw,_ = _text_bbox(tag, f_t)
    tx0 = rx1 + int(S["w"]*0.010)
    ty0 = ny + int(S["h"]*0.004)
    tx1 = tx0 + ttw + int(S["w"]*0.012)
    ty1 = ty0 + int(S["h"]*0.028)
    _rounded_rect(draw, (tx0,ty0,tx1,ty1), radius=ty1-ty0//2, fill=(30,30,28,alpha), outline=None)
    draw.text((tx0+int(S["w"]*0.006), ty0+int(S["h"]*0.002)), tag, font=f_t, fill=(255,255,255,alpha))
    # lower loader card
    l_y0 = b_y1 + int(S["h"]*0.025)
    l_y1 = y1 - int(S["h"]*0.025)
    l_x0 = x0 + bubble_pad
    l_x1 = x1 - bubble_pad
    _rounded_rect(draw, (l_x0,l_y0,l_x1,l_y1), radius=int(S["h"]*0.012), fill=(238,238,233,int(180*alpha/255)), outline=None)
    # spinner
    cx_sp = l_x0 + int(S["w"]*0.025)
    cy_sp = (l_y0+l_y1)//2
    sp_r = int(S["h"]*0.018)
    # draw arc spinner (approx)
    for i in range(12):
        ang = math.radians(i*30 + p*360*2)
        rad = sp_r
        x = cx_sp + int(rad*0.7*math.cos(ang))
        y = cy_sp + int(rad*0.7*math.sin(ang))
        a = int(255 * (0.2 + 0.8*_clamp01((i/12))))
        if i==0:
            a = int(255*alpha/255)
        draw.ellipse((x-2,y-2,x+2,y+2), fill=(80,80,75,int(a*alpha/255)))
    txt = "configuring parser..."
    fs_l = max(13, int(S["h"]*0.017))
    f_l = _font(config.MGFX_FONT_REGULAR, fs_l)
    draw.text((cx_sp+int(S["w"]*0.035), cy_sp - f_l.getbbox(txt)[3]//2), txt, font=f_l, fill=(*style["muted"][:3], int(180*alpha/255)))
    # muted overlay to feel desaturated
    if p>0.1:
        draw.rectangle((0,0,S["w"],S["h"]), fill=(120,120,115,int(12*alpha/255)))

def _draw_clep_enter(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:07-0:11 snap to lime/cream, Clep logo pop
    # lime wash bg
    lime_a = int(255 * _clamp01(p/0.22))
    draw.rectangle((0,0,S["w"],S["h"]), fill=(*config.CLEP_LIME_RGB[:3], int(255* _clamp01(p/0.18))))
    # cream card centered
    is_portrait = config.PORTRAIT
    card_w = int(S["w"] * (0.58 if not is_portrait else 0.82))
    card_h = int(S["h"] * (0.42 if not is_portrait else 0.38))
    cx, cy = S["w"]//2, S["h"]//2
    x0, y0 = cx - card_w//2, cy - card_h//2
    x1, y1 = cx + card_w//2, cy + card_h//2
    e = _ease_out_back(_clamp01(p/0.42))
    alpha = int(255*e)
    # card with soft shadow
    _rounded_rect(draw, (x0+int(S["h"]*0.010), y0+int(S["h"]*0.010), x1+int(S["h"]*0.010), y1+int(S["h"]*0.010)), radius=int(S["h"]*0.018), fill=(0,0,0,int(12*alpha/255)), outline=None)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.018), fill=(255,255,255,alpha), outline=(*config.CLEP_CREAM_DARK_RGB[:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
    # logo "clep" pop
    # use text rendering for clep (lowercase)
    logo = "clep"
    fs_logo = max(56, int(S["h"]*0.10))
    f_logo = _font(config.MGFX_FONT_BLACK, fs_logo)
    tw,_ = _text_bbox(logo, f_logo)
    # scale pop
    sc = _lerp(0.85, 1.0, e)
    # we simulate scale by adjusting position slightly
    lx = cx - int(tw*sc)//2
    ly = cy - int(S["h"]*0.06) + int((1-e)*S["h"]*0.02)
    # draw with scale via offset? just draw normal at e
    l_alpha = int(255*e)
    draw.text((lx, ly), logo, font=f_logo, fill=(*style["fg"][:3], l_alpha))
    # subtle lime dot under logo
    dot_y = ly + int(S["h"]*0.13)
    _rounded_rect(draw, (cx-int(S["w"]*0.03), dot_y, cx+int(S["w"]*0.03), dot_y+int(S["h"]*0.006)), radius=int(S["h"]*0.003), fill=(*config.CLEP_LIME_RGB[:3], int(180*l_alpha/255)), outline=None)
    # tagline just... works.
    tag = spec.get("tagline") or "just... works."
    if p>0.28:
        tp = _clamp01((p-0.28)/0.32)
        te = _ease_out_cubic(tp)
        ta = int(255*te)
        fs_t = max(20, int(S["h"]*0.032))
        f_t = _font(config.MGFX_FONT_SERIF_ITALIC, fs_t)
        tw2,_ = _text_bbox(tag, f_t)
        draw.text((cx - tw2//2, cy + int(S["h"]*0.08) + int((1-te)*S["h"]*0.015)), tag, font=f_t, fill=(*style["fg"][:3], ta))

def _draw_clep_dropzone(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:11-0:16 hero: dropzone left, spreadsheet right, scanning
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2 + int(S["h"]*0.04)
    # overall browser-like container
    card_w = int(S["w"] * (0.78 if not is_portrait else 0.92))
    card_h = int(S["h"] * (0.58 if not is_portrait else 0.62))
    x0, y0 = cx - card_w//2, cy - card_h//2
    x1, y1 = cx + card_w//2, cy + card_h//2
    e = _ease_out_cubic(_clamp01(p/0.30))
    if e<=0: return
    alpha = int(255*e)
    # card
    _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.016), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(160*alpha/255)), width=max(1,S["stroke"]//2))
    # top bar like app.clep.io
    bar_h = int(S["h"]*0.055)
    _rounded_rect(draw, (x0,y0,x1,y0+bar_h), radius=int(S["h"]*0.016), fill=(250,250,245,alpha), outline=None)
    draw.rectangle((x0, y0+bar_h//2, x1, y0+bar_h), fill=(250,250,245,alpha))
    # dots
    dot_r = int(S["h"]*0.007)
    gap = int(S["w"]*0.008)
    sx = x0 + int(S["w"]*0.016)
    sy = y0 + bar_h//2
    for i,col in enumerate([(231,76,60),(241,196,15),(46,204,113)]):
        cxd = sx + i*(dot_r*2+gap)
        draw.ellipse((cxd-dot_r, sy-dot_r, cxd+dot_r, sy+dot_r), fill=(*col, alpha))
    # app title
    fs_a = max(11, int(S["h"]*0.014))
    f_a = _font(config.MGFX_FONT_REGULAR, fs_a)
    t = "app.clep.io — live conversion"
    tw,_ = _text_bbox(t, f_a)
    draw.text((x0+int(S["w"]*0.08), sy - f_a.getbbox(t)[3]//2), t, font=f_a, fill=(*style["muted"][:3], int(160*alpha/255)))
    # live demo pill
    pill_w = int(S["w"]*0.11)
    pill_h = int(bar_h*0.62)
    px1 = x1 - int(S["w"]*0.016)
    px0 = px1 - pill_w
    py0 = y0 + (bar_h - pill_h)//2
    py1 = py0 + pill_h
    _rounded_rect(draw, (px0,py0,px1,py1), radius=pill_h//2, fill=(*config.CLEP_LIME_RGB[:3], int(40*alpha/255)), outline=(*config.CLEP_LIME_RGB[:3], int(120*alpha/255)), width=1)
    fs_p = max(10, int(S["h"]*0.013))
    f_p = _font(config.MGFX_FONT_BOLD, fs_p)
    pt = "● LIVE DEMO"
    ptw,_ = _text_bbox(pt, f_p)
    draw.text((px0+(pill_w-ptw)//2, py0+(pill_h - f_p.getbbox(pt)[3])//2 -1), pt, font=f_p, fill=(*config.CLEP_GREEN_RGB[:3], alpha))
    # split into left dropzone and right sheet
    div_x = x0 + int(card_w*0.48)
    # vertical divider
    draw.line((div_x, y0+bar_h, div_x, y1), fill=(*style["card_border"][:3], int(120*alpha/255)), width=1)
    # LEFT: dropzone
    dz_pad = int(S["w"]*0.018)
    dz_x0 = x0 + dz_pad
    dz_x1 = div_x - dz_pad
    dz_y0 = y0 + bar_h + dz_pad
    dz_y1 = y1 - dz_pad
    # dashed border
    dash_a = int(180*alpha/255)
    # draw dashed rect via segments
    seg = int(S["w"]*0.015)
    # top/bottom dashed
    y = dz_y0
    # use rounded rect with dashed effect approximated by dotted line
    _rounded_rect(draw, (dz_x0,dz_y0,dz_x1,dz_y1), radius=int(S["h"]*0.012), fill=(250,255,240,int(120*alpha/255)), outline=(*config.CLEP_LIME_RGB[:3], dash_a), width=max(2,S["stroke"]//2))
    # we will fake dash by overlaying small gaps? Keep solid for simplicity but lighter
    # upload icon
    icon_cx = (dz_x0+dz_x1)//2
    icon_cy = dz_y0 + int((dz_y1-dz_y0)*0.32)
    icon_r = int(S["h"]*0.028)
    # lime circle bg
    draw.ellipse((icon_cx-icon_r, icon_cy-icon_r, icon_cx+icon_r, icon_cy+icon_r), fill=(*config.CLEP_LIME_RGB[:3], int(200*alpha/255)), outline=None)
    # arrow up icon
    aw = int(icon_r*0.6)
    draw.line((icon_cx, icon_cy-aw//2, icon_cx, icon_cy+aw//3), fill=(30,30,28,alpha), width=max(2,S["stroke"]//2))
    draw.line((icon_cx-aw//3, icon_cy-aw//6, icon_cx, icon_cy-aw//2), fill=(30,30,28,alpha), width=max(2,S["stroke"]//2))
    draw.line((icon_cx+aw//3, icon_cy-aw//6, icon_cx, icon_cy-aw//2), fill=(30,30,28,alpha), width=max(2,S["stroke"]//2))
    # text
    fs_dz = max(13, int(S["h"]*0.018))
    f_dz = _font(config.MGFX_FONT_BOLD, fs_dz)
    txt1 = "Drag a bank statement, invoice, or receipt here"
    tw1,_ = _text_bbox(txt1, f_dz)
    # wrap if needed
    if tw1 > (dz_x1-dz_x0 - int(S["w"]*0.04)):
        # split
        parts = txt1.split(",")
        line1 = parts[0]+","
        line2 = parts[1].strip() if len(parts)>1 else ""
        tw1a,_ = _text_bbox(line1, f_dz)
        tw1b,_ = _text_bbox(line2, f_dz)
        draw.text(((dz_x0+dz_x1)//2 - tw1a//2, icon_cy+int(S["h"]*0.045)), line1, font=f_dz, fill=(*style["fg"][:3], alpha))
        draw.text(((dz_x0+dz_x1)//2 - tw1b//2, icon_cy+int(S["h"]*0.068)), line2, font=f_dz, fill=(*style["fg"][:3], alpha))
    else:
        draw.text(((dz_x0+dz_x1)//2 - tw1//2, icon_cy+int(S["h"]*0.045)), txt1, font=f_dz, fill=(*style["fg"][:3], alpha))
    # small text PDF etc
    fs_sm = max(11, int(S["h"]*0.014))
    f_sm = _font(config.MGFX_FONT_REGULAR, fs_sm)
    sm = "PDF, scanned image, or photo"
    tws,_ = _text_bbox(sm, f_sm)
    draw.text(((dz_x0+dz_x1)//2 - tws//2, icon_cy+int(S["h"]*0.095)), sm, font=f_sm, fill=(*style["muted"][:3], int(160*alpha/255)))
    # PDF thumbnail with scanning
    # simulate PDF area below text
    pdf_y0 = dz_y0 + int((dz_y1-dz_y0)*0.62)
    pdf_y1 = dz_y1 - int(S["h"]*0.015)
    pdf_x0 = dz_x0 + int(S["w"]*0.03)
    pdf_x1 = dz_x1 - int(S["w"]*0.03)
    _rounded_rect(draw, (pdf_x0,pdf_y0,pdf_x1,pdf_y1), radius=int(S["h"]*0.008), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(120*alpha/255)), width=1)
    # pdf lines
    for li in range(4):
        ly = pdf_y0 + int(S["h"]*0.018) + li*int(S["h"]*0.022)
        lw = int((pdf_x1-pdf_x0)* (0.75 - li*0.08))
        _rounded_rect(draw, (pdf_x0+int(S["w"]*0.012), ly, pdf_x0+int(S["w"]*0.012)+lw, ly+int(S["h"]*0.012)), radius=int(S["h"]*0.004), fill=(220,220,215,int(160*alpha/255)), outline=None)
    # scanning line
    if p>0.30:
        sp = _clamp01((p-0.30)/0.45)
        sy = int(pdf_y0 + (pdf_y1-pdf_y0)*sp)
        # lime scanning line with glow
        draw.line((pdf_x0+2, sy, pdf_x1-2, sy), fill=(*config.CLEP_LIME_RGB[:3], int(220*alpha/255)), width=max(2,S["stroke"]))
        # glow
        draw.line((pdf_x0, sy-2, pdf_x1, sy+2), fill=(*config.CLEP_LIME_RGB[:3], int(30*alpha/255)), width=1)
        # boxes around rows sweeping
        # boxes appear as scanning passes
        for li in range(4):
            box_y = pdf_y0 + int(S["h"]*0.018) + li*int(S["h"]*0.022) - int(S["h"]*0.006)
            if sy > box_y:
                bx0 = pdf_x0+int(S["w"]*0.010)
                bx1 = pdf_x1-int(S["w"]*0.010)
                by0 = box_y
                by1 = box_y+int(S["h"]*0.018)
                # draw lime box
                b_alpha = int(120*alpha/255) if sy < by1+10 else int(80*alpha/255)
                # use outline
                _rounded_rect(draw, (bx0,by0,bx1,by1), radius=3, fill=(255,255,255,0), outline=(*config.CLEP_LIME_RGB[:3], b_alpha), width=2)
    # RIGHT: clean spreadsheet
    sh_x0 = div_x + dz_pad
    sh_x1 = x1 - dz_pad
    sh_y0 = y0 + bar_h + dz_pad
    sh_y1 = y1 - dz_pad
    # header
    hdr_h = int(S["h"]*0.08)
    _rounded_rect(draw, (sh_x0,sh_y0,sh_x1,sh_y0+hdr_h), radius=int(S["h"]*0.008), fill=(250,255,240,int(200*alpha/255)), outline=None)
    fs_hd = max(11, int(S["h"]*0.015))
    f_hd = _font(config.MGFX_FONT_BOLD, fs_hd)
    hd = "Clean spreadsheet"
    draw.text((sh_x0+int(S["w"]*0.012), sh_y0+int(S["h"]*0.015)), hd, font=f_hd, fill=(*style["fg"][:3], alpha))
    # waiting text
    wait = "waiting for file..."
    f_wait = _font(config.MGFX_FONT_REGULAR, max(10, int(S["h"]*0.012)))
    tww,_ = _text_bbox(wait, f_wait)
    draw.text((sh_x1 - tww - int(S["w"]*0.012), sh_y0+int(S["h"]*0.018)), wait, font=f_wait, fill=(*style["muted"][:3], int(140*alpha/255)))
    # rows that populate after scanning
    rows = 5
    row_h = (sh_y1 - (sh_y0+hdr_h) - int(S["h"]*0.02)) // rows
    start_ry = sh_y0+hdr_h + int(S["h"]*0.015)
    for ri in range(rows):
        rp = _clamp01((p - 0.55 - ri*0.07)/0.30)
        if rp<=0: continue
        re = _ease_out_cubic(rp)
        ra = int(255*re*alpha/255)
        ry0 = start_ry + ri*row_h
        ry1 = ry0 + row_h - int(S["h"]*0.006)
        # row bg
        _rounded_rect(draw, (sh_x0+int(S["w"]*0.006), ry0, sh_x1-int(S["w"]*0.006), ry1), radius=4, fill=(255,255,255,ra), outline=(*style["card_border"][:3], int(80*ra/255)), width=1)
        # columns lines
        for ci in range(3):
            cx0 = sh_x0 + int(S["w"]*0.015) + ci*int((sh_x1-sh_x0)*0.30)
            lw = int((sh_x1-sh_x0)*0.22) if ci<2 else int((sh_x1-sh_x0)*0.18)
            ly = (ry0+ry1)//2 - int(S["h"]*0.006)
            _rounded_rect(draw, (cx0, ly, cx0+lw, ly+int(S["h"]*0.012)), radius=3, fill=(230,230,225,int(180*ra/255)), outline=None)
        # check if last rows need slim
        if re>0.9 and ri==rows-1:
            # subtle lime flash
            pass
    # "Instantly." pill at bottom after rows complete
    if p>0.82:
        ip = _clamp01((p-0.82)/0.18)
        ie = _ease_out_back(ip)
        ia = int(255*ie)
        pill_h = int(S["h"]*0.042)
        pill_w = int(S["w"]*0.14)
        px0 = (sh_x0+sh_x1)//2 - pill_w//2
        py0 = sh_y1 - int(S["h"]*0.045)
        py1 = py0 + pill_h
        # slide up
        py0 -= int((1-ie)*S["h"]*0.02)
        py1 -= int((1-ie)*S["h"]*0.02)
        _rounded_rect(draw, (px0,py0,px1:=px0+pill_w,py1), radius=pill_h//2, fill=(*config.CLEP_LIME_RGB[:3], ia), outline=None)
        fs_i = max(12, int(S["h"]*0.016))
        f_i = _font(config.MGFX_FONT_BOLD, fs_i)
        it = "Instantly."
        tww,_ = _text_bbox(it, f_i)
        draw.text(((px0+px1)//2 - tww//2, (py0+py1)//2 - f_i.getbbox(it)[3]//2), it, font=f_i, fill=(17,17,17,ia))

def _draw_clep_icons(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:16-0:20 three icon callouts: No templates, No hidden fees, No re-checking
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2
    card_w = int(S["w"] * (0.22 if not is_portrait else 0.42))
    card_h = int(S["h"] * (0.36 if not is_portrait else 0.28))
    gap = int(S["w"]*0.04)
    total_w = 3*card_w + 2*gap
    start_x = cx - total_w//2
    base_y = cy - card_h//2
    items = [
        {"icon":"template", "label":"No templates", "sub":"No setup"},
        {"icon":"price", "label":"No hidden fees", "sub":"No surprise credits"},
        {"icon":"check", "label":"No re-checking", "sub":"Row-by-row OK"},
    ]
    for idx, it in enumerate(items):
        delay = idx*0.10
        cp = _clamp01((p - delay)/0.32)
        if cp<=0: continue
        e = _ease_out_back(cp) if cp<0.85 else _ease_out_cubic(cp)
        alpha = int(255*e)
        x0 = start_x + idx*(card_w+gap)
        y0 = base_y + int((1-e)*S["h"]*0.04)
        x1, y1 = x0+card_w, y0+card_h
        # card
        _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.014), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
        # lime top accent
        _rounded_rect(draw, (x0, y0, x1, y0+int(S["h"]*0.008)), radius=int(S["h"]*0.014), fill=(*config.CLEP_LIME_RGB[:3], alpha), outline=None)
        draw.rectangle((x0, y0+int(S["h"]*0.004), x1, y0+int(S["h"]*0.008)), fill=(*config.CLEP_LIME_RGB[:3], alpha))
        # icon circle
        icon_cx = (x0+x1)//2
        icon_cy = y0 + int(card_h*0.32)
        icon_r = int(S["h"]*0.042)
        draw.ellipse((icon_cx-icon_r, icon_cy-icon_r, icon_cx+icon_r, icon_cy+icon_r), fill=(*config.CLEP_LIME_RGB[:3], int(220*alpha/255)), outline=None)
        # icon glyphs
        if it["icon"]=="template":
            # doc with strike
            doc_w = int(icon_r*1.0)
            doc_h = int(icon_r*1.2)
            dx0 = icon_cx - doc_w//2
            dy0 = icon_cy - doc_h//2 + int(S["h"]*0.004)
            dx1 = dx0 + doc_w
            dy1 = dy0 + doc_h
            _rounded_rect(draw, (dx0,dy0,dx1,dy1), radius=4, fill=(255,255,255,alpha), outline=(*style["fg"][:3], alpha), width=2)
            # lines inside doc
            for li in range(3):
                ly = dy0 + int(S["h"]*0.012) + li*int(S["h"]*0.014)
                _rounded_rect(draw, (dx0+int(S["w"]*0.008), ly, dx0+doc_w-int(S["w"]*0.008), ly+int(S["h"]*0.004)), radius=2, fill=(30,30,28,int(180*alpha/255)), outline=None)
            # strike circle
            sr = int(icon_r*0.42)
            sx = icon_cx + int(icon_r*0.55)
            sy = icon_cy + int(icon_r*0.45)
            draw.ellipse((sx-sr, sy-sr, sx+sr, sy+sr), fill=(30,30,28,alpha), outline=None)
            draw.line((sx-sr//2, sy-sr//2, sx+sr//2, sy+sr//2), fill=(255,255,255,alpha), width=3)
            draw.line((sx-sr//2, sy+sr//2, sx+sr//2, sy-sr//2), fill=(255,255,255,alpha), width=3)
        elif it["icon"]=="price":
            # price tag
            tag_w = int(icon_r*1.4)
            tag_h = int(icon_r*0.9)
            tx0 = icon_cx - tag_w//2
            ty0 = icon_cy - tag_h//2
            tx1, ty1 = tx0+tag_w, ty0+tag_h
            _rounded_rect(draw, (tx0,ty0,tx1,ty1), radius=6, fill=(255,255,255,alpha), outline=(*style["fg"][:3], alpha), width=2)
            # hole
            hr = int(S["h"]*0.006)
            draw.ellipse((tx0+int(tag_w*0.78)-hr, (ty0+ty1)//2-hr, tx0+int(tag_w*0.78)+hr, (ty0+ty1)//2+hr), fill=(*style["fg"][:3], alpha))
            # $ sign
            fs_d = max(12, int(S["h"]*0.022))
            f_d = _font(config.MGFX_FONT_BOLD, fs_d)
            draw.text((tx0+int(tag_w*0.22), (ty0+ty1)//2 - f_d.getbbox("$")[3]//2), "$", font=f_d, fill=(*style["fg"][:3], alpha))
            # no asterisk
        else: # check
            # checkmark rows
            for li in range(3):
                ly = icon_cy - int(icon_r*0.5) + li*int(S["h"]*0.028)
                # check circle
                cr = int(S["h"]*0.008)
                cx_c = icon_cx - int(icon_r*0.55)
                cy_c = ly + int(S["h"]*0.007)
                draw.ellipse((cx_c-cr, cy_c-cr, cx_c+cr, cy_c+cr), fill=(*config.CLEP_GREEN_RGB[:3], alpha), outline=None)
                # check
                p1 = (cx_c-3, cy_c)
                p2 = (cx_c, cy_c+3)
                p3 = (cx_c+5, cy_c-4)
                draw.line((p1,p2), fill=(255,255,255,alpha), width=2)
                draw.line((p2,p3), fill=(255,255,255,alpha), width=2)
                # line
                _rounded_rect(draw, (cx_c+int(S["w"]*0.012), ly, cx_c+int(icon_r*0.9), ly+int(S["h"]*0.008)), radius=3, fill=(100,100,95,int(180*alpha/255)), outline=None)
        # label
        fs_l = max(14, int(S["h"]*0.019))
        f_l = _font(config.MGFX_FONT_BOLD, fs_l)
        tw,_ = _text_bbox(it["label"], f_l)
        draw.text(((x0+x1)//2 - tw//2, y0+int(card_h*0.62)), it["label"], font=f_l, fill=(*style["fg"][:3], alpha))
        # sub
        fs_s = max(11, int(S["h"]*0.014))
        f_s = _font(config.MGFX_FONT_REGULAR, fs_s)
        tw2,_ = _text_bbox(it["sub"], f_s)
        draw.text(((x0+x1)//2 - tw2//2, y0+int(card_h*0.78)), it["sub"], font=f_s, fill=(*style["muted"][:3], int(180*alpha/255)))

def _draw_clep_trust(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:20-0:24 zoom into spreadsheet, one row flagged orange with tooltip
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2 + int(S["h"]*0.04)
    card_w = int(S["w"] * (0.72 if not is_portrait else 0.92))
    card_h = int(S["h"] * (0.48 if not is_portrait else 0.52))
    x0, y0 = cx - card_w//2, cy - card_h//2
    x1, y1 = cx + card_w//2, cy + card_h//2
    e = _ease_out_cubic(_clamp01(p/0.30))
    if e<=0: return
    alpha = int(255*e)
    _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.014), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(160*alpha/255)), width=max(1,S["stroke"]//2))
    # header
    hdr_h = int(S["h"]*0.09)
    _rounded_rect(draw, (x0,y0,x1,y0+hdr_h), radius=int(S["h"]*0.014), fill=(250,255,240,int(200*alpha/255)), outline=None)
    draw.rectangle((x0, y0+hdr_h//2, x1, y0+hdr_h), fill=(250,255,240,int(200*alpha/255)))
    # header text
    fs_h = max(12, int(S["h"]*0.017))
    f_h = _font(config.MGFX_FONT_BOLD, fs_h)
    draw.text((x0+int(S["w"]*0.014), y0+int(S["h"]*0.022)), "Clean spreadsheet — review", font=f_h, fill=(*style["fg"][:3], alpha))
    # rows
    rows = 5
    row_h = (card_h - hdr_h) // rows
    flagged = 2  # 0-indexed third row
    for ri in range(rows):
        ry0 = y0+hdr_h + ri*row_h
        ry1 = ry0 + row_h - int(S["h"]*0.004)
        # flagged row highlight orange
        if ri==flagged:
            # orange wash that pulses in
            fp = _clamp01((p-0.30)/0.35)
            if fp<=0: continue
            fe = _ease_out_cubic(fp)
            fa = int(255*fe*alpha/255)
            _rounded_rect(draw, (x0+int(S["w"]*0.008), ry0+2, x1-int(S["w"]*0.008), ry1-2), radius=6, fill=(*config.CLEP_ORANGE_RGB[:3], int(120*fa/255)), outline=(*config.CLEP_ORANGE_RGB[:3], int(180*fa/255)), width=2)
            # flag icon
            fx = x0 + int(S["w"]*0.014)
            fy = (ry0+ry1)//2
            fr = int(S["h"]*0.014)
            draw.ellipse((fx-fr, fy-fr, fx+fr, fy+fr), fill=(*config.CLEP_ORANGE_RGB[:3], fa), outline=None)
            draw.text((fx-4, fy-7), "!", font=_font(config.MGFX_FONT_BOLD, int(S["h"]*0.018)), fill=(255,255,255,fa))
            # tooltip
            if fp>0.45:
                tp = _clamp01((fp-0.45)/0.30)
                te = _ease_out_cubic(tp)
                ta = int(255*te)
                tip_w = int(S["w"]*0.42)
                tip_h = int(S["h"]*0.048)
                tx0 = fx + int(S["w"]*0.032)
                ty0 = fy - tip_h//2 - int(S["h"]*0.02) - int((1-te)*S["h"]*0.015)
                tx1, ty1 = tx0+tip_w, ty0+tip_h
                # ensure within card
                if tx1 > x1 - int(S["w"]*0.01):
                    tx1 = x1 - int(S["w"]*0.01)
                    tx0 = tx1 - tip_w
                _rounded_rect(draw, (tx0,ty0,tx1,ty1), radius=tip_h//2, fill=(30,30,28,int(240*ta/255)), outline=None)
                # tail
                draw.polygon([(tx0+int(S["w"]*0.012), ty1), (tx0+int(S["w"]*0.020), ty1+int(S["h"]*0.010)), (tx0+int(S["w"]*0.028), ty1)], fill=(30,30,28,int(240*ta/255)), outline=None)
                ft = _font(config.MGFX_FONT_REGULAR, max(11,int(S["h"]*0.013)))
                ttxt = "This doesn't match your statement total — check row 14."
                # truncate?
                if _text_bbox(ttxt, ft)[0] > tip_w - int(S["w"]*0.02):
                    ttxt = "Doesn't match total — check row 14."
                tw,_ = _text_bbox(ttxt, ft)
                draw.text((tx0+(tip_w-tw)//2, ty0+(tip_h - ft.getbbox(ttxt)[3])//2 -1), ttxt, font=ft, fill=(255,255,255,ta))
                # cursor click effect on flagged row at ~0.75
                if tp>0.65:
                    c_alpha = int(220*_clamp01((tp-0.65)/0.20)*ta/255)
                    cx_c = (x0+x1)//2
                    cy_c = fy
                    # click ring
                    cr = int(S["h"]*0.022 * _clamp01((tp-0.65)/0.20)) + int(S["h"]*0.012)
                    draw.ellipse((cx_c-cr, cy_c-cr, cx_c+cr, cy_c+cr), fill=None, outline=(30,30,28,int(120*c_alpha/255)), width=2)
                    # row expand slightly
                    # already highlighted
        # row content lines (for non-flagged also draw)
        # we draw lines for all rows but flagged already has highlight, still draw lines with muted
        # lines
        col_w = int((card_w)*0.28)
        start_x = x0 + int(S["w"]*0.045)
        for ci in range(3):
            lx0 = start_x + ci*col_w
            ly = (ry0+ry1)//2 - int(S["h"]*0.006)
            lw = int(col_w*0.70)
            # flagged row's middle column maybe bold
            col = (60,60,58) if ri!=flagged else (30,30,28)
            a2 = int(180*alpha/255) if ri!=flagged else int(220*alpha/255)
            _rounded_rect(draw, (lx0, ly, lx0+lw, ly+int(S["h"]*0.010)), radius=3, fill=(*col[:3], a2), outline=None)
        # bottom text
    # bottom caption
    if p>0.55:
        bp = _clamp01((p-0.55)/0.30)
        be = _ease_out_cubic(bp)
        ba = int(255*be*alpha/255)
        fs_b = max(12, int(S["h"]*0.016))
        f_b = _font(config.MGFX_FONT_BOLD, fs_b)
        bt = "We tell you what to check."
        tw,_ = _text_bbox(bt, f_b)
        draw.text((cx - tw//2, y1 + int(S["h"]*0.028) + int((1-be)*S["h"]*0.015)), bt, font=f_b, fill=(*style["fg"][:3], ba))

def _draw_clep_range(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:24-0:27 three docs fly in and transform to sheets
    is_portrait = config.PORTRAIT
    cx, cy = S["w"]//2, S["h"]//2
    card_w = int(S["w"] * (0.18 if not is_portrait else 0.26))
    card_h = int(S["h"] * (0.26 if not is_portrait else 0.22))
    gap = int(S["w"]*0.06)
    total_w = 3*card_w + 2*gap
    start_x = cx - total_w//2
    base_y = cy - card_h//2 + int(S["h"]*0.02)
    labels = ["Bank\nstatement", "Invoice", "Receipt"]
    for idx in range(3):
        delay = idx*0.12
        cp = _clamp01((p - delay)/0.40)
        if cp<=0: continue
        e = _ease_out_back(cp) if cp<0.85 else _ease_out_cubic(cp)
        alpha = int(255*e)
        x0 = start_x + idx*(card_w+gap)
        y0 = base_y + int((1-e)*S["h"]*0.06)
        x1, y1 = x0+card_w, y0+card_h
        # doc card
        _rounded_rect(draw, (x0,y0,x1,y1), radius=int(S["h"]*0.012), fill=(255,255,255,alpha), outline=(*style["card_border"][:3], int(180*alpha/255)), width=max(1,S["stroke"]//2))
        # icon area top
        icon_y = y0 + int(card_h*0.28)
        icon_x = (x0+x1)//2
        # doc icon (before transform)
        # stage 1: doc, stage 2: sheet
        trans_p = _clamp01((p - 0.45 - idx*0.08)/0.35)
        if trans_p < 0.5:
            # doc icon - folded corner
            doc_w = int(card_w*0.42)
            doc_h = int(card_h*0.38)
            dx0 = icon_x - doc_w//2
            dy0 = icon_y - doc_h//2
            dx1, dy1 = dx0+doc_w, dy0+doc_h
            _rounded_rect(draw, (dx0,dy0,dx1,dy1), radius=4, fill=(250,250,245,alpha), outline=(*style["fg"][:3], alpha), width=2)
            # folded corner
            fold = int(doc_w*0.22)
            draw.polygon([(dx1-fold, dy0), (dx1, dy0), (dx1, dy0+fold)], fill=(*config.CLEP_LIME_RGB[:3], alpha), outline=(*style["fg"][:3], alpha), width=1)
            # lines
            for li in range(3):
                ly = dy0 + int(doc_h*0.28) + li*int(S["h"]*0.012)
                _rounded_rect(draw, (dx0+int(S["w"]*0.006), ly, dx0+doc_w-int(S["w"]*0.006), ly+int(S["h"]*0.004)), radius=2, fill=(180,180,175,int(160*alpha/255)), outline=None)
        else:
            # sheet icon - grid
            te = _ease_out_cubic(_clamp01((trans_p-0.5)/0.5))
            ta = int(255*te*alpha/255)
            doc_w = int(card_w*0.42)
            doc_h = int(card_h*0.38)
            dx0 = icon_x - doc_w//2
            dy0 = icon_y - doc_h//2 + int((1-te)*S["h"]*0.015)
            dx1, dy1 = dx0+doc_w, dy0+doc_h
            _rounded_rect(draw, (dx0,dy0,dx1,dy1), radius=4, fill=(255,255,255,ta), outline=(*config.CLEP_LIME_RGB[:3], ta), width=2)
            # small grid inside
            for r in range(3):
                for c in range(3):
                    cx_ = dx0 + int(S["w"]*0.006) + c*int(doc_w*0.30)
                    cy_ = dy0 + int(S["h"]*0.010) + r*int(S["h"]*0.016)
                    _rounded_rect(draw, (cx_, cy_, cx_+int(doc_w*0.22), cy_+int(S["h"]*0.008)), radius=2, fill=(*config.CLEP_LIME_RGB[:3], int(180*ta/255)) if (r==0) else (230,230,225,int(160*ta/255)), outline=None)
            # sparkle
            if te>0.6:
                sx = dx1 - int(S["w"]*0.008)
                sy = dy0 - int(S["h"]*0.008)
                sr = int(S["h"]*0.006)
                draw.ellipse((sx-sr, sy-sr, sx+sr, sy+sr), fill=(*config.CLEP_LIME_RGB[:3], int(220*ta/255)), outline=None)
        # label
        fs_l = max(12, int(S["h"]*0.016))
        f_l = _font(config.MGFX_FONT_BOLD, fs_l)
        label = labels[idx]
        if "\n" in label:
            lines = label.split("\n")
            for li, ln in enumerate(lines):
                tw,_ = _text_bbox(ln, f_l)
                draw.text(((x0+x1)//2 - tw//2, y0+int(card_h*0.62) + li*int(S["h"]*0.022)), ln, font=f_l, fill=(*style["fg"][:3], alpha))
        else:
            tw,_ = _text_bbox(label, f_l)
            draw.text(((x0+x1)//2 - tw//2, y0+int(card_h*0.68)), label, font=f_l, fill=(*style["fg"][:3], alpha))
        # arrow to next
        if idx<2 and p>0.55:
            ap = _clamp01((p-0.55 - idx*0.08)/0.25)
            if ap>0:
                ae = _ease_out_cubic(ap)
                aa = int(180*ae*alpha/255)
                ax0 = x1 + int(gap*0.25)
                ax1 = x1 + int(gap*0.75)
                ay = (y0+y1)//2
                draw.line((ax0, ay, ax1, ay), fill=(*style["muted"][:3], aa), width=max(2,S["stroke"]//2))
                # arrow head
                ah = int(S["h"]*0.012)
                draw.line((ax1-ah, ay-ah//2, ax1, ay), fill=(*style["muted"][:3], aa), width=max(2,S["stroke"]//2))
                draw.line((ax1-ah, ay+ah//2, ax1, ay), fill=(*style["muted"][:3], aa), width=max(2,S["stroke"]//2))
    # bottom text
    if p>0.62:
        bp = _clamp01((p-0.62)/0.28)
        be = _ease_out_cubic(bp)
        ba = int(255*be)
        fs_b = max(13, int(S["h"]*0.018))
        f_b = _font(config.MGFX_FONT_BOLD, fs_b)
        bt = "Statements. Invoices. Receipts."
        tw,_ = _text_bbox(bt, f_b)
        draw.text((cx - tw//2, base_y+card_h+int(S["h"]*0.045) + int((1-be)*S["h"]*0.015)), bt, font=f_b, fill=(*style["fg"][:3], ba))
        # lime underline
        if be>0.6:
            lx0 = cx - tw//2
            lx1 = lx0 + int(tw*_clamp01((be-0.6)/0.4))
            ly = base_y+card_h+int(S["h"]*0.068)
            draw.line((lx0, ly, lx1, ly), fill=(*config.CLEP_LIME_RGB[:3], int(180*ba/255)), width=max(2,S["stroke"]//2))

def _draw_clep_lockup(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0:27-0:30 clean end-card — Clep logo, Drop it. Done., clep.com
    # cream bg with lime glow top
    # lime glow at top as in hero
    glow_a = int(22 * _clamp01(p/0.4))
    draw.rectangle((0,0,S["w"],int(S["h"]*0.22)), fill=(*config.CLEP_LIME_RGB[:3], glow_a))
    cx, cy = S["w"]//2, S["h"]//2
    # logo "clep" - big
    e = _ease_out_back(_clamp01(p/0.38))
    alpha = int(255*e)
    # scale
    logo = "clep"
    fs_logo = max(56, int(S["h"]*0.11))
    f_logo = _font(config.MGFX_FONT_BLACK, fs_logo)
    tw,_ = _text_bbox(logo, f_logo)
    lx = cx - tw//2
    ly = cy - int(S["h"]*0.14) + int((1-e)*S["h"]*0.03)
    draw.text((lx, ly), logo, font=f_logo, fill=(*style["fg"][:3], alpha))
    # tagline Drop it. Done.
    if p>0.22:
        tp = _clamp01((p-0.22)/0.32)
        te = _ease_out_cubic(tp)
        ta = int(255*te)
        # "Drop it." black, "Done." green italic
        fs_t = max(28, int(S["h"]*0.048))
        f_t = _font(config.MGFX_FONT_BOLD, fs_t)
        f_ti = _font(config.MGFX_FONT_SERIF_ITALIC, fs_t)
        line = "Drop it. Done."
        # split
        part1 = "Drop it. "
        part2 = "Done."
        tw1,_ = _text_bbox(part1, f_t)
        tw2,_ = _text_bbox(part2, f_ti)
        total = tw1+tw2
        sx = cx - total//2
        sy = cy + int(S["h"]*0.02) + int((1-te)*S["h"]*0.015)
        draw.text((sx, sy), part1, font=f_t, fill=(*style["fg"][:3], ta))
        draw.text((sx+tw1, sy), part2, font=f_ti, fill=(*config.CLEP_GREEN_RGB[:3], ta))
    # URL
    if p>0.45:
        up = _clamp01((p-0.45)/0.28)
        ue = _ease_out_cubic(up)
        ua = int(255*ue*alpha/255)
        fs_u = max(14, int(S["h"]*0.019))
        f_u = _font(config.MGFX_FONT_REGULAR, fs_u)
        url = spec.get("url") or "clep.com"
        twu,_ = _text_bbox(url, f_u)
        uy = cy + int(S["h"]*0.12) + int((1-ue)*S["h"]*0.012)
        # pill behind url
        pad = int(S["w"]*0.014)
        ph = int(S["h"]*0.036)
        px0 = cx - twu//2 - pad
        py0 = uy - int(S["h"]*0.010)
        px1 = cx + twu//2 + pad
        py1 = py0 + ph
        _rounded_rect(draw, (px0,py0,px1,py1), radius=ph//2, fill=(255,255,255,int(220*ua/255)), outline=(*style["card_border"][:3], int(160*ua/255)), width=1)
        draw.text((cx - twu//2, py0+int(S["h"]*0.008)), url, font=f_u, fill=(*style["muted"][:3], ua))


# ── Clep launch video (Sep 2026 brief) ───────────────────────────────────
# Editorial, minimal, off-white #F3F3F1, ink #111714, lime #B8FF19,
# green #4D8A18, gray #5D6662. Physical motion: easeOutCubic /
# easeInOutCubic / spring overshoot, 300-500ms UI, 600-900ms hero type.

def _launch_shadow(draw: ImageDraw.ImageDraw, rect, radius: int, alpha: int, S: dict, dy_frac: float = 0.008):
    x0, y0, x1, y1 = rect
    dy = int(S["h"] * dy_frac)
    _rounded_rect(draw, (x0, y0 + dy, x1, y1 + dy), radius=radius,
                  fill=(17, 23, 20, int(alpha * 0.10)), outline=None)

def _launch_cursor(draw: ImageDraw.ImageDraw, x: int, y: int, s: int, alpha: int):
    # classic arrow cursor, s = height in px
    k = s / 24.0
    pts = [(x + 5 * k, y + 3 * k), (x + 19 * k, y + 10 * k),
           (x + 12.5 * k, y + 11.5 * k), (x + 9 * k, y + 18 * k)]
    draw.polygon(pts, fill=(17, 23, 20, alpha), outline=(255, 255, 255, alpha))

def _launch_ripple(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, alpha: int, S: dict):
    if alpha <= 0 or r <= 0:
        return
    w = max(2, S["stroke"] // 2)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=None,
                 outline=(77, 138, 24, alpha), width=w)

def _launch_rich_line(draw: ImageDraw.ImageDraw, segs: list, cx: int, y_top: int,
                      p: float, S: dict, stagger: float = 0.07, dur: float = 0.34,
                      lift_frac: float = 0.016):
    """Word-by-word upward reveal + blur->sharp (ghost pass) for mixed
    color/font segments. segs = [(word, rgb, font)]. Returns total width."""
    if not segs:
        return 0
    space_w = max(_text_bbox(" ", f)[0] for _, _, f in segs)
    widths = [_text_bbox(w, f)[0] for w, _, f in segs]
    total = sum(widths) + space_w * (len(segs) - 1)
    x = cx - total // 2
    for i, (w, col, f) in enumerate(segs):
        wp = _clamp01((p - i * stagger) / dur)
        if wp <= 0:
            x += widths[i] + space_w
            continue
        e = _ease_out_cubic(wp)
        a = int(255 * e)
        yoff = int((1 - e) * S["h"] * lift_frac)
        if e < 1.0:  # blurred ghost trailing below while landing
            draw.text((x, y_top + yoff + int(S["h"] * 0.004)), w, font=f,
                      fill=(*col[:3], int(70 * e)))
        draw.text((x, y_top + yoff), w, font=f, fill=(*col[:3], a))
        x += widths[i] + space_w
    return total

def _launch_segs(text_words: list[str], color, font, accents: dict | None = None):
    """Split plain words, applying accent (color, font) overrides."""
    out = []
    for w in text_words:
        key = w.strip(".,!?—")
        if accents and key in accents:
            out.append((w, *accents[key]))
        else:
            out.append((w, color, font))
    return out

def _draw_launch_hook(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0.0-2.5s: "You built it." then "Now show it." (show it = italic green)
    ink, green = style["fg"], style["accent2"]
    f_serif = _font(config.MGFX_FONT_SERIF, max(40, int(S["h"] * 0.078)))
    f_ital = _font(config.MGFX_FONT_SERIF_ITALIC, max(40, int(S["h"] * 0.078)))
    lh = int(S["h"] * 0.105)
    cx, cy = S["w"] // 2, S["h"] // 2
    y1 = cy - lh + int(S["h"] * 0.008)
    y2 = cy + int(S["h"] * 0.012)
    _launch_rich_line(draw, [(w, ink, f_serif) for w in "You built it.".split()],
                      cx, y1, _clamp01(p / 0.42), S, stagger=0.075, dur=0.36)
    segs2 = [("Now", ink, f_serif), ("show", green, f_ital), ("it.", green, f_ital)]
    _launch_rich_line(draw, segs2, cx, y2, _clamp01((p - 0.48) / 0.52), S,
                      stagger=0.09, dur=0.36)

def _draw_launch_code(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 2.5-5.5s: hook type drifts up, code card slides in, lime highlight on
    # data-clip attr, subtext "Your code already knows the feature."
    ink, green, lime = style["fg"], style["accent2"], style["accent"]
    gray, border = style["muted"], style["card_border"]
    cx, cy = S["w"] // 2, S["h"] // 2
    # hook residue drifting upward + fading (first 30%)
    if p < 0.38:
        rp = _clamp01(p / 0.38)
        f_mini = _font(config.MGFX_FONT_SERIF, max(20, int(S["h"] * 0.034)))
        t = "Now show it."
        tw, _ = _text_bbox(t, f_mini)
        a = int(255 * (1 - rp))
        if a > 0:
            draw.text((cx - tw // 2, int(S["h"] * 0.16) - int(rp * S["h"] * 0.06)),
                      t, font=f_mini, fill=(*gray[:3], a))
    # code card slides up from bottom
    e = _ease_out_cubic(_clamp01(p / 0.34))
    if e <= 0:
        return
    a = int(255 * e)
    cw, ch = int(S["w"] * 0.52), int(S["h"] * 0.34)
    x0 = cx - cw // 2
    y0 = cy - ch // 2 + int(S["h"] * 0.02) + int((1 - e) * S["h"] * 0.14)
    x1, y1 = x0 + cw, y0 + ch
    _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.014), a, S)
    _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.014),
                  fill=(255, 255, 255, a),
                  outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
    # header dots + filename
    dot_r = int(S["h"] * 0.007)
    sy = y0 + int(S["h"] * 0.030)
    sx = x0 + int(S["w"] * 0.018)
    for i, col in enumerate([(217, 220, 216), (217, 220, 216), (184, 255, 25)]):
        dx = sx + i * (dot_r * 2 + int(S["w"] * 0.006))
        draw.ellipse((dx - dot_r, sy - dot_r, dx + dot_r, sy + dot_r), fill=(*col, a))
    f_fn = _font(config.MGFX_FONT_REGULAR, max(12, int(S["h"] * 0.016)))
    draw.text((sx + int(S["w"] * 0.045), sy - f_fn.getbbox("Ag")[3] // 2),
              "FeatureCard.tsx", font=f_fn, fill=(*gray[:3], int(200 * a / 255)))
    # code lines
    f_code = _font(config.MGFX_FONT_REGULAR, max(14, int(S["h"] * 0.024)))
    f_bold = _font(config.MGFX_FONT_BOLD, max(14, int(S["h"] * 0.024)))
    tx = x0 + int(S["w"] * 0.030)
    ty = y0 + int(S["h"] * 0.075)
    lh = int(S["h"] * 0.048)
    pre, attr, post = '<div ', 'data-clip="ai-research"', '>'
    # lime highlight sweep behind attr (p 0.38-0.62)
    hp = _ease_out_cubic(_clamp01((p - 0.38) / 0.24))
    pre_w, _ = _text_bbox(pre, f_code)
    attr_w, attr_h = _text_bbox(attr, f_bold)
    if hp > 0:
        pad = int(S["h"] * 0.006)
        hx0 = tx + pre_w - pad
        hx1 = hx0 + int((attr_w + pad * 2) * hp)
        hy0 = ty - pad // 2
        hy1 = ty + attr_h + pad
        _rounded_rect(draw, (hx0, hy0, hx1, hy1), radius=int(S["h"] * 0.006),
                      fill=(*lime[:3], int(255 * a / 255)), outline=None)
    if p > 0.12:
        la = int(255 * _clamp01((p - 0.12) / 0.2) * a / 255)
        draw.text((tx, ty), pre, font=f_code, fill=(*gray[:3], la))
        draw.text((tx + pre_w, ty), attr, font=f_bold, fill=(17, 23, 20, la))
        tw_full, _ = _text_bbox(pre + attr, f_code)
        draw.text((tx + pre_w + attr_w, ty), post, font=f_code, fill=(*gray[:3], la))
    if p > 0.24:
        la2 = int(255 * _clamp01((p - 0.24) / 0.2) * a / 255)
        draw.text((tx, ty + lh), "  <Research />", font=f_code, fill=(*ink[:3], la2))
    if p > 0.32:
        la3 = int(255 * _clamp01((p - 0.32) / 0.2) * a / 255)
        draw.text((tx, ty + lh * 2), "</div>", font=f_code, fill=(*gray[:3], la3))
    # subtext
    if p > 0.60:
        sp = _clamp01((p - 0.60) / 0.40)
        f_sub = _font(config.MGFX_FONT_BOLD, max(16, int(S["h"] * 0.030)))
        f_sub_i = _font(config.MGFX_FONT_SERIF_ITALIC, max(16, int(S["h"] * 0.030)))
        segs = _launch_segs("Your code already knows the feature.".split(), ink, f_sub,
                            {"feature": (green, f_sub_i)})
        _launch_rich_line(draw, segs, cx, y1 + int(S["h"] * 0.045), sp, S,
                          stagger=0.06, dur=0.30)

def _draw_launch_browser(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 5.5-8.0s: code -> browser window, cursor clicks lime Research button
    ink, lime, gray, border = style["fg"], style["accent"], style["muted"], style["card_border"]
    cx, cy = S["w"] // 2, S["h"] // 2
    e = _ease_out_cubic(_clamp01(p / 0.30))
    if e <= 0:
        return
    a = int(255 * e)
    sc = 0.90 + 0.10 * e
    cw, ch = int(S["w"] * 0.46 * sc), int(S["h"] * 0.60 * sc)
    x0, y0 = cx - cw // 2, cy - ch // 2
    x1, y1 = x0 + cw, y0 + ch
    _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.014), a, S)
    _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.014),
                  fill=(255, 255, 255, a),
                  outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
    # chrome bar
    bar_h = int(ch * 0.11)
    _rounded_rect(draw, (x0, y0, x1, y0 + bar_h), radius=int(S["h"] * 0.014),
                  fill=(235, 235, 232, a), outline=None)
    draw.rectangle((x0, y0 + bar_h // 2, x1, y0 + bar_h), fill=(235, 235, 232, a))
    dot_r = int(S["h"] * 0.006)
    for i in range(3):
        dx = x0 + int(S["w"] * 0.016) + i * (dot_r * 2 + int(S["w"] * 0.005))
        draw.ellipse((dx - dot_r, y0 + bar_h // 2 - dot_r, dx + dot_r, y0 + bar_h // 2 + dot_r),
                     fill=(217, 220, 216, a))
    # title + subtitle
    f_t = _font(config.MGFX_FONT_BOLD, max(20, int(S["h"] * 0.042)))
    f_s = _font(config.MGFX_FONT_REGULAR, max(12, int(S["h"] * 0.021)))
    t = "AI Research"
    tw, _ = _text_bbox(t, f_t)
    ty = y0 + bar_h + int(S["h"] * 0.045)
    draw.text((cx - tw // 2, ty), t, font=f_t, fill=(*ink[:3], a))
    s = "What do you want to research?"
    sw, _ = _text_bbox(s, f_s)
    draw.text((cx - sw // 2, ty + int(S["h"] * 0.075)), s, font=f_s, fill=(*gray[:3], a))
    # input pill with typing (p 0.30-0.55)
    iw, ih = int(cw * 0.72), int(S["h"] * 0.062)
    ix0, iy0 = cx - iw // 2, ty + int(S["h"] * 0.135)
    ix1, iy1 = ix0 + iw, iy0 + ih
    _rounded_rect(draw, (ix0, iy0, ix1, iy1), radius=ih // 2,
                  fill=(255, 255, 255, a),
                  outline=(*border[:3], int(220 * a / 255)), width=max(1, S["stroke"] // 2))
    query = "AI browser agents"
    nch = int(len(query) * _ease_out_cubic(_clamp01((p - 0.30) / 0.25)) + 0.5)
    f_q = _font(config.MGFX_FONT_REGULAR, max(13, int(S["h"] * 0.020)))
    vis = query[:nch]
    qx = ix0 + int(S["w"] * 0.018)
    _, qh = _text_bbox("Ag", f_q)
    qy = (iy0 + iy1) // 2 - qh // 2 - f_q.getbbox("Ag")[1] // 2
    if vis:
        draw.text((qx, qy), vis, font=f_q, fill=(*ink[:3], a))
    if 0.30 < p < 0.55 and (int(p * 10) % 10) < 6 and nch < len(query):
        vw, _ = _text_bbox(vis, f_q)
        draw.line((qx + vw + int(S["w"] * 0.003), qy, qx + vw + int(S["w"] * 0.003), qy + qh),
                  fill=(*ink[:3], a), width=max(2, S["stroke"] // 2))
    # lime Research button with click punch
    bw, bh = int(cw * 0.44), int(S["h"] * 0.062)
    bx0, by0 = cx - bw // 2, iy1 + int(S["h"] * 0.030)
    bx1, by1 = bx0 + bw, by0 + bh
    punch = 0.0
    if p > 0.76:
        punch = 0.10 * math.exp(-(p - 0.76) * 14) * math.cos((p - 0.76) * 28)
    bw2, bh2 = int(bw * (1 + punch)), int(bh * (1 + punch))
    bx0, by0 = cx - bw2 // 2, (by0 + by1) // 2 - bh2 // 2
    bx1, by1 = bx0 + bw2, by0 + bh2
    _rounded_rect(draw, (bx0, by0, bx1, by1), radius=bh2 // 2,
                  fill=(*lime[:3], a), outline=None)
    f_b = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.020)))
    bt = "Research"
    btw, _ = _text_bbox(bt, f_b)
    draw.text((cx - btw // 2, (by0 + by1) // 2 - f_b.getbbox(bt)[3] // 2), bt,
              font=f_b, fill=(17, 23, 20, a))
    # cursor flight (p 0.52-0.76) then ripple (0.76-1.0)
    btn_cx, btn_cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    if 0.52 <= p <= 0.78:
        cp = _ease_in_out_cubic(_clamp01((p - 0.52) / 0.24))
        cur_x = int(_lerp(S["w"] * 0.80, btn_cx + bw * 0.12, cp))
        cur_y = int(_lerp(S["h"] * 0.98, btn_cy + bh * 0.10, cp))
        _launch_cursor(draw, cur_x, cur_y, int(S["h"] * 0.030), a)
    elif p > 0.78:
        _launch_cursor(draw, int(btn_cx + bw * 0.12), int(btn_cy + bh * 0.10),
                       int(S["h"] * 0.030), a)
    if p > 0.76:
        rp = _clamp01((p - 0.76) / 0.24)
        _launch_ripple(draw, btn_cx, btn_cy, int(S["h"] * (0.02 + 0.075 * _ease_out_cubic(rp))),
                       int(200 * (1 - rp) * a / 255), S)
        if rp < 0.6:
            _launch_ripple(draw, btn_cx, btn_cy, int(S["h"] * (0.015 + 0.045 * _ease_out_cubic(rp / 0.6))),
                           int(140 * (1 - rp / 0.6) * a / 255), S)

def _draw_launch_camera(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 8.0-11.0s: browser becomes camera frame, smooth zoom into the button:
    # RESEARCH -> LOADING -> RESULTS, kinetic labels around it.
    ink, lime, green, gray = style["fg"], style["accent"], style["accent2"], style["muted"]
    cx, cy = S["w"] // 2, S["h"] // 2 - int(S["h"] * 0.02)
    e = _ease_out_cubic(_clamp01(p / 0.25))
    if e <= 0:
        return
    a = int(255 * e)
    # camera frame
    fw, fh = int(S["w"] * 0.66), int(S["h"] * 0.62)
    fx0, fy0 = cx - fw // 2, cy - fh // 2
    fx1, fy1 = fx0 + fw, fy0 + fh
    draw.rounded_rectangle((fx0, fy0, fx1, fy1), radius=int(S["h"] * 0.012),
                           fill=None, outline=(*ink[:3], int(220 * a / 255)),
                           width=max(2, S["stroke"] // 2))
    # lime corner ticks
    tick = int(S["h"] * 0.035)
    cw_ = max(3, S["stroke"])
    for (tx, ty, dx, dy) in ((fx0, fy0, 1, 1), (fx1, fy0, -1, 1),
                             (fx0, fy1, 1, -1), (fx1, fy1, -1, -1)):
        draw.line((tx, ty, tx + dx * tick, ty), fill=(*lime[:3], a), width=cw_)
        draw.line((tx, ty, tx, ty + dy * tick), fill=(*lime[:3], a), width=cw_)
    # smooth camera zoom on the button
    zoom = 1.0 + 0.45 * _ease_in_out_cubic(_clamp01((p - 0.10) / 0.75))
    stage = 0 if p < 0.38 else (1 if p < 0.68 else 2)
    # stage crossfade helper
    def stage_alpha(s, lo, hi):
        return _clamp01((p - lo) / 0.06) * (1 - _clamp01((p - hi) / 0.06))
    bw, bh = int(S["w"] * 0.30 * zoom), int(S["h"] * 0.085 * zoom)
    bw = min(bw, int(fw * 0.86))
    bx0, by0 = cx - bw // 2, cy - bh // 2
    bx1, by1 = bx0 + bw, by0 + bh
    f_b = _font(config.MGFX_FONT_BOLD, max(14, int(S["h"] * 0.022 * zoom)))
    if stage == 0:
        sa = int(a * (1 - _clamp01((p - 0.32) / 0.06)))
        _rounded_rect(draw, (bx0, by0, bx1, by1), radius=bh // 2,
                      fill=(*lime[:3], sa), outline=None)
        t = "RESEARCH"
        tw, _ = _text_bbox(t, f_b)
        if tw < bw - 20:
            draw.text((cx - tw // 2, (by0 + by1) // 2 - f_b.getbbox(t)[3] // 2), t,
                      font=f_b, fill=(17, 23, 20, sa))
        _launch_cursor(draw, bx1 - int(S["w"] * 0.02), by1 - int(S["h"] * 0.01),
                       int(S["h"] * 0.028), sa)
    elif stage == 1:
        sa = int(a * stage_alpha(1, 0.38, 0.62))
        _rounded_rect(draw, (bx0, by0, bx1, by1), radius=bh // 2,
                      fill=(255, 255, 255, sa),
                      outline=(*ink[:3], int(160 * sa / 255)), width=max(2, S["stroke"] // 2))
        # spinner left + LOADING
        sp_cx = bx0 + int(bh * 0.7)
        sp_cy = (by0 + by1) // 2
        sp_r = int(bh * 0.22)
        for i in range(10):
            ang = math.radians(i * 36 + p * 540)
            dx = int(sp_r * math.cos(ang))
            dy = int(sp_r * math.sin(ang))
            ia = int(sa * (0.25 + 0.75 * i / 9))
            draw.ellipse((sp_cx + dx - 3, sp_cy + dy - 3, sp_cx + dx + 3, sp_cy + dy + 3),
                         fill=(*green[:3], ia))
        t = "LOADING"
        tw, _ = _text_bbox(t, f_b)
        draw.text((sp_cx + int(bh * 0.55), sp_cy - f_b.getbbox(t)[3] // 2), t,
                  font=f_b, fill=(*ink[:3], sa))
    else:
        sa = int(a * _clamp01((p - 0.68) / 0.08))
        rw, rh = min(int(S["w"] * 0.40), int(fw * 0.88)), int(S["h"] * 0.30)
        rx0, ry0 = cx - rw // 2, cy - rh // 2
        rx1, ry1 = rx0 + rw, ry0 + rh
        _launch_shadow(draw, (rx0, ry0, rx1, ry1), int(S["h"] * 0.010), sa, S)
        _rounded_rect(draw, (rx0, ry0, rx1, ry1), radius=int(S["h"] * 0.010),
                      fill=(255, 255, 255, sa),
                      outline=(217, 220, 216, int(220 * sa / 255)), width=max(1, S["stroke"] // 2))
        # header: check + RESULTS
        cr = int(S["h"] * 0.016)
        ccx = rx0 + int(S["w"] * 0.025) + cr
        ccy = ry0 + int(S["h"] * 0.038)
        draw.ellipse((ccx - cr, ccy - cr, ccx + cr, ccy + cr), fill=(*lime[:3], sa))
        draw.line((ccx - cr // 2, ccy, ccx - 1, ccy + cr // 2), fill=(17, 23, 20, sa), width=3)
        draw.line((ccx - 1, ccy + cr // 2, ccx + cr // 2 + 2, ccy - cr // 2), fill=(17, 23, 20, sa), width=3)
        f_r = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.020)))
        draw.text((ccx + cr + int(S["w"] * 0.010), ccy - f_r.getbbox("Ag")[3] // 2),
                  "RESULTS", font=f_r, fill=(*ink[:3], sa))
        for i in range(3):
            lp = _clamp01((p - 0.74 - i * 0.06) / 0.18)
            if lp <= 0:
                continue
            la = int(sa * _ease_out_cubic(lp))
            ly = ry0 + int(S["h"] * 0.085) + i * int(S["h"] * 0.048)
            lw = int(rw * (0.78 - i * 0.09))
            _rounded_rect(draw, (rx0 + int(S["w"] * 0.025), ly,
                                 rx0 + int(S["w"] * 0.025) + lw, ly + int(S["h"] * 0.020)),
                          radius=int(S["h"] * 0.008), fill=(238, 240, 237, la), outline=None)
    # kinetic labels (gray + lime dot)
    f_l = _font(config.MGFX_FONT_BOLD, max(11, int(S["h"] * 0.016)))
    labels = [("AUTO-ZOOM", fx0 + int(S["w"] * 0.01), fy0 - int(S["h"] * 0.048), 0.10),
              ("CURSOR", fx1 + int(S["w"] * 0.012), cy - int(S["h"] * 0.02), 0.30),
              ("MOTION", fx0 + int(S["w"] * 0.01), fy1 + int(S["h"] * 0.020), 0.50)]
    for txt, lx, ly, t0 in labels:
        lp = _ease_out_cubic(_clamp01((p - t0) / 0.22))
        if lp <= 0:
            continue
        la = int(255 * lp * a / 255)
        dx = lx + int((1 - lp) * S["w"] * 0.008)
        dr = int(S["h"] * 0.006)
        draw.ellipse((dx - dr, ly - dr, dx + dr, ly + dr), fill=(*lime[:3], la))
        draw.text((dx + dr + int(S["w"] * 0.005), ly - f_l.getbbox(txt)[3] // 2), txt,
                  font=f_l, fill=(*gray[:3], la))

def _draw_launch_norecord(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 11.0-14.5s: results fill screen -> shrink to floating video card.
    # "No recording." / "No editing." ("No" flashes green).
    ink, lime, green, gray, border = (style["fg"], style["accent"], style["accent2"],
                                      style["muted"], style["card_border"])
    cx = S["w"] // 2
    # card morph: fullscreen-ish -> floating video card
    mp = _ease_in_out_cubic(_clamp01((p - 0.38) / 0.32))
    fx, fy = S["w"] * 0.86, S["h"] * 0.80  # full
    tx, ty = S["w"] * 0.52, S["h"] * 0.40  # floating
    cw = int(_lerp(fx, tx, mp))
    ch = int(_lerp(fy, ty, mp))
    cy = int(_lerp(S["h"] * 0.46, S["h"] * 0.36, mp))
    x0, y0 = cx - cw // 2, cy - ch // 2
    x1, y1 = x0 + cw, y0 + ch
    e = _ease_out_cubic(_clamp01(p / 0.18))
    a = int(255 * e)
    _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.014), a, S, dy_frac=0.012)
    _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.014),
                  fill=(255, 255, 255, a),
                  outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
    # result rows (fade as card shrinks)
    rows_a = int(a * (1 - mp))
    if rows_a > 0:
        # header: Results + source pill
        f_h = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.020)))
        draw.text((x0 + int(S["w"] * 0.03), y0 + int(S["h"] * 0.022)), "Results",
                  font=f_h, fill=(*ink[:3], rows_a))
        pill_t = "3 sources"
        f_p = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.013)))
        ptw, _ = _text_bbox(pill_t, f_p)
        ph = int(S["h"] * 0.028)
        ppx1 = x1 - int(S["w"] * 0.03)
        ppx0 = ppx1 - ptw - int(S["w"] * 0.024)
        ppy0 = y0 + int(S["h"] * 0.018)
        _rounded_rect(draw, (ppx0, ppy0, ppx1, ppy0 + ph), radius=ph // 2,
                      fill=(*lime[:3], rows_a), outline=None)
        draw.text((ppx0 + int(S["w"] * 0.012), ppy0 + ph // 2 - f_p.getbbox(pill_t)[3] // 2),
                  pill_t, font=f_p, fill=(17, 23, 20, rows_a))
        rh = int(S["h"] * 0.105 * (ch / (S["h"] * 0.80)))
        gap_r = int(S["h"] * 0.018)
        for i in range(4):
            rp = _clamp01((p - 0.06 - i * 0.07) / 0.22)
            if rp <= 0:
                continue
            ra = int(rows_a * _ease_out_cubic(rp))
            ry0 = y0 + int(S["h"] * 0.085) + i * (rh + gap_r)
            ry1 = ry0 + rh
            if ry1 > y1 - int(S["h"] * 0.075):
                break
            _rounded_rect(draw, (x0 + int(S["w"] * 0.03), ry0, x1 - int(S["w"] * 0.03), ry1),
                          radius=int(S["h"] * 0.008), fill=(243, 243, 241, ra), outline=None)
            cr = int(S["h"] * 0.011)
            ccx = x0 + int(S["w"] * 0.055) + cr
            ccy = (ry0 + ry1) // 2
            draw.ellipse((ccx - cr, ccy - cr, ccx + cr, ccy + cr), fill=(*lime[:3], ra))
            # two-line skeleton: title + sub
            tx0 = ccx + cr + int(S["w"] * 0.012)
            lw1 = int(cw * (0.52 - i * 0.05))
            lw2 = int(cw * (0.34 - i * 0.03))
            _rounded_rect(draw, (tx0, ccy - int(S["h"] * 0.016),
                                 tx0 + lw1, ccy - int(S["h"] * 0.002)),
                          radius=int(S["h"] * 0.005), fill=(120, 126, 122, ra), outline=None)
            _rounded_rect(draw, (tx0, ccy + int(S["h"] * 0.004),
                                 tx0 + lw2, ccy + int(S["h"] * 0.014)),
                          radius=int(S["h"] * 0.005), fill=(200, 204, 200, ra), outline=None)
        # footer assembling note (stage A only)
        if mp < 0.5:
            fa_ = int(rows_a * (1 - mp * 2) * _clamp01((p - 0.30) / 0.2))
            if fa_ > 0:
                f_f = _font(config.MGFX_FONT_REGULAR, max(11, int(S["h"] * 0.015)))
                ft = "Clep is assembling your clip…"
                ftw, _ = _text_bbox(ft, f_f)
                draw.text((cx - ftw // 2, y1 - int(S["h"] * 0.048)), ft,
                          font=f_f, fill=(*gray[:3], max(0, fa_)))
    # floating video chrome: play + lime progress (fade in as morph completes)
    if mp > 0.25:
        va = int(a * _clamp01((mp - 0.25) / 0.4))
        pr = int(S["h"] * 0.030)
        pcx, pcy = cx, (y0 + y1) // 2 - int(S["h"] * 0.01)
        draw.ellipse((pcx - pr, pcy - pr, pcx + pr, pcy + pr), fill=(17, 23, 20, va))
        draw.polygon([(pcx - int(pr * 0.35), pcy - int(pr * 0.5)),
                      (pcx - int(pr * 0.35), pcy + int(pr * 0.5)),
                      (pcx + int(pr * 0.55), pcy)], fill=(255, 255, 255, va))
        bar_pad = int(S["w"] * 0.03)
        bar_y = y1 - int(S["h"] * 0.030)
        draw.line((x0 + bar_pad, bar_y, x1 - bar_pad, bar_y),
                  fill=(230, 232, 229, va), width=max(3, int(S["h"] * 0.006)))
        prog = _clamp01((p - 0.62) / 0.30)
        if prog > 0:
            px1 = (x0 + bar_pad) + int((cw - bar_pad * 2) * prog)
            draw.line((x0 + bar_pad, bar_y, px1, bar_y),
                      fill=(*lime[:3], va), width=max(3, int(S["h"] * 0.006)))
    # "No recording." / "No editing."
    f_no = _font(config.MGFX_FONT_BOLD, max(20, int(S["h"] * 0.040)))
    lines = [("No recording.", 0.56), ("No editing.", 0.72)]
    for li, (txt, t0) in enumerate(lines):
        lp = _clamp01((p - t0) / 0.20)
        if lp <= 0:
            continue
        le = _ease_out_cubic(lp)
        la = int(255 * le)
        # "No" flashes green shortly after landing
        flash = (t0 + 0.06) < p < (t0 + 0.26)
        c_no = green if flash else ink
        w_no, _ = _text_bbox("No ", f_no)
        w_rest, _ = _text_bbox(txt[3:], f_no)
        total = w_no + w_rest
        sx = cx - total // 2
        sy = y1 + int(S["h"] * 0.045) + li * int(S["h"] * 0.062) + int((1 - le) * S["h"] * 0.015)
        draw.text((sx, sy), "No ", font=f_no, fill=(*c_no[:3], la))
        draw.text((sx + w_no, sy), txt[3:], font=f_no, fill=(*ink[:3], la))

def _draw_launch_cards(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 14.5-17.5s: PRODUCT VIDEO + WALKTHROUGH + UI MOCKUP fly in, merge.
    ink, lime, gray, border = style["fg"], style["accent"], style["muted"], style["card_border"]
    cx, cy = S["w"] // 2, S["h"] // 2 - int(S["h"] * 0.03)
    cw, ch = int(S["w"] * 0.235), int(S["h"] * 0.34)
    gap = int(S["w"] * 0.035)
    total_w = 3 * cw + 2 * gap
    start_x = cx - total_w // 2
    base_y = cy - ch // 2
    cards = [("PRODUCT VIDEO", "play"), ("WALKTHROUGH", "steps"), ("UI MOCKUP", "grid")]
    # entry origins: left / right / bottom
    origins = [(-cw - gap, 0), (S["w"] + gap, 0), (0, S["h"] * 0.5)]
    mp = _ease_in_out_cubic(_clamp01((p - 0.62) / 0.30))  # merge
    for idx, (label, kind) in enumerate(cards):
        ep = _clamp01((p - idx * 0.10) / 0.34)
        if ep <= 0:
            continue
        ee = _ease_out_back(ep) if ep < 0.9 else _ease_out_cubic(ep)
        fa = int(255 * _clamp01(ep / 0.25) * (1 - mp))
        if fa <= 0:
            continue
        home_x = start_x + idx * (cw + gap)
        ox, oy = origins[idx]
        x0 = int(_lerp(home_x + ox if idx < 2 else home_x, cx - cw // 2, mp) +
                 (1 - ee) * (ox if idx == 2 else 0) * 0)
        if idx == 0:
            x0 = int(_lerp(home_x - (S["w"] * 0.3) * (1 - ee), cx - cw // 2, mp))
        elif idx == 1:
            x0 = int(_lerp(home_x + (S["w"] * 0.3) * (1 - ee), cx - cw // 2, mp))
        else:
            x0 = home_x
        y0 = int(_lerp(base_y + (S["h"] * 0.5) * (1 - ee) if idx == 2 else base_y,
                       base_y, mp))
        x1, y1 = x0 + cw, y0 + ch
        _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.010), fa, S)
        _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.010),
                      fill=(255, 255, 255, fa),
                      outline=(*border[:3], int(200 * fa / 255)), width=max(1, S["stroke"] // 2))
        # lime tag
        f_tag = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.014)))
        tag = ("VIDEO", "GUIDE", "MOCKUP")[idx]
        tgw, _ = _text_bbox(tag, f_tag)
        tpad = int(S["w"] * 0.008)
        tgh = int(S["h"] * 0.026)
        tgx0 = (x0 + x1) // 2 - (tgw + tpad * 2) // 2
        tgy0 = y0 + int(S["h"] * 0.022)
        _rounded_rect(draw, (tgx0, tgy0, tgx0 + tgw + tpad * 2, tgy0 + tgh),
                      radius=tgh // 2, fill=(*lime[:3], fa), outline=None)
        draw.text((tgx0 + tpad, tgy0 + tgh // 2 - f_tag.getbbox(tag)[3] // 2), tag,
                  font=f_tag, fill=(17, 23, 20, fa))
        # mock visual
        if kind == "play":
            pr = int(S["h"] * 0.030)
            pcx, pcy = (x0 + x1) // 2, y0 + int(ch * 0.52)
            draw.ellipse((pcx - pr, pcy - pr, pcx + pr, pcy + pr),
                         fill=(17, 23, 20, fa))
            draw.polygon([(pcx - int(pr * 0.35), pcy - int(pr * 0.5)),
                          (pcx - int(pr * 0.35), pcy + int(pr * 0.5)),
                          (pcx + int(pr * 0.55), pcy)], fill=(255, 255, 255, fa))
        elif kind == "steps":
            for li in range(3):
                ly = y0 + int(ch * 0.42) + li * int(S["h"] * 0.030)
                lw = int(cw * (0.62 - li * 0.08))
                _rounded_rect(draw, ((x0 + x1) // 2 - lw // 2, ly,
                                     (x0 + x1) // 2 + lw // 2, ly + int(S["h"] * 0.012)),
                              radius=int(S["h"] * 0.005), fill=(225, 228, 224, fa), outline=None)
        else:
            gx, gy = x0 + int(cw * 0.22), y0 + int(ch * 0.40)
            cell = int(cw * 0.18)
            for r in range(2):
                for c in range(3):
                    _rounded_rect(draw, (gx + c * (cell + 6), gy + r * (cell // 2 + 6),
                                         gx + c * (cell + 6) + cell, gy + r * (cell // 2 + 6) + cell // 2),
                                  radius=4, fill=(225, 228, 224, fa) if (r, c) != (0, 0) else (*lime[:3], fa),
                                  outline=None)
        # label
        f_lb = _font(config.MGFX_FONT_BOLD, max(12, int(S["h"] * 0.019)))
        lw_, _ = _text_bbox(label, f_lb)
        if lw_ > cw - int(S["w"] * 0.02):
            f_lb = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.016)))
            lw_, _ = _text_bbox(label, f_lb)
        draw.text(((x0 + x1) // 2 - lw_ // 2, y1 - int(S["h"] * 0.045)), label,
                  font=f_lb, fill=(*ink[:3], fa))
    # "+" separators
    if 0.42 < p < 0.66:
        pa = int(255 * _clamp01((p - 0.42) / 0.1) * (1 - _clamp01((p - 0.58) / 0.08)))
        f_p = _font(config.MGFX_FONT_BOLD, max(16, int(S["h"] * 0.026)))
        for i in range(2):
            px = start_x + (i + 1) * cw + i * gap + gap // 2
            tw, _ = _text_bbox("+", f_p)
            draw.text((px - tw // 2, base_y + ch // 2 - f_p.getbbox("+")[3] // 2), "+",
                      font=f_p, fill=(*gray[:3], pa))
    # merged large frame
    if mp > 0:
        ma = int(255 * mp)
        mw, mh = int(S["w"] * 0.58), int(S["h"] * 0.44)
        mx0, my0 = cx - mw // 2, cy - mh // 2
        mx1, my1 = mx0 + mw, my0 + mh
        _launch_shadow(draw, (mx0, my0, mx1, my1), int(S["h"] * 0.014), ma, S, dy_frac=0.012)
        _rounded_rect(draw, (mx0, my0, mx1, my1), radius=int(S["h"] * 0.014),
                      fill=(255, 255, 255, ma),
                      outline=(*border[:3], int(220 * ma / 255)), width=max(1, S["stroke"] // 2))
        # fake timeline rows inside
        for i in range(3):
            ly = my0 + int(S["h"] * 0.05) + i * int(S["h"] * 0.045)
            lw = int(mw * (0.7 - i * 0.1))
            _rounded_rect(draw, (mx0 + int(S["w"] * 0.03), ly,
                                 mx0 + int(S["w"] * 0.03) + lw, ly + int(S["h"] * 0.018)),
                          radius=int(S["h"] * 0.007), fill=(238, 240, 237, ma), outline=None)
        pr = int(S["h"] * 0.034)
        draw.ellipse((cx - pr, cy - pr + int(S["h"] * 0.03), cx + pr, cy + pr + int(S["h"] * 0.03)),
                     fill=(17, 23, 20, ma))
        pcy = cy + int(S["h"] * 0.03)
        draw.polygon([(cx - int(pr * 0.35), pcy - int(pr * 0.5)),
                      (cx - int(pr * 0.35), pcy + int(pr * 0.5)),
                      (cx + int(pr * 0.55), pcy)], fill=(255, 255, 255, ma))
        bar_y = my1 - int(S["h"] * 0.032)
        draw.line((mx0 + int(S["w"] * 0.03), bar_y, mx1 - int(S["w"] * 0.03), bar_y),
                  fill=(*lime[:3], ma), width=max(3, int(S["h"] * 0.007)))

def _draw_launch_lockup(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 17.5-20.5s: "clep" + "Turn your product into video." + pulsing lime dot
    ink, lime, green = style["fg"], style["accent"], style["accent2"]
    cx, cy = S["w"] // 2, S["h"] // 2 - int(S["h"] * 0.02)
    logo = "clep"
    f_logo = _font(config.MGFX_FONT_BLACK, max(60, int(S["h"] * 0.125)))
    widths = [_text_bbox(ch, f_logo)[0] for ch in logo]
    space = int(S["w"] * 0.002)
    total = sum(widths) + space * (len(logo) - 1)
    x = cx - total // 2
    ly = cy - int(S["h"] * 0.13)
    for i, ch in enumerate(logo):
        cp = _clamp01((p - i * 0.06) / 0.32)
        if cp <= 0:
            x += widths[i] + space
            continue
        ce = _ease_out_back(cp) if cp < 0.9 else _ease_out_cubic(cp)
        ca = int(255 * _clamp01(cp / 0.3))
        draw.text((x, ly + int((1 - ce) * S["h"] * 0.03)), ch, font=f_logo,
                  fill=(*ink[:3], ca))
        x += widths[i] + space
    if p > 0.34:
        sp = _clamp01((p - 0.34) / 0.5)
        f_t = _font(config.MGFX_FONT_REGULAR, max(18, int(S["h"] * 0.036)))
        f_ti = _font(config.MGFX_FONT_SERIF_ITALIC, max(18, int(S["h"] * 0.036)))
        segs = [("Turn", ink, f_t), ("your", ink, f_t), ("product", green, f_ti),
                ("into", ink, f_t), ("video.", ink, f_t)]
        tw = _launch_rich_line(draw, segs, cx - int(S["h"] * 0.012),
                               cy + int(S["h"] * 0.045), sp, S,
                               stagger=0.07, dur=0.30)
        # pulsing lime dot beside the line
        pulse = 1.0 + 0.22 * math.sin(p * 9)
        dr = int(max(4, S["h"] * 0.009 * pulse))
        dot_x = cx + tw // 2 + int(S["w"] * 0.012)
        dot_y = int(cy + S["h"] * 0.045 + S["h"] * 0.018)
        halo = int(40 * (0.5 + 0.5 * math.sin(p * 9)))
        draw.ellipse((dot_x - dr * 2, dot_y - dr * 2, dot_x + dr * 2, dot_y + dr * 2),
                     fill=(*lime[:3], halo))
        draw.ellipse((dot_x - dr, dot_y - dr, dot_x + dr, dot_y + dr),
                     fill=(*lime[:3], int(255 * _clamp01(sp / 0.3))))

def _draw_launch_cta(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 20.5-22s: "clep.dev" small on off-white
    ink, lime, gray = style["fg"], style["accent"], style["muted"]
    cx, cy = S["w"] // 2, S["h"] // 2
    e = _ease_out_cubic(_clamp01(p / 0.45))
    if e <= 0:
        return
    a = int(255 * e)
    dr = int(S["h"] * 0.007 * (1.0 + 0.18 * math.sin(p * 8)))
    draw.ellipse((cx - dr, cy - int(S["h"] * 0.055) - dr, cx + dr, cy - int(S["h"] * 0.055) + dr),
                 fill=(*lime[:3], a))
    f_c = _font(config.MGFX_FONT_REGULAR, max(16, int(S["h"] * 0.034)))
    t = "clep.dev"
    tw, _ = _text_bbox(t, f_c)
    draw.text((cx - tw // 2, cy - int(S["h"] * 0.015) + int((1 - e) * S["h"] * 0.015)),
              t, font=f_c, fill=(*ink[:3], a))


# ── Clep narrative cut (20s silent brief) ─────────────────────────────────
# Beats: problem -> tag -> drive -> ship -> endcard. Narrative captions sit
# at the bottom; the scene plays above them. Same launch palette.

_LAUNCH_RED = (225, 60, 50)

def _launch_caption(draw: ImageDraw.ImageDraw, lines: list, cx: int, y_top: int,
                    p: float, S: dict, t0: float = 0.06, fs_frac: float = 0.027):
    f_b = _font(config.MGFX_FONT_BOLD, max(14, int(S["h"] * fs_frac)))
    f_r = _font(config.MGFX_FONT_REGULAR, max(14, int(S["h"] * fs_frac)))
    lh = int(S["h"] * (fs_frac + 0.014))
    for i, txt in enumerate(lines):
        lp = _clamp01((p - t0 - i * 0.14) / 0.24)
        if lp <= 0:
            continue
        le = _ease_out_cubic(lp)
        la = int(255 * le)
        f = f_b if i == 0 else f_r
        col = (17, 23, 20) if i == 0 else (93, 102, 98)
        tw, _ = _text_bbox(txt, f)
        if tw > S["w"] * 0.88:
            # shrink to fit
            f2 = _font(config.MGFX_FONT_BOLD if i == 0 else config.MGFX_FONT_REGULAR,
                       max(12, int(S["h"] * (fs_frac - 0.004))))
            tw, _ = _text_bbox(txt, f2)
            f = f2
        draw.text((cx - tw // 2, y_top + i * lh + int((1 - le) * S["h"] * 0.012)),
                  txt, font=f, fill=(*col, la))

def _launch_redx(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, alpha: int):
    if alpha <= 0:
        return
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, alpha),
                 outline=(*_LAUNCH_RED, alpha), width=max(3, r // 4))
    o = int(r * 0.45)
    draw.line((cx - o, cy - o, cx + o, cy + o), fill=(*_LAUNCH_RED, alpha), width=max(3, r // 4))
    draw.line((cx - o, cy + o, cx + o, cy - o), fill=(*_LAUNCH_RED, alpha), width=max(3, r // 4))

def _draw_launch_problem(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 0-4s: fresh UI feature + editing timeline with red X's over tools.
    ink, lime, gray, border = style["fg"], style["accent"], style["muted"], style["card_border"]
    cx = S["w"] // 2
    cy = int(S["h"] * 0.36)
    # feature card
    e = _ease_out_cubic(_clamp01(p / 0.22))
    if e > 0:
        a = int(255 * e)
        cw, ch = int(S["w"] * 0.42), int(S["h"] * 0.26)
        x0, y0 = cx - cw // 2, cy - ch // 2 + int((1 - e) * S["h"] * 0.06)
        x1, y1 = x0 + cw, y0 + ch
        _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.012), a, S)
        _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.012),
                      fill=(255, 255, 255, a),
                      outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
        # mock hero: headline bars + lime button
        for i, wfrac in enumerate((0.55, 0.38)):
            lw = int(cw * wfrac)
            _rounded_rect(draw, (x0 + int(S["w"] * 0.03), y0 + int(S["h"] * 0.035) + i * int(S["h"] * 0.032),
                                 x0 + int(S["w"] * 0.03) + lw, y0 + int(S["h"] * 0.035) + i * int(S["h"] * 0.032) + int(S["h"] * 0.016)),
                          radius=int(S["h"] * 0.006), fill=(225, 228, 224, a), outline=None)
        bny0 = y0 + int(S["h"] * 0.13)
        bnh = int(S["h"] * 0.045)
        bnw = int(cw * 0.34)
        _rounded_rect(draw, (x0 + int(S["w"] * 0.03), bny0, x0 + int(S["w"] * 0.03) + bnw, bny0 + bnh),
                      radius=bnh // 2, fill=(*lime[:3], a), outline=None)
        f_bn = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.015)))
        bt = "New feature"
        btw, _ = _text_bbox(bt, f_bn)
        draw.text((x0 + int(S["w"] * 0.03) + (bnw - btw) // 2, bny0 + bnh // 2 - f_bn.getbbox(bt)[3] // 2),
                  bt, font=f_bn, fill=(17, 23, 20, a))
        f_ok = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.014)))
        ok = "shipped"
        otw, _ = _text_bbox(ok, f_ok)
        ox = x1 - otw - int(S["w"] * 0.025)
        draw.text((ox, y0 + int(S["h"] * 0.030)), ok,
                  font=f_ok, fill=(*style["accent2"][:3], a))
        odr = int(S["h"] * 0.006)
        ocx, ocy = ox - int(S["w"] * 0.009), y0 + int(S["h"] * 0.030) + int(S["h"] * 0.009)
        draw.ellipse((ocx - odr, ocy - odr, ocx + odr, ocy + odr), fill=(*lime[:3], a))
    # timeline bar
    tp = _clamp01((p - 0.22) / 0.25)
    if tp > 0:
        te = _ease_out_cubic(tp)
        ta = int(255 * te)
        tw, th = int(S["w"] * 0.56), int(S["h"] * 0.085)
        tx0, ty0 = cx - tw // 2, cy + int(S["h"] * 0.20) + int((1 - te) * S["h"] * 0.03)
        tx1, ty1 = tx0 + tw, ty0 + th
        _rounded_rect(draw, (tx0, ty0, tx1, ty1), radius=int(S["h"] * 0.010),
                      fill=(17, 23, 20, ta), outline=None)
        # clips
        for i, (c0, c1) in enumerate(((0.04, 0.30), (0.33, 0.62), (0.65, 0.90))):
            _rounded_rect(draw, (tx0 + int(tw * c0), ty0 + int(S["h"] * 0.018),
                                 tx0 + int(tw * c1), ty1 - int(S["h"] * 0.018)),
                          radius=int(S["h"] * 0.006),
                          fill=(52, 58, 55, ta) if i != 1 else (77, 138, 24, ta), outline=None)
        # playhead
        px = tx0 + int(tw * (0.10 + 0.35 * _clamp01((p - 0.3) / 0.6)))
        draw.line((px, ty0 + 4, px, ty1 - 4), fill=(255, 255, 255, ta), width=3)
    # tool chips with red X's
    tools = ["CROP", "ZOOM", "KEYFRAME"]
    chip_y = cy + int(S["h"] * 0.325)
    for i, tool in enumerate(tools):
        xp = _clamp01((p - 0.42 - i * 0.09) / 0.22)
        if xp <= 0:
            continue
        xe = _ease_out_back(xp) if xp < 0.9 else _ease_out_cubic(xp)
        xa = int(255 * _clamp01(xp / 0.3))
        f_t = _font(config.MGFX_FONT_BOLD, max(11, int(S["h"] * 0.016)))
        tww, _ = _text_bbox(tool, f_t)
        pad = int(S["w"] * 0.012)
        chw = tww + pad * 2 + int(S["h"] * 0.030)
        chh = int(S["h"] * 0.042)
        total = len(tools) * chw + 2 * int(S["w"] * 0.018)
        sx = cx - total // 2 + i * (chw + int(S["w"] * 0.018))
        sy = chip_y + int((1 - xe) * S["h"] * 0.02)
        _rounded_rect(draw, (sx, sy, sx + chw, sy + chh), radius=chh // 2,
                      fill=(255, 255, 255, xa),
                      outline=(217, 220, 216, int(220 * xa / 255)), width=max(1, S["stroke"] // 2))
        draw.text((sx + pad, sy + chh // 2 - f_t.getbbox(tool)[3] // 2), tool,
                  font=f_t, fill=(93, 102, 98, xa))
        _launch_redx(draw, int(sx + chw - pad - S["h"] * 0.011), int(sy + chh // 2),
                     int(S["h"] * 0.011), int(xa * _clamp01((xp - 0.35) / 0.3)))
    _launch_caption(draw, ["You built the feature.",
                           "Don't waste time editing a video."],
                    cx, int(S["h"] * 0.815), p, S, t0=0.04)

def _draw_launch_tag(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 4-8s: tag component (lime highlight) + terminal /clep:clep.
    ink, lime, green, gray, border = (style["fg"], style["accent"], style["accent2"],
                                      style["muted"], style["card_border"])
    cx = S["w"] // 2
    cy = int(S["h"] * 0.40)
    # code card
    e = _ease_out_cubic(_clamp01(p / 0.22))
    if e > 0:
        a = int(255 * e)
        cw, ch = int(S["w"] * 0.46), int(S["h"] * 0.27)
        x0, y0 = cx - cw // 2, cy - ch // 2 - int(S["h"] * 0.06) + int((1 - e) * S["h"] * 0.06)
        x1, y1 = x0 + cw, y0 + ch
        _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.012), a, S)
        _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.012),
                      fill=(255, 255, 255, a),
                      outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
        f_c = _font(config.MGFX_FONT_REGULAR, max(13, int(S["h"] * 0.021)))
        f_cb = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.021)))
        tx = x0 + int(S["w"] * 0.028)
        ty = y0 + int(S["h"] * 0.035)
        lh = int(S["h"] * 0.042)
        draw.text((tx, ty), "<button", font=f_c, fill=(*gray[:3], a))
        attr = 'data-clep="ai-research"'
        aw, ah = _text_bbox(attr, f_cb)
        hp = _ease_out_cubic(_clamp01((p - 0.30) / 0.22))
        if hp > 0:
            pad = int(S["h"] * 0.005)
            _rounded_rect(draw, (tx + int(S["w"] * 0.012) - pad, ty + lh - pad // 2,
                                 tx + int(S["w"] * 0.012) + int((aw + pad * 2) * hp), ty + lh + ah + pad),
                          radius=int(S["h"] * 0.005), fill=(*lime[:3], a), outline=None)
        if p > 0.14:
            la = int(a * _clamp01((p - 0.14) / 0.15))
            draw.text((tx + int(S["w"] * 0.012), ty + lh), attr, font=f_cb, fill=(17, 23, 20, la))
        if p > 0.30:
            la2 = int(a * _clamp01((p - 0.30) / 0.15))
            draw.text((tx, ty + lh * 2), "onClick={runResearch}>", font=f_c, fill=(*gray[:3], la2))
        if p > 0.40:
            la3 = int(a * _clamp01((p - 0.40) / 0.15))
            draw.text((tx, ty + lh * 3), "  Research", font=f_c, fill=(*ink[:3], la3))
            draw.text((tx, ty + lh * 4), "</button>", font=f_c, fill=(*gray[:3], la3))
    # terminal card
    tp = _clamp01((p - 0.38) / 0.25)
    if tp > 0:
        te = _ease_out_cubic(tp)
        ta = int(255 * te)
        tw, th = int(S["w"] * 0.46), int(S["h"] * 0.105)
        tx0, ty0 = cx - tw // 2, cy + int(S["h"] * 0.135) + int((1 - te) * S["h"] * 0.05)
        tx1, ty1 = tx0 + tw, ty0 + th
        _launch_shadow(draw, (tx0, ty0, tx1, ty1), int(S["h"] * 0.010), ta, S)
        _rounded_rect(draw, (tx0, ty0, tx1, ty1), radius=int(S["h"] * 0.010),
                      fill=(17, 23, 20, ta), outline=None)
        dot_r = int(S["h"] * 0.005)
        for i in range(3):
            dx = tx0 + int(S["w"] * 0.014) + i * (dot_r * 2 + int(S["w"] * 0.004))
            draw.ellipse((dx - dot_r, ty0 + int(S["h"] * 0.020) - dot_r,
                          dx + dot_r, ty0 + int(S["h"] * 0.020) + dot_r),
                         fill=(70, 76, 73, ta))
        f_m = _font(config.MGFX_FONT_REGULAR, max(13, int(S["h"] * 0.021)))
        cmd = "/clep:clep"
        nch = int(len(cmd) * _ease_out_cubic(_clamp01((p - 0.45) / 0.25)) + 0.5)
        vis = cmd[:nch]
        qx = tx0 + int(S["w"] * 0.022)
        qy = ty0 + int(S["h"] * 0.042)
        draw.text((qx, qy), "$ ", font=f_m, fill=(93, 102, 98, ta))
        pw, _ = _text_bbox("$ ", f_m)
        draw.text((qx + pw, qy), vis, font=f_m, fill=(184, 255, 25, ta))
        if nch < len(cmd) and (int(p * 10) % 10) < 6:
            vw, _ = _text_bbox(vis, f_m)
            _, vh = _text_bbox("Ag", f_m)
            draw.line((qx + pw + vw + 4, qy, qx + pw + vw + 4, qy + vh),
                      fill=(184, 255, 25, ta), width=3)
        if p > 0.72:
            ra = int(ta * _clamp01((p - 0.72) / 0.15))
            f_r2 = _font(config.MGFX_FONT_BOLD, max(11, int(S["h"] * 0.015)))
            rt = "▸ run"
            rtw, _ = _text_bbox(rt, f_r2)
            rpad = int(S["w"] * 0.010)
            rh2 = int(S["h"] * 0.032)
            rx1 = tx1 - int(S["w"] * 0.018)
            rx0 = rx1 - rtw - rpad * 2
            ry0 = (ty0 + ty1) // 2 - rh2 // 2 + int(S["h"] * 0.008)
            _rounded_rect(draw, (rx0, ry0, rx1, ry0 + rh2), radius=rh2 // 2,
                          fill=(184, 255, 25, ra), outline=None)
            draw.text((rx0 + rpad, ry0 + rh2 // 2 - f_r2.getbbox(rt)[3] // 2), rt,
                      font=f_r2, fill=(17, 23, 20, ra))
    _launch_caption(draw, ["Just tag your code.",
                           "One command does the rest."],
                    cx, int(S["h"] * 0.815), p, S, t0=0.10)

def _draw_launch_drive(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 8-13s: browser click (A) -> camera zoom + loading -> results (B).
    ink, lime, green, gray, border = (style["fg"], style["accent"], style["accent2"],
                                      style["muted"], style["card_border"])
    cx = S["w"] // 2
    cy = int(S["h"] * 0.38)
    # stage A: browser mini with cursor click
    a_out = 1 - _clamp01((p - 0.36) / 0.10)
    if a_out > 0 and p < 0.48:
        ap = _clamp01(p / 0.40)
        e = _ease_out_cubic(_clamp01(ap / 0.30))
        a = int(255 * e * a_out)
        cw, ch = int(S["w"] * 0.40), int(S["h"] * 0.42)
        x0, y0 = cx - cw // 2, cy - ch // 2
        x1, y1 = x0 + cw, y0 + ch
        _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.012), a, S)
        _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.012),
                      fill=(255, 255, 255, a),
                      outline=(*border[:3], int(200 * a / 255)), width=max(1, S["stroke"] // 2))
        f_t = _font(config.MGFX_FONT_BOLD, max(18, int(S["h"] * 0.034)))
        t = "AI Research"
        tww, _ = _text_bbox(t, f_t)
        draw.text((cx - tww // 2, y0 + int(S["h"] * 0.035)), t, font=f_t, fill=(*ink[:3], a))
        bw, bh = int(cw * 0.52), int(S["h"] * 0.058)
        bx0, by0 = cx - bw // 2, y0 + ch - int(S["h"] * 0.10)
        punch = 0.0
        if ap > 0.72:
            punch = 0.10 * math.exp(-(ap - 0.72) * 14) * math.cos((ap - 0.72) * 28)
        bw2, bh2 = int(bw * (1 + punch)), int(bh * (1 + punch))
        bx0, by0 = cx - bw2 // 2, (by0 + by0 + bh) // 2 - bh2 // 2
        _rounded_rect(draw, (bx0, by0, bx0 + bw2, by0 + bh2), radius=bh2 // 2,
                      fill=(*lime[:3], a), outline=None)
        f_b = _font(config.MGFX_FONT_BOLD, max(12, int(S["h"] * 0.019)))
        bt = "Research"
        btw, _ = _text_bbox(bt, f_b)
        draw.text((cx - btw // 2, by0 + bh2 // 2 - f_b.getbbox(bt)[3] // 2), bt,
                  font=f_b, fill=(17, 23, 20, a))
        btn_cx, btn_cy = cx, by0 + bh2 // 2
        if 0.30 <= ap <= 0.74:
            cp = _ease_in_out_cubic(_clamp01((ap - 0.30) / 0.42))
            _launch_cursor(draw, int(_lerp(S["w"] * 0.74, btn_cx + bw * 0.10, cp)),
                           int(_lerp(S["h"] * 0.72, btn_cy, cp)), int(S["h"] * 0.026), a)
        elif ap > 0.74:
            _launch_cursor(draw, int(btn_cx + bw * 0.10), int(btn_cy), int(S["h"] * 0.026), a)
        if ap > 0.72:
            rp = _clamp01((ap - 0.72) / 0.28)
            _launch_ripple(draw, btn_cx, btn_cy, int(S["h"] * (0.02 + 0.07 * _ease_out_cubic(rp))),
                           int(200 * (1 - rp) * a / 255), S)
    # stage B: camera frame zoom + loading -> results
    bp = _clamp01((p - 0.36) / 0.64)
    if bp > 0:
        be = _ease_out_cubic(_clamp01(bp / 0.18))
        ba = int(255 * be)
        fw, fh = int(S["w"] * 0.60), int(S["h"] * 0.46)
        fx0, fy0 = cx - fw // 2, cy - fh // 2
        fx1, fy1 = fx0 + fw, fy0 + fh
        draw.rounded_rectangle((fx0, fy0, fx1, fy1), radius=int(S["h"] * 0.010),
                               fill=None, outline=(*ink[:3], int(220 * ba / 255)),
                               width=max(2, S["stroke"] // 2))
        tick = int(S["h"] * 0.028)
        cww = max(3, S["stroke"])
        for (tx, ty, dx, dy) in ((fx0, fy0, 1, 1), (fx1, fy0, -1, 1),
                                 (fx0, fy1, 1, -1), (fx1, fy1, -1, -1)):
            draw.line((tx, ty, tx + dx * tick, ty), fill=(*lime[:3], ba), width=cww)
            draw.line((tx, ty, tx, ty + dy * tick), fill=(*lime[:3], ba), width=cww)
        zoom = 1.0 + 0.35 * _ease_in_out_cubic(_clamp01((bp - 0.10) / 0.70))
        f_b2 = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.020 * zoom)))
        if bp < 0.55:
            sa = int(ba * (1 - _clamp01((bp - 0.48) / 0.07)))
            bw, bh = min(int(S["w"] * 0.28 * zoom), int(fw * 0.84)), int(S["h"] * 0.075 * zoom)
            bx0, by0 = cx - bw // 2, cy - bh // 2
            _rounded_rect(draw, (bx0, by0, bx0 + bw, by0 + bh), radius=bh // 2,
                          fill=(255, 255, 255, sa),
                          outline=(*ink[:3], int(160 * sa / 255)), width=max(2, S["stroke"] // 2))
            sp_cx, sp_cy = bx0 + int(bh * 0.7), cy
            sp_r = int(bh * 0.20)
            for i in range(10):
                ang = math.radians(i * 36 + p * 540)
                ia = int(sa * (0.25 + 0.75 * i / 9))
                draw.ellipse((sp_cx + int(sp_r * math.cos(ang)) - 3, sp_cy + int(sp_r * math.sin(ang)) - 3,
                              sp_cx + int(sp_r * math.cos(ang)) + 3, sp_cy + int(sp_r * math.sin(ang)) + 3),
                             fill=(*green[:3], ia))
            t = "LOADING"
            draw.text((sp_cx + int(bh * 0.55), sp_cy - f_b2.getbbox(t)[3] // 2), t,
                      font=f_b2, fill=(*ink[:3], sa))
        else:
            sa = int(ba * _clamp01((bp - 0.55) / 0.10))
            rw, rh = min(int(S["w"] * 0.36), int(fw * 0.86)), int(S["h"] * 0.26)
            rx0, ry0 = cx - rw // 2, cy - rh // 2
            _rounded_rect(draw, (rx0, ry0, rx0 + rw, ry0 + rh), radius=int(S["h"] * 0.010),
                          fill=(255, 255, 255, sa),
                          outline=(217, 220, 216, int(220 * sa / 255)), width=max(1, S["stroke"] // 2))
            cr = int(S["h"] * 0.014)
            ccx, ccy = rx0 + int(S["w"] * 0.022) + cr, ry0 + int(S["h"] * 0.034)
            draw.ellipse((ccx - cr, ccy - cr, ccx + cr, ccy + cr), fill=(*lime[:3], sa))
            f_r = _font(config.MGFX_FONT_BOLD, max(12, int(S["h"] * 0.018)))
            draw.text((ccx + cr + int(S["w"] * 0.009), ccy - f_r.getbbox("Ag")[3] // 2),
                      "RESULTS", font=f_r, fill=(*ink[:3], sa))
            for i in range(3):
                lp2 = _clamp01((bp - 0.62 - i * 0.07) / 0.16)
                if lp2 <= 0:
                    continue
                la = int(sa * _ease_out_cubic(lp2))
                ly = ry0 + int(S["h"] * 0.075) + i * int(S["h"] * 0.042)
                _rounded_rect(draw, (rx0 + int(S["w"] * 0.022), ly,
                                     rx0 + int(S["w"] * 0.022) + int(rw * (0.75 - i * 0.08)), ly + int(S["h"] * 0.018)),
                              radius=int(S["h"] * 0.007), fill=(238, 240, 237, la), outline=None)
        # kinetic tags
        f_l = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.015)))
        for txt, lx, ly, t0 in (("AUTO-ZOOM", fx0 + int(S["w"] * 0.008), fy0 - int(S["h"] * 0.042), 0.44),
                                ("60FPS", fx1 + int(S["w"] * 0.010), cy - int(S["h"] * 0.015), 0.60)):
            lp3 = _ease_out_cubic(_clamp01((p - t0) / 0.20))
            if lp3 <= 0:
                continue
            la3 = int(255 * lp3 * ba / 255)
            dr = int(S["h"] * 0.005)
            draw.ellipse((lx - dr, ly - dr, lx + dr, ly + dr), fill=(*lime[:3], la3))
            draw.text((lx + dr + int(S["w"] * 0.004), ly - f_l.getbbox(txt)[3] // 2), txt,
                      font=f_l, fill=(*gray[:3], la3))
    _launch_caption(draw, ["Clep drives your feature.",
                           "Auto-zooms, follows cursors, and exports in 60fps."],
                    cx, int(S["h"] * 0.815), p, S, t0=0.42, fs_frac=0.024)

def _launch_player_thumb(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
                         prog: float, alpha: int, S: dict, badge: str | None = None):
    """Finished-video player: dark frame, play button, lime progress."""
    _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.008),
                  fill=(17, 23, 20, alpha), outline=(184, 255, 25, int(200 * alpha / 255)),
                  width=max(2, S["stroke"] // 2))
    pr = max(10, int((y1 - y0) * 0.16))
    pcx, pcy = (x0 + x1) // 2, (y0 + y1) // 2 - int(S["h"] * 0.006)
    draw.ellipse((pcx - pr, pcy - pr, pcx + pr, pcy + pr), fill=(255, 255, 255, alpha))
    draw.polygon([(pcx - int(pr * 0.3), pcy - int(pr * 0.45)),
                  (pcx - int(pr * 0.3), pcy + int(pr * 0.45)),
                  (pcx + int(pr * 0.5), pcy)], fill=(17, 23, 20, alpha))
    bar_y = y1 - int(S["h"] * 0.014)
    pad = int(S["w"] * 0.010)
    draw.line((x0 + pad, bar_y, x1 - pad, bar_y),
              fill=(70, 76, 73, alpha), width=max(2, int(S["h"] * 0.004)))
    if prog > 0:
        draw.line((x0 + pad, bar_y,
                   x0 + pad + int((x1 - x0 - pad * 2) * _clamp01(prog)), bar_y),
                  fill=(184, 255, 25, alpha), width=max(2, int(S["h"] * 0.004)))
    if badge:
        f_bg = _font(config.MGFX_FONT_BOLD, max(9, int(S["h"] * 0.012)))
        btw, _ = _text_bbox(badge, f_bg)
        bpad = int(S["w"] * 0.006)
        bh = int(S["h"] * 0.022)
        _rounded_rect(draw, (x0 + pad, y0 + int(S["h"] * 0.008),
                             x0 + pad + btw + bpad * 2, y0 + int(S["h"] * 0.008) + bh),
                      radius=bh // 2, fill=(184, 255, 25, alpha), outline=None)
        draw.text((x0 + pad + bpad, y0 + int(S["h"] * 0.008) + bh // 2 - f_bg.getbbox(badge)[3] // 2),
                  badge, font=f_bg, fill=(17, 23, 20, alpha))

def _draw_launch_ship(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 13-17s: finished player drops into an X post preview + landing hero.
    ink, lime, gray, border = style["fg"], style["accent"], style["muted"], style["card_border"]
    cx = S["w"] // 2
    cy = int(S["h"] * 0.38)
    # hero player drops from top, holds, then yields
    out = _clamp01((p - 0.40) / 0.16)
    if out < 1:
        da = int(255 * _clamp01(p / 0.12) * (1 - out))
        vw, vh = int(S["w"] * 0.34), int(S["h"] * 0.25)
        vx0 = cx - vw // 2
        vy0 = int(_lerp(-vh, cy - vh // 2, _ease_out_back(_clamp01(p / 0.28))) - out * S["h"] * 0.08)
        prog = _clamp01((p - 0.10) / 0.28)
        # draw player chrome manually (bigger badge + play)
        _rounded_rect(draw, (vx0, vy0, vx0 + vw, vy0 + vh), radius=int(S["h"] * 0.010),
                      fill=(17, 23, 20, da), outline=(*lime[:3], int(220 * da / 255)),
                      width=max(2, S["stroke"] // 2))
        pr = int(S["h"] * 0.030)
        pcx, pcy = cx, vy0 + vh // 2 - int(S["h"] * 0.008)
        draw.ellipse((pcx - pr, pcy - pr, pcx + pr, pcy + pr), fill=(255, 255, 255, da))
        draw.polygon([(pcx - int(pr * 0.35), pcy - int(pr * 0.5)),
                      (pcx - int(pr * 0.35), pcy + int(pr * 0.5)),
                      (pcx + int(pr * 0.55), pcy)], fill=(17, 23, 20, da))
        bar_y = vy0 + vh - int(S["h"] * 0.020)
        pad = int(S["w"] * 0.016)
        draw.line((vx0 + pad, bar_y, vx0 + vw - pad, bar_y),
                  fill=(70, 76, 73, da), width=max(3, int(S["h"] * 0.006)))
        if prog > 0:
            draw.line((vx0 + pad, bar_y, vx0 + pad + int((vw - pad * 2) * prog), bar_y),
                      fill=(*lime[:3], da), width=max(3, int(S["h"] * 0.006)))
        f_bg = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.014)))
        bg = "1080p · 60fps"
        btw, _ = _text_bbox(bg, f_bg)
        bpad = int(S["w"] * 0.008)
        bh = int(S["h"] * 0.028)
        _rounded_rect(draw, (vx0 + pad, vy0 + int(S["h"] * 0.012),
                             vx0 + pad + btw + bpad * 2, vy0 + int(S["h"] * 0.012) + bh),
                      radius=bh // 2, fill=(*lime[:3], da), outline=None)
        draw.text((vx0 + pad + bpad, vy0 + int(S["h"] * 0.012) + bh // 2 - f_bg.getbbox(bg)[3] // 2),
                  bg, font=f_bg, fill=(17, 23, 20, da))
    # destinations: X post (left) + landing hero (right)
    f_cap = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.014)))
    dests = [("X / LINKEDIN POST", -1, 0.0), ("LANDING PAGE HERO", 1, 0.08)]
    for label, side, delay in dests:
        ep = _clamp01((p - 0.42 - delay) / 0.28)
        if ep <= 0:
            continue
        ee = _ease_out_back(ep) if ep < 0.9 else _ease_out_cubic(ep)
        ea = int(255 * _clamp01(ep / 0.22))
        dw, dh = (int(S["w"] * 0.295), int(S["h"] * 0.47)) if side < 0 else (int(S["w"] * 0.33), int(S["h"] * 0.47))
        home = cx + side * int(S["w"] * 0.245) - dw // 2
        x0 = int(home + side * S["w"] * 0.22 * (1 - ee))
        y0 = cy - dh // 2 + int((1 - ee) * S["h"] * 0.03)
        x1, y1 = x0 + dw, y0 + dh
        _launch_shadow(draw, (x0, y0, x1, y1), int(S["h"] * 0.010), ea, S)
        _rounded_rect(draw, (x0, y0, x1, y1), radius=int(S["h"] * 0.010),
                      fill=(255, 255, 255, ea),
                      outline=(*border[:3], int(200 * ea / 255)), width=max(1, S["stroke"] // 2))
        # frame label
        ltw, _ = _text_bbox(label, f_cap)
        draw.text((x0 + (dw - ltw) // 2, y0 - int(S["h"] * 0.034)), label,
                  font=f_cap, fill=(*gray[:3], ea))
        if side < 0:
            # X post: avatar + name + body + player thumb
            ar = int(S["h"] * 0.017)
            acx, acy = x0 + int(S["w"] * 0.020) + ar, y0 + int(S["h"] * 0.036)
            draw.ellipse((acx - ar, acy - ar, acx + ar, acy + ar), fill=(17, 23, 20, ea))
            f_nm = _font(config.MGFX_FONT_BOLD, max(11, int(S["h"] * 0.016)))
            f_hd = _font(config.MGFX_FONT_REGULAR, max(10, int(S["h"] * 0.014)))
            draw.text((acx + ar + int(S["w"] * 0.008), acy - int(S["h"] * 0.018)), "Clep",
                      font=f_nm, fill=(*ink[:3], ea))
            draw.text((acx + ar + int(S["w"] * 0.008), acy + int(S["h"] * 0.002)), "@clep_dev · now",
                      font=f_hd, fill=(*gray[:3], ea))
            f_post = _font(config.MGFX_FONT_REGULAR, max(11, int(S["h"] * 0.016)))
            post = "AI Research is live — watch it work."
            ptw, _ = _text_bbox(post, f_post)
            if ptw > dw - int(S["w"] * 0.04):
                post = "AI Research is live."
                ptw, _ = _text_bbox(post, f_post)
            draw.text((x0 + int(S["w"] * 0.020), y0 + int(S["h"] * 0.075)), post,
                      font=f_post, fill=(*ink[:3], ea))
            thumb_y0 = y0 + int(S["h"] * 0.115)
        else:
            # landing hero: browser chrome + headline + CTA + player thumb
            bar_h = int(S["h"] * 0.038)
            _rounded_rect(draw, (x0, y0, x1, y0 + bar_h), radius=int(S["h"] * 0.010),
                          fill=(235, 235, 232, ea), outline=None)
            draw.rectangle((x0, y0 + bar_h // 2, x1, y0 + bar_h), fill=(235, 235, 232, ea))
            dot_r = int(S["h"] * 0.004)
            for i in range(3):
                dx = x0 + int(S["w"] * 0.012) + i * (dot_r * 2 + int(S["w"] * 0.003))
                draw.ellipse((dx - dot_r, y0 + bar_h // 2 - dot_r, dx + dot_r, y0 + bar_h // 2 + dot_r),
                             fill=(200, 204, 200, ea))
            f_url = _font(config.MGFX_FONT_REGULAR, max(9, int(S["h"] * 0.012)))
            u = "clep.dev"
            utw, _ = _text_bbox(u, f_url)
            draw.text((x0 + (dw - utw) // 2, y0 + bar_h // 2 - f_url.getbbox(u)[3] // 2), u,
                      font=f_url, fill=(*gray[:3], ea))
            f_hl = _font(config.MGFX_FONT_BOLD, max(13, int(S["h"] * 0.020)))
            hl = "Ship features, show video."
            hlw, _ = _text_bbox(hl, f_hl)
            if hlw > dw - int(S["w"] * 0.036):
                hl = "Show your feature."
            draw.text((x0 + int(S["w"] * 0.018), y0 + bar_h + int(S["h"] * 0.014)), hl,
                      font=f_hl, fill=(*ink[:3], ea))
            f_cta = _font(config.MGFX_FONT_BOLD, max(10, int(S["h"] * 0.013)))
            ct = "Watch demo"
            ctw, _ = _text_bbox(ct, f_cta)
            cpad = int(S["w"] * 0.008)
            chh = int(S["h"] * 0.028)
            _rounded_rect(draw, (x0 + int(S["w"] * 0.018), y0 + bar_h + int(S["h"] * 0.058),
                                 x0 + int(S["w"] * 0.018) + ctw + cpad * 2, y0 + bar_h + int(S["h"] * 0.058) + chh),
                          radius=chh // 2, fill=(*lime[:3], ea), outline=None)
            draw.text((x0 + int(S["w"] * 0.018) + cpad,
                       y0 + bar_h + int(S["h"] * 0.058) + chh // 2 - f_cta.getbbox(ct)[3] // 2), ct,
                      font=f_cta, fill=(17, 23, 20, ea))
            thumb_y0 = y0 + bar_h + int(S["h"] * 0.115)
        # player thumb drops into its slot with overshoot + lime landing ring
        vp = _clamp01((p - 0.60 - delay) / 0.22)
        if vp > 0:
            ve = _ease_out_back(vp) if vp < 0.9 else _ease_out_cubic(vp)
            va = int(ea * _clamp01(vp / 0.25))
            tw2 = int(dw * 0.88)
            th2 = y1 - thumb_y0 - int(S["h"] * 0.016)
            tx0 = x0 + (dw - tw2) // 2
            ty0 = int(thumb_y0 - S["h"] * 0.10 * (1 - ve))
            _launch_player_thumb(draw, tx0, ty0, tx0 + tw2, ty0 + th2,
                                 _ease_out_cubic(_clamp01((p - 0.72 - delay) / 0.25)), va, S,
                                 badge="0:05")
            if 0.55 < vp < 1.0:
                rp = _clamp01((vp - 0.55) / 0.30)
                _launch_ripple(draw, tx0 + tw2 // 2, ty0 + th2 // 2,
                               int(S["h"] * (0.02 + 0.06 * _ease_out_cubic(rp))),
                               int(160 * (1 - rp) * va / 255), S)
    _launch_caption(draw, ["Get a cinematic demo.",
                           "Ready to share everywhere."],
                    cx, int(S["h"] * 0.815), p, S, t0=0.44)

def _draw_launch_endcard(draw: ImageDraw.ImageDraw, spec: dict, p: float, style: dict, S: dict):
    # 17-20s: wordmark + headline + clep.dev / /clep:clep.
    ink, lime, green, gray = style["fg"], style["accent"], style["accent2"], style["muted"]
    cx, cy = S["w"] // 2, S["h"] // 2 - int(S["h"] * 0.02)
    e = _ease_out_cubic(_clamp01(p / 0.25))
    if e > 0:
        f_wm = _font(config.MGFX_FONT_BLACK, max(28, int(S["h"] * 0.055)))
        tww, _ = _text_bbox("clep", f_wm)
        draw.text((cx - tww // 2, cy - int(S["h"] * 0.16) + int((1 - e) * S["h"] * 0.02)),
                  "clep", font=f_wm, fill=(*ink[:3], int(255 * e)))
    if p > 0.22:
        sp = _clamp01((p - 0.22) / 0.45)
        f_t = _font(config.MGFX_FONT_BOLD, max(20, int(S["h"] * 0.042)))
        f_ti = _font(config.MGFX_FONT_SERIF_ITALIC, max(20, int(S["h"] * 0.042)))
        segs = [("Turn", ink, f_t), ("your", ink, f_t), ("code", green, f_ti),
                ("into", ink, f_t), ("product", ink, f_t), ("videos.", ink, f_t)]
        _launch_rich_line(draw, segs, cx, cy - int(S["h"] * 0.02), sp, S,
                          stagger=0.06, dur=0.28)
    if p > 0.55:
        cp = _clamp01((p - 0.55) / 0.30)
        ce = _ease_out_cubic(cp)
        ca = int(255 * ce)
        f_u = _font(config.MGFX_FONT_REGULAR, max(14, int(S["h"] * 0.022)))
        f_cmd = _font(config.MGFX_FONT_BOLD, max(14, int(S["h"] * 0.022)))
        u, c = "clep.dev", "/clep:clep"
        uw, _ = _text_bbox(u, f_u)
        cw_, _ = _text_bbox(c, f_cmd)
        dot_w = int(S["w"] * 0.020)
        pad = int(S["w"] * 0.012)
        pill_w = cw_ + pad * 2
        total = uw + dot_w + pill_w
        sx = cx - total // 2
        sy = cy + int(S["h"] * 0.075) + int((1 - ce) * S["h"] * 0.015)
        draw.text((sx, sy), u, font=f_u, fill=(*gray[:3], ca))
        dr = int(S["h"] * 0.005)
        draw.ellipse((sx + uw + dot_w // 2 - dr, sy + int(S["h"] * 0.013) - dr,
                      sx + uw + dot_w // 2 + dr, sy + int(S["h"] * 0.013) + dr),
                     fill=(*lime[:3], ca))
        ph = int(S["h"] * 0.040)
        px0 = sx + uw + dot_w
        _rounded_rect(draw, (px0, sy - int(S["h"] * 0.008), px0 + pill_w, sy - int(S["h"] * 0.008) + ph),
                      radius=ph // 2, fill=(*lime[:3], ca), outline=None)
        draw.text((px0 + pad, sy - int(S["h"] * 0.008) + ph // 2 - f_cmd.getbbox(c)[3] // 2), c,
                  font=f_cmd, fill=(17, 23, 20, ca))


def _spec_for_beat(beat: dict) -> tuple[dict, str]:
    # Returns (spec_dict, style_name)
    # Priority: kinetic > clean > mgfx > diagram
    for key in ("kinetic", "clean", "mgfx"):
        if beat.get(key):
            s = beat[key]
            if isinstance(s, dict):
                return s, s.get("style", config.MGFX_DEFAULT_STYLE)
    if beat.get("diagram"):
        s = beat["diagram"]
        # map diagram style to clean variant if light/blue/gray requested
        sty = s.get("style", config.MGFX_DEFAULT_STYLE)
        # if legacy dark requested and config is clean, keep dark but render crisp
        return {"layout":"diagram", "diagram":s, "style":sty}, sty
    # fallback: headline from on_screen_text / narration
    fallback_text = beat.get("on_screen_text") or beat.get("narration_snippet") or ""
    return {"layout":"headline", "text": fallback_text, "style": config.MGFX_DEFAULT_STYLE}, config.MGFX_DEFAULT_STYLE

def render_mgfx_clip(beat: dict, canvas_w: int, canvas_h: int, duration: float,
                     out_dir: Path | None = None) -> Path:
    """
    Renders a fullscreen kinetic clip for a motion-only beat.
    Cached by hash of spec + size + duration.
    """
    spec, style_name = _spec_for_beat(beat)
    style = _style(style_name)
    # normalize layout field
    layout = spec.get("layout") or spec.get("type") or "headline"
    # for diagram-inside-kinetic, store inner diagram spec
    fps = config.FPS
    # hash key includes whole beat kinetic spec
    # Include quality in cache key so --hq / --4k busts cache correctly
    q_tag = f"|hq{getattr(config,'HQ_CRF','18')}4k{getattr(config,'FOUR_K',False)}ss{getattr(config,'MGFX_SUPERSAMPLE',2)}"
    key_src = json.dumps(spec, sort_keys=True) + f"|{canvas_w}x{canvas_h}|{duration:.3f}|{fps}|{style_name}|{layout}{q_tag}"
    clip_hash = hashlib.md5(key_src.encode()).hexdigest()[:10]
    out_dir = out_dir or (config.CACHE_DIR / f"beat_{beat['id']}")
    out_path = out_dir / f"mgfx_{clip_hash}.mp4"
    if out_path.exists():
        return out_path
    out_dir.mkdir(parents=True, exist_ok=True)

    ss = config.MGFX_SUPERSAMPLE
    W, H = canvas_w * ss, canvas_h * ss
    S = {
        "w": W, "h": H, "card_h": H, "card_w": W,
        "stroke": max(2, round(canvas_w * config.GFX_STROKE_W_FRAC)) * ss,
        "node_fontsize": max(12, int(canvas_h * config.GFX_NODE_FONTSIZE_FRAC)) * ss,
        "wobble": 0,  # clean has no wobble
    }

    # Prepare diagram elements if layout == diagram
    elements=None; nodes_by_id=None
    if layout == "diagram" and spec.get("diagram"):
        dspec = spec["diagram"]
        elements=[]; nodes_by_id={}
        for i, n in enumerate(dspec.get("nodes",[]) or []):
            nid=str(n.get("id") or f"n{i}")
            node={"kind":"node","id":nid,"label":str(n.get("label","")).strip().upper()[:28],
                  "x":_clamp01(float(n.get("x",0.5))),"y":_clamp01(float(n.get("y",0.4))),
                  "w":_clamp01(float(n.get("w",0.24))),"h":_clamp01(float(n.get("h",0.18))),
                  "shape": n.get("shape") if n.get("shape") in ("rect","oval","plain") else "rect",
                  "emph": bool(n.get("emph"))}
            nodes_by_id[nid]=node; elements.append(node)
        for a in dspec.get("arrows",[]) or []:
            if a.get("from") in nodes_by_id and a.get("to") in nodes_by_id and a.get("from")!=a.get("to"):
                elements.append({"kind":"arrow","from":a["from"],"to":a["to"],
                                 "label": (str(a["label"]).strip().upper()[:18] if a.get("label") else None),
                                 "path": a.get("path") if a.get("path") in ("straight","elbow") else "straight"})
        for c in dspec.get("callouts",[]) or []:
            if str(c.get("text","")).strip():
                elements.append({"kind":"callout","text":str(c["text"]).strip().upper()[:32],
                                 "x":_clamp01(float(c.get("x",0.5))),"y":_clamp01(float(c.get("y",0.85))),
                                 "big":bool(c.get("big")),"emph":bool(c.get("emph"))})
        # schedule — clean diagrams draw faster so mid-beat is fully formed
        draw_window = duration * (0.55 if style_name in ("light","gray","blue") else config.GFX_DRAW_FRACTION)
        total_cost = sum({"node":1.0,"arrow":0.85,"callout":0.55}[e["kind"]] for e in elements) or 1.0
        cursor=0.0
        for e in elements:
            slot = {"node":1.0,"arrow":0.85,"callout":0.55}[e["kind"]] / total_cost * draw_window
            e["t0"]=max(0.0, cursor - slot*0.25)
            e["t1"]=min(duration, e["t0"]+slot*1.45)
            cursor+=slot
            e["t1"]=min(e["t1"], draw_window*1.05)

    frames = max(2, round(duration * fps))
    bg = Image.new("RGB", (W, H), style["bg"][:3] if len(style["bg"])==3 else style["bg"])

    # Quality: HQ/4K uses lower crf (higher quality) but keep mgfx intermediates fast
    mgfx_crf = str(config.MGFX_CRF) if hasattr(config, "MGFX_CRF") else ("14" if getattr(config, "HQ", False) or getattr(config, "FOUR_K", False) else "17")
    mgfx_preset = "slow" if (getattr(config, "HQ", False) or getattr(config, "FOUR_K", False)) else "fast"
    enc = subprocess.Popen(
        [config.FFMPEG_BIN, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{canvas_w}x{canvas_h}",
         "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", mgfx_preset, "-crf", mgfx_crf,
         "-pix_fmt", "yuv420p", str(out_path)],
        stdin=subprocess.PIPE)

    for fi in range(frames):
        t = fi / fps
        p_global = _clamp01(t / max(duration*0.78, 0.4))  # entry progress

        frame = bg.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        # watermark ghost (paper vs dark) — composite faint logo before text (makes overlay slick per brief)
        try:
            # skip for talo_logo which already draws its own huge ghost (avoid double)
            # also skip for clep (has its own lime watermark handling)
            if spec.get("layout") in ("talo_logo","logo","taloLogo") or str(spec.get("layout","")).startswith("clep_") or str(spec.get("layout","")).startswith("launch_"):
                pass
            elif style_name in ("clep","clep_lime","clep_dark","clep_launch"):
                pass
            else:
                is_dark_bg = style.get("bg", (255,255,255))[0] < 30
                # only for editorial paper / talo styles, not for vivid blue
                if style.get("bg") not in [(1,12,202), (1, 12, 202)] and p_global < 0.92:
                    # use cached watermark image composited onto overlay
                    wm_img = _get_watermark_image(W, H, is_dark_bg)
                    if wm_img is not None:
                        # center with slight offset, already includes opacity/rotation
                        xw = (W - wm_img.size[0])//2
                        yw = (H - wm_img.size[1])//2 + int(H*0.03)
                        overlay.alpha_composite(wm_img, dest=(xw, yw))
        except Exception:
            pass

        # Dispatch layout
        if layout == "diagram" and elements is not None:
            for e in elements:
                p = _clamp01((t - e["t0"]) / max(e["t1"]-e["t0"], 1e-6))
                if p<=0: continue
                if e["kind"]=="node":
                    _draw_node_clean(draw, e, p, style, S)
                elif e["kind"]=="arrow":
                    _draw_arrow_clean(draw, e, nodes_by_id, p, style, S)
                else:
                    _draw_callout_clean(draw, e, p, style, S)
        elif layout in ("headline", "hero", "title", "text"):
            _draw_headline(draw, spec, _clamp01(t / (duration*0.85)), style, S)
        elif layout in ("search_typing", "search", "typing"):
            _draw_search_typing(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("badge_cards", "badge", "search_results", "cards_with_badge"):
            _draw_badge_cards(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("stat_blue", "stat", "big_stat"):
            _draw_stat_blue(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("grid_cards", "grid", "feature_grid"):
            _draw_grid_cards(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("bars", "bars_skeleton", "skeleton"):
            _draw_bars_skeleton(draw, spec, _clamp01(t / max(duration*0.8,0.5)), style, S)
        elif layout in ("checklist", "progress", "progress_checklist"):
            _draw_checklist(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("task_box", "task", "task_accepted"):
            _draw_task_box(draw, spec, _clamp01(t / max(duration*0.90,0.5)), style, S)
        elif layout in ("ticker_grid", "ticker"):
            # reuse grid_cards for ticker variant (grid + pill already handles ticker)
            _draw_grid_cards(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("talo_hero", "taloHero", "hero_talo"):
            _draw_talo_hero(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("talo_logo", "logo", "taloLogo"):
            _draw_talo_logo(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("chat_slick", "chat", "task_box_slick", "slick_chat"):
            _draw_chat_slick(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("browser_work", "browser", "work", "watch_work"):
            _draw_browser_work(draw, spec, _clamp01(t / max(duration*0.90,0.5)), style, S)
        elif layout in ("spreadsheet", "sheet", "spreadsheet_result", "result"):
            _draw_spreadsheet_result(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("chaos_cards", "chaos", "old_way", "three_cards"):
            _draw_chaos_cards(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("editorial_black", "problem", "black_hero", "editorial_dark"):
            _draw_editorial_black(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("editorial_statement", "category", "statement"):
            _draw_editorial_statement(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("clep_chaos", "clepChaos"):
            _draw_clep_chaos(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("clep_false", "clep_chat_wrong", "clepChatWrong"):
            _draw_clep_false(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("clep_enter", "clepEnter"):
            _draw_clep_enter(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("clep_dropzone", "clep_drop", "dropzone", "clepHero"):
            _draw_clep_dropzone(draw, spec, _clamp01(t / max(duration*0.90,0.5)), style, S)
        elif layout in ("clep_icons", "clepIcons", "clep_differentiators"):
            _draw_clep_icons(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("clep_trust", "clepTrust", "clep_flag"):
            _draw_clep_trust(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("clep_range", "clepRange"):
            _draw_clep_range(draw, spec, _clamp01(t / max(duration*0.88,0.5)), style, S)
        elif layout in ("clep_lockup", "clep_logo", "clepLockup"):
            _draw_clep_lockup(draw, spec, _clamp01(t / max(duration*0.85,0.5)), style, S)
        elif layout in ("launch_hook",):
            _draw_launch_hook(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_code",):
            _draw_launch_code(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_browser",):
            _draw_launch_browser(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_camera",):
            _draw_launch_camera(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_norecord",):
            _draw_launch_norecord(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_cards",):
            _draw_launch_cards(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_lockup",):
            _draw_launch_lockup(draw, spec, _clamp01(t / max(duration*0.90,0.5)), style, S)
        elif layout in ("launch_cta",):
            _draw_launch_cta(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        elif layout in ("launch_problem",):
            _draw_launch_problem(draw, spec, _clamp01(t / max(duration*0.94,0.5)), style, S)
        elif layout in ("launch_tag",):
            _draw_launch_tag(draw, spec, _clamp01(t / max(duration*0.94,0.5)), style, S)
        elif layout in ("launch_drive",):
            _draw_launch_drive(draw, spec, _clamp01(t / max(duration*0.96,0.5)), style, S)
        elif layout in ("launch_ship",):
            _draw_launch_ship(draw, spec, _clamp01(t / max(duration*0.94,0.5)), style, S)
        elif layout in ("launch_endcard",):
            _draw_launch_endcard(draw, spec, _clamp01(t / max(duration*0.92,0.5)), style, S)
        else:
            # unknown -> headline fallback
            _draw_headline(draw, spec, _clamp01(t / (duration*0.85)), style, S)

        # fade out at tail (last 0.22s)
        tail = duration - t
        if tail < 0.22 and frames>1:
            fade = _clamp01(tail/0.22)
            # apply fade by reducing overlay alpha via extra composite?
            # simple: blend overlay alpha
            # We'll adjust by adding a white fade layer if needed (cheap)
            pass

        frame = Image.alpha_composite(frame, overlay).convert("RGB")
        enc.stdin.write(frame.resize((canvas_w, canvas_h), Image.LANCZOS).tobytes())

    enc.stdin.close()
    enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"motion_gfx clean: ffmpeg failed for beat {beat['id']} ({out_path})")
    print(f"[motion_gfx:clean] beat {beat['id']} layout={layout} style={style_name} -> {out_path.name} ({frames}f)")
    return out_path

# ── legacy alias for compositor compat ───────────────────────────────────
def render_diagram_clip(beat: dict, card_w: int, card_h: int, duration: float, out_dir: Path | None = None) -> Path:
    """
    Compat shim: if beat has kinetic spec, it will be used to fill fullscreen;
    otherwise fall back to diagram (rendered at card size but will be upscaled to canvas).
    For motion-only pipeline the caller should use render_mgfx_clip with canvas size.
    """
    # Try kinetic first with provided dims as canvas
    if beat.get("kinetic") or beat.get("clean") or beat.get("mgfx"):
        return render_mgfx_clip(beat, card_w, card_h, duration, out_dir)
    # For plain diagram, we still want to use render_mgfx_clip so the style
    # mapping (light/blue) is honoured and we get fullscreen output.
    # But if caller expects card-size clip, pass through: render_mgfx_clip handles both.
    return render_mgfx_clip(beat, card_w, card_h, duration, out_dir)

# Convenience for image-sized fallback (used by compositor for image beats)
def render_headline_fallback(text: str, canvas_w: int, canvas_h: int, duration: float, style_name: str = "light", accent: str | None = None) -> Path:
    beat = {"id": 9999, "kinetic": {"layout":"headline","text":text,"accent":accent,"style":style_name}}
    return render_mgfx_clip(beat, canvas_w, canvas_h, duration)
