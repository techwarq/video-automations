"""
Builds a single ffmpeg filter_complex graph from beats.json and renders the
final 9:16 explainer video in one pass.

Pipeline this stage expects to run AFTER (each beat dict must already carry
these fields, added progressively by the earlier stages):
    start, end, narration_snippet, image_query, on_screen_text, text_style,
    motion, prepped_image_path (added by prep_images.py)
Optional per-beat fields (added by planner / hand-tuning):
    transition {"type","duration"} — how this beat hands off to the next
    word_timings [{"word","start","end"}] — exact caption alignment if a
        forced-alignment stage ever provides it

Design:
  - Each beat becomes its own short "card" clip: an eased Ken Burns zoompan
    of the prepped image, bordered, drop-shadowed, with its on_screen_text
    drawtext'd (fade+slide) directly onto the clip's own local timeline.
  - Beat clips are chained together with `xfade` into one continuous
    top-motion-graphics track; each cut uses the beat's own transition type
    and duration (fades, wipes, whip-blurs, hard cuts...) instead of one
    uniform crossfade.
  - That track is overlaid onto a canvas (dot-grid background texture) at
    the top zone; the talking-head video (cropped/looped/trimmed) is
    overlaid at the bottom zone.
  - Word-synced caption groups (from narration_snippets) are drawn over the
    full canvas so viewers read exactly what the speaker says.
  - A single finishing pass grades the whole composite (S-curve + split-tone
    or .cube LUT), adds halation bloom, film grain and vignette — one unified
    look across stills and talking head.
  - Narration audio runs through a mastering chain (HPF → compressor →
    de-esser → loudnorm -14 LUFS), optionally mixed against a sidechain-ducked
    music bed.
"""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config
import motion_gfx


def _is_diagram(beat: dict) -> bool:
    return beat.get("visual_kind") == "diagram" and bool(beat.get("diagram"))


def _is_video(beat: dict) -> bool:
    return beat.get("visual_kind") == "video_clip" and bool(beat.get("resolved_clip_path"))


def _prepare_video_clip(src: Path, card_w: int, card_h: int, duration: float) -> Path:
    """
    Normalizes a top-zone video beat's clip to exactly card size / pipeline
    fps / beat duration. Clips shorter than the beat get a forward+reverse
    ping-pong loop (same trick as the talking-head bottom zone); longer
    clips are trimmed from their start.
    """
    out_dir = config.CACHE_DIR / "video_beats"
    out_dir.mkdir(parents=True, exist_ok=True)
    key_src = f"{src}|{card_w}x{card_h}|{duration:.3f}|{src.stat().st_mtime}"
    out_path = out_dir / f"vid_{hashlib.md5(key_src.encode()).hexdigest()[:10]}_{src.stem}.mp4"
    if out_path.exists():
        return out_path

    fps = config.FPS
    fit = (f"scale={card_w}:{card_h}:force_original_aspect_ratio=increase,"
           f"crop={card_w}:{card_h},fps={fps},setsar=1")
    src_dur = _probe_duration(src)
    if src_dur >= duration + 0.1:
        graph = f"[0:v]{fit},trim=duration={duration:.3f},setpts=PTS-STARTPTS[v]"
    else:
        graph = (f"[0:v]{fit},split=2[vf][vrs];[vrs]reverse[vr];"
                 f"[vf][vr]concat=n=2:v=1:a=0,trim=duration={duration:.3f},setpts=PTS-STARTPTS[v]")
    cmd = [
        config.FFMPEG_BIN, "-y", "-v", "error", "-i", str(src),
        "-filter_complex", graph, "-map", "[v]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}", str(out_path),
    ]
    print(f"[compositor] normalizing video beat clip {src.name} -> {out_path.name}")
    subprocess.run(cmd, check=True)
    return out_path


# ── small utilities ─────────────────────────────────────────────────────

def _even(n: float) -> int:
    """libx264 + yuv420p require even width/height."""
    n = int(round(n))
    return n if n % 2 == 0 else n - 1


def _escape_drawtext(text: str) -> str:
    """
    Escape a string for safe use inside an ffmpeg drawtext filter arg.
    The filter is always built with `expansion=none` (see _build_text_filter)
    so `%` needs no special handling here — only backslash/colon/quote.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # avoid quote-escaping headaches entirely
    return text


def _text_width(text: str, font_path: str, fontsize: int) -> int:
    font = ImageFont.truetype(font_path, fontsize)
    # multiline text (already wrapped): width is the widest line
    return max(font.getbbox(line)[2] - font.getbbox(line)[0] for line in text.split("\n"))


def _fit_text(text: str, font_path: str, base_fontsize: int, max_width: int) -> tuple[str, int]:
    """
    ffmpeg's drawtext has no auto-fit/wrap — text wider than the frame simply
    overflows off-canvas on both sides and gets silently clipped (e.g. "TWO
    PREBURNERS" rendering as "WO PREBURNER"). Shrink fontsize until it fits;
    if it's still too wide at the minimum readable size, wrap onto two lines.
    """
    fontsize = base_fontsize
    while fontsize > config.TEXT_MIN_FONTSIZE:
        if _text_width(text, font_path, fontsize) <= max_width:
            return text, fontsize
        fontsize -= 2
    fontsize = config.TEXT_MIN_FONTSIZE

    words = text.split()
    if len(words) > 1 and _text_width(text, font_path, fontsize) > max_width:
        mid = len(words) // 2
        wrapped = f"{' '.join(words[:mid])}\n{' '.join(words[mid:])}"
        if _text_width(wrapped, font_path, fontsize) <= max_width:
            return wrapped, fontsize
        return wrapped, fontsize  # best effort — still shorter per-line than the single-line version
    return text, fontsize


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            config.FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _ensure_grid_background() -> Path:
    """
    Generates (once, cached) a subtle dot-grid texture the size of the full
    canvas — the "grid dot pattern" backdrop from the reference screenshots.
    Plain ffmpeg lavfi sources can't draw dots, so this is pre-rendered with
    Pillow and used as a static image input.
    """
    path = config.ASSETS_DIR / "grid_bg.png"
    if path.exists():
        return path

    w, h = config.CANVAS_WIDTH, config.CANVAS_HEIGHT
    bg_color = config.CANVAS_BG_COLOR_RGB
    dot_color = config.CANVAS_GRID_DOT_RGB
    spacing = config.CANVAS_GRID_SPACING_PX

    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)
    r = 1
    for y in range(spacing // 2, h, spacing):
        for x in range(spacing // 2, w, spacing):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=dot_color)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# ── per-beat Ken Burns (zoompan) ────────────────────────────────────────

def _ease_expr(p: str) -> str:
    """
    Cubic ease-in-out applied to a progress expression `p` (0..1).
    NOTE: commas inside any expression must be backslash-escaped — see the
    parser note in _build_text_filter.
    """
    return f"(if(lt({p}\\,0.5)\\,4*pow({p}\\,3)\\,1-pow(-2*{p}+2\\,3)/2))"


def _zoompan_filter(motion: str, duration_s: float, card_w: int, card_h: int,
                     focus: tuple[float, float] | None = None) -> str:
    fps = config.FPS
    frames = max(1, round(duration_s * fps))
    z_start, z_end = config.KEN_BURNS_ZOOM_START, config.KEN_BURNS_ZOOM_END
    pan = config.KEN_BURNS_PAN_FRACTION

    # zoompan needs an oversized source to zoom/pan into without pixelating
    # or hard-cutting at the source's native resolution.
    upscale_w, upscale_h = card_w * 4, card_h * 4
    fx, fy = focus if focus else (0.5, 0.5)  # aim at the vision-picked detail, or dead-center if none

    # Progress term: eased (NLE-style keyframe bezier feel) or linear.
    raw_p = f"(on/{frames})"
    p = _ease_expr(raw_p) if config.KEN_BURNS_EASED else raw_p

    # Zoom is computed absolutely from the frame counter each frame (never
    # referencing zoompan's persistent `zoom` variable), so both directions
    # are exact from frame 0 and there is no first-frame flash.
    if motion == "zoom_in":
        z_expr = f"{z_start}+({z_end - z_start:.6f})*{p}"
        x_expr = f"max(0,min(({fx}*iw)-(iw/zoom/2)\\,iw-iw/zoom))"
        y_expr = f"max(0,min(({fy}*ih)-(ih/zoom/2)\\,ih-ih/zoom))"
    elif motion == "zoom_out":
        z_expr = f"{z_end}-({z_end - z_start:.6f})*{p}"
        x_expr = f"max(0,min(({fx}*iw)-(iw/zoom/2)\\,iw-iw/zoom))"
        y_expr = f"max(0,min(({fy}*ih)-(ih/zoom/2)\\,ih-ih/zoom))"
    elif motion in ("pan_left", "pan_right"):
        z_expr = f"{z_end}"
        avail = upscale_w * (1 - 1 / z_end)  # total pannable width at z_end
        shift = avail * pan
        center = avail / 2
        x_from, x_to = center - shift / 2, center + shift / 2
        if motion == "pan_left":
            x_from, x_to = x_to, x_from
        x_expr = f"{x_from:.3f}+({x_to - x_from:.3f})*{p}"
        # z is constant (z_end) through a pan, so the vertical crop window
        # position can be precomputed rather than expressed dynamically.
        y_window = upscale_h / z_end
        y_avail = upscale_h - y_window
        y_target = max(0.0, min(fy * upscale_h - y_window / 2, y_avail))
        y_expr = f"{y_target:.3f}"
    else:
        raise ValueError(f"Unknown motion: {motion!r}")

    return (
        f"scale={upscale_w}:{upscale_h},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={card_w}x{card_h}:fps={fps}"
    )


# ── per-beat card (border + drop shadow) ────────────────────────────────

def _card_dims() -> tuple[int, int]:
    """Max image-card size that still fits in TOP_ZONE with margin, border, and shadow offset."""
    _, _, zone_w, zone_h = config.TOP_ZONE
    margin = config.TOP_ZONE_CARD_MARGIN
    border = config.CARD_BORDER_PX
    shadow = config.CARD_SHADOW_OFFSET_PX

    available_w = zone_w - 2 * margin - shadow
    available_h = zone_h - 2 * margin - shadow
    card_w = _even(available_w - 2 * border)
    card_h = _even(available_h - 2 * border)
    return card_w, card_h


def _build_card_filters(beat_idx: int, image_input_idx: int, duration: float) -> tuple[list[str], str, int, int]:
    """Returns (filter_lines, output_label, composite_w, composite_h)."""
    card_w, card_h = _card_dims()
    border, shadow_offset = config.CARD_BORDER_PX, config.CARD_SHADOW_OFFSET_PX
    bordered_w, bordered_h = card_w + 2 * border, card_h + 2 * border
    comp_w, comp_h = _even(bordered_w + shadow_offset), _even(bordered_h + shadow_offset)
    fps = config.FPS

    lines = []
    zp_label = f"zp{beat_idx}"
    lines.append(f"[{image_input_idx}:v]{{ZOOMPAN}}[{zp_label}]")  # filled in by caller (needs motion)

    card_label = f"card{beat_idx}"
    lines.append(
        f"[{zp_label}]pad={bordered_w}:{bordered_h}:{border}:{border}:color={config.CARD_BORDER_COLOR}[{card_label}]"
    )

    shadow_base = f"shbase{beat_idx}"
    lines.append(f"color=c={config.CARD_SHADOW_COLOR}:s={bordered_w}x{bordered_h}:d={duration:.3f}:r={fps}[{shadow_base}]")
    shadow_blurred = f"shblur{beat_idx}"
    lines.append(f"[{shadow_base}]boxblur={config.CARD_SHADOW_BLUR}:1[{shadow_blurred}]")

    stage_bg = f"stagebg{beat_idx}"
    lines.append(f"color=c={config.CANVAS_BG_COLOR}@0:s={comp_w}x{comp_h}:d={duration:.3f}:r={fps}[{stage_bg}]")
    stage1 = f"stage1_{beat_idx}"
    lines.append(f"[{stage_bg}][{shadow_blurred}]overlay={shadow_offset}:{shadow_offset}[{stage1}]")
    composite_label = f"cardcomp{beat_idx}"
    lines.append(f"[{stage1}][{card_label}]overlay=0:0[{composite_label}]")

    return lines, composite_label, comp_w, comp_h


def _build_text_filter(beat_idx: int, base_label: str, beat: dict, duration: float, comp_w: int, comp_h: int) -> tuple[list[str], str]:
    text = beat.get("on_screen_text")
    if not text:
        return [], base_label

    style = config.TEXT_STYLES[beat["text_style"]]
    fade = config.TEXT_FADE_DURATION
    slide = config.TEXT_SLIDE_PX

    pos = beat.get("text_position")
    is_bottom_left = not pos and style["default_position"] != "center"
    # bottom-left text starts at x=40, so it has less room to the right edge
    # than centered text does on both sides.
    max_text_width = comp_w - (40 + config.TEXT_SAFE_MARGIN_PX if is_bottom_left else 2 * config.TEXT_SAFE_MARGIN_PX)
    base_fontsize = style["fontsize"]
    if beat.get("role") == "hook":
        # Reels grammar: the hook text punches harder.
        base_fontsize = round(base_fontsize * config.HOOK_TEXT_SCALE)
    fitted_text, fitted_fontsize = _fit_text(text, style["font"], base_fontsize, max_text_width)
    escaped = _escape_drawtext(fitted_text)

    if pos:
        base_x = f"{pos['x']}*{comp_w}"
        base_y = f"{pos['y']}*{comp_h}"
    elif style["default_position"] == "center":
        base_x = "(w-text_w)/2"
        base_y = "(h-text_h)/2"
    else:  # bottom-left
        base_x = "40"
        base_y = f"{comp_h}-text_h-40"

    # Slight slide-in from below on entry; fade in/out over the whole window.
    # NOTE: a comma left unescaped inside an expression — even when the whole
    # expression is single-quoted — terminates the option early and corrupts
    # every option after it (ffmpeg's filtergraph parser treats `,` as a
    # chain separator above the quote-parsing layer). Every generated
    # expression must have its internal commas backslash-escaped.
    slide_progress = f"min(t/{fade}\\,1)"
    y_expr = f"({base_y})+({slide}*(1-{slide_progress}))"
    alpha_expr = (
        f"if(lt(t\\,{fade})\\,t/{fade}\\,"
        f"if(gt(t\\,{duration:.3f}-{fade})\\,({duration:.3f}-t)/{fade}\\,1))"
    )

    out_label = f"txt{beat_idx}"
    filt = (
        f"[{base_label}]drawtext=fontfile='{style['font']}':text='{escaped}':expansion=none:"
        f"fontsize={fitted_fontsize}:fontcolor={style['fontcolor']}:"
        f"box=1:boxcolor={style['boxcolor']}:boxborderw={style['boxborderw']}:"
        f"line_spacing={style['line_spacing']}:"
        f"x='{base_x}':y='{y_expr}':alpha='{alpha_expr}'[{out_label}]"
    )
    return [filt], out_label


# ── transition chain (concatenate beat clips with varied transitions) ──

def _transition_spec(beats: list[dict], i: int) -> tuple[str, float]:
    """Transition OUT of beat i (into beat i+1) as a validated (type, duration).
    Type "cut" renders as a 2-frame fade, i.e. a hard cut."""
    t = beats[i].get("transition") or {}
    ttype = t.get("type", "fade")
    tdur = float(t.get("duration", config.CROSSFADE_DURATION))
    if ttype == "cut":
        ttype = "fade"
        tdur = config.TRANSITION_CUT_AS_FADE_SECONDS
    elif ttype not in config.VALID_XFADE_TYPES:
        print(f"[compositor] warning: unknown transition type {ttype!r} — using 'fade'.")
        ttype = "fade"
    return ttype, tdur


def _chain_xfade(labels_durations: list[tuple[str, float]], comp_w: int, comp_h: int,
                 beats: list[dict]) -> tuple[list[str], str, float]:
    """
    Chains beat cards with the per-beat `transition` field
    ({"type", "duration"} — assigned by planner.assign_transitions, falling
    back to config.CROSSFADE_DURATION fades for hand-made beats.json).
    Cards arrive pre-extended by their outgoing transition length, so the
    chained track's total equals the narration duration exactly.
    """
    lines = []

    if len(labels_durations) == 1:
        label, dur = labels_durations[0]
        return lines, label, dur

    running_label, running_dur = labels_durations[0]
    for i in range(1, len(labels_durations)):
        next_label, next_dur = labels_durations[i]
        ttype, want_dur = _transition_spec(beats, i - 1)
        this_t = min(want_dur, running_dur, next_dur)  # never exceed either clip's own length
        offset = running_dur - this_t
        out_label = f"xf{i}"
        lines.append(
            f"[{running_label}][{next_label}]xfade=transition={ttype}:duration={this_t:.3f}:offset={offset:.3f}[{out_label}]"
        )
        running_dur = running_dur + next_dur - this_t
        running_label = out_label

    return lines, running_label, running_dur


# ── bottom zone (talking head) ──────────────────────────────────────────

def _ensure_pingpong_clip(video_path: Path, bw: int, bh: int) -> Path:
    """
    Pre-renders a forward+reverse ping-pong version of the talking head,
    already cropped to bottom-zone size, and caches it to disk.

    This is later looped via ffmpeg's demuxer-level `-stream_loop -1` on the
    resulting file, NOT the `loop` FILTER in-graph. The `loop` filter needs
    an exact frame-count guess (duration*fps), and even a couple of frames
    of overshoot made it hold the last frame for ~20 extra frames (a visible
    freeze-then-jump) instead of looping cleanly — re-reading a real file
    from disk has no such guesswork.
    """
    cache_key = f"{video_path.stem}_{bw}x{bh}_pingpong.mp4"
    out_path = config.CACHE_DIR / cache_key
    if out_path.exists():
        return out_path

    fps = config.FPS
    filter_complex = (
        f"[0:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},fps={fps}[bh_scaled];"
        f"[bh_scaled]split=2[fwd][rev_src];"
        f"[rev_src]reverse[rev];"
        f"[fwd][rev]concat=n=2:v=1:a=0[pingpong]"
    )
    cmd = [
        config.FFMPEG_BIN, "-y",
        "-ss", str(config.TALKING_HEAD_TRIM_START_SECONDS), "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", "[pingpong]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        str(out_path),
    ]
    print(f"[compositor] pre-rendering ping-pong loop clip -> {out_path}")
    subprocess.run(cmd, check=True)
    return out_path


def _build_bottom_zone_filters(video_input_idx: int, pre_scaled: bool, bw: int, bh: int, total_duration: float) -> list[str]:
    if pre_scaled:
        # Ping-pong clip was already scaled/cropped/fps-converted (and had
        # its opening settling frames trimmed) when pre-rendered; the
        # demuxer's -stream_loop is what makes it repeat.
        return [f"[{video_input_idx}:v]trim=duration={total_duration:.3f},setpts=PTS-STARTPTS[bottomclip]"]
    trim_start = config.TALKING_HEAD_TRIM_START_SECONDS
    return [
        f"[{video_input_idx}:v]trim=start={trim_start:.3f}:end={trim_start + total_duration:.3f},setpts=PTS-STARTPTS,"
        f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},fps={config.FPS}[bottomclip]"
    ]


# ── final look: grade → bloom → grain → vignette ────────────────────────

def _build_finishing_filters(src_label: str) -> tuple[list[str], str]:
    """
    One global "grade" over the finished composite — the Resolve-timeline
    move of applying a single look to every clip at once, so stills and
    talking head share the same color response. Stack: eq+curves+colorbalance
    (or a .cube LUT if assets/grade.cube exists), halation-style bloom,
    film grain, vignette.
    """
    lines: list[str] = []
    label = src_label

    if config.GRADE_ENABLED:
        lut_path = Path(config.GRADE_LUT_PATH)
        if lut_path.exists():
            grade = f"lut3d=file={lut_path}"
        else:
            grade = (
                f"eq=contrast={config.GRADE_CONTRAST}:saturation={config.GRADE_SATURATION}"
                f":gamma={config.GRADE_GAMMA},"
                f"curves=all='{config.GRADE_CURVES_POINTS}',"
                f"colorbalance="
                f"rs={config.GRADE_SHADOWS_RS}:bs={config.GRADE_SHADOWS_BS}"
                f":rm={config.GRADE_MIDTONES_RM}:bm={config.GRADE_MIDTONES_BM}"
                f":rh={config.GRADE_HIGHLIGHTS_RH}:bh={config.GRADE_HIGHLIGHTS_BH}"
            )
        out = f"{src_label}_graded"
        lines.append(f"[{label}]{grade}[{out}]")
        label = out

    if config.BLOOM_ENABLED:
        # Halation: a heavily blurred copy screened back over the frame lifts
        # highlights into a soft glow. blend must happen in RGB planes —
        # screen-blending raw Y/U/V planes would shift chroma.
        a, b = f"{label}_ba", f"{label}_bb"
        blur = f"{label}_blur"
        out = f"{label}_bloom"
        lines.append(f"[{label}]split=2[{a}][{b}]")
        lines.append(f"[{b}]format=gbrp,gblur=sigma={config.BLOOM_SIGMA}[{blur}]")
        lines.append(
            f"[{a}]format=gbrp[{a}r];"
            f"[{a}r][{blur}]blend=all_mode=screen:all_opacity={config.BLOOM_OPACITY},format=yuv420p[{out}]"
        )
        label = out

    if config.FILM_GRAIN_STRENGTH > 0:
        out = f"{label}_grain"
        lines.append(f"[{label}]noise=alls={config.FILM_GRAIN_STRENGTH}:allf=t+u[{out}]")
        label = out

    out = f"{label}_vig"
    lines.append(f"[{label}]vignette={config.VIGNETTE_ANGLE}[{out}]")
    label = out
    return lines, label


# ── word-synced captions (kinetic typography layer) ─────────────────────

def _build_caption_filters(beats: list[dict], src_label: str,
                           canvas_w: int, canvas_h: int) -> tuple[list[str], str]:
    """
    Renders captions.build_captions() groups as drawtext overlays on the full
    canvas: each group pops in (fade + upward settle) exactly when its words
    are being spoken and hard-swaps to the next. Groups containing numbers
    render in the accent color.
    """
    import captions as captions_mod

    if not config.CAPTIONS_ENABLED:
        return [], src_label

    groups: list[dict] = []
    for beat in beats:
        if beat.get("caption_mode") == "off":
            continue  # e.g. the hook, when its big on-screen text would fight the captions
        groups.extend(captions_mod.build_captions(beat))
    if not groups:
        return [], src_label

    y_center = round(config.CAPTION_Y_CENTER_FRACTION * canvas_h)
    fade = config.CAPTION_FADE_IN
    settle = config.CAPTION_SETTLE_PX

    lines: list[str] = []
    label = src_label
    for i, g in enumerate(groups):
        text, fontsize = captions_mod.fit_caption_fontsize(g["text"], canvas_w)
        escaped = _escape_drawtext(text)
        s, e = g["start"], g["end"]

        # easeOutCubic on both the fade and the settle so entries decelerate
        # into place instead of snapping.
        p_in = f"min((t-{s:.3f})/{fade}\\,1)"
        eased_in = f"(1-pow(1-{p_in}\\,3))"
        alpha_expr = f"min((t-{s:.3f})/{fade}\\,1)"
        y_expr = f"{y_center}-text_h/2+({settle}*(1-{eased_in}))"

        out_label = f"cap{i}"
        lines.append(
            f"[{label}]drawtext=fontfile='{config.CAPTION_FONT}':text='{escaped}':expansion=none:"
            f"fontsize={fontsize}:fontcolor={config.CAPTION_ACCENT_COLOR if g['accent'] else config.CAPTION_TEXT_COLOR}:"
            f"borderw={config.CAPTION_BORDER_W}:bordercolor={config.CAPTION_BORDER_COLOR}:"
            f"shadowcolor={config.CAPTION_SHADOW_COLOR}:shadowx=0:shadowy={config.CAPTION_SHADOW_OFFSET_PX}:"
            f"x='(w-text_w)/2':y='{y_expr}':alpha='{alpha_expr}':"
            f"enable='between(t\\,{s:.3f}\\,{e:.3f})'[{out_label}]"
        )
        label = out_label
    return lines, label


# ── audio post chain ────────────────────────────────────────────────────

def _voice_chain() -> str:
    """Mastering chain applied to narration: rumble guard → compression →
    de-ess → EBU R128 normalization (-14 LUFS short-form target)."""
    return (
        f"highpass=f={config.AUDIO_HIGHPASS_HZ},"
        f"acompressor={config.AUDIO_COMPRESSOR},"
        f"deesser={config.AUDIO_DEESSER},"
        f"loudnorm={config.AUDIO_LOUDNORM},"
        f"aresample={config.AUDIO_TARGET_RATE}"
    )


def _build_audio_filters(total_duration: float, mute_original_audio: bool,
                         music_input_idx: Optional[int]) -> list[str]:
    D = total_duration
    lines: list[str] = []

    if music_input_idx is None:
        if mute_original_audio:
            lines.append(
                f"anullsrc=channel_layout=stereo:sample_rate={config.AUDIO_TARGET_RATE},"
                f"atrim=duration={D:.3f}[aout]"
            )
        elif config.AUDIO_CHAIN_ENABLED:
            lines.append(f"[0:a]{_voice_chain()},apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[aout]")
        else:
            lines.append(f"[0:a]apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[aout]")
        return lines

    # Music bed: looped, faded in/out, sidechain-ducked under the voice.
    fade_out_start = max(D - 1.5, 0)
    lines.append(
        f"[{music_input_idx}:a]aresample={config.AUDIO_TARGET_RATE},"
        f"volume={config.MUSIC_VOLUME},"
        f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d=1.5,"
        f"atrim=duration={D:.3f},asetpts=PTS-STARTPTS[mus]"
    )

    if mute_original_audio:
        # No voice track: the bed IS the soundtrack.
        lines.append(f"[mus]anull[aout]")
        return lines

    lines.append("[0:a]asplit=2[vo][scraw]")
    # sidechaincompress needs matching rates on both inputs — pin the key
    # signal to the same rate as the bed.
    lines.append(f"[scraw]aresample={config.AUDIO_TARGET_RATE}[sc]")
    if config.AUDIO_CHAIN_ENABLED:
        lines.append(f"[vo]{_voice_chain()},apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[vox]")
    else:
        lines.append(f"[vo]apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[vox]")
    lines.append(f"[mus][sc]sidechaincompress={config.MUSIC_DUCK}[musduck]")
    lines.append(
        "[vox][musduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    return lines


# ── main render entrypoint ──────────────────────────────────────────────

def render(beats: list[dict], talking_head_path: Path, output_path: Path,
           total_duration: Optional[float] = None, mute_original_audio: bool = False) -> None:
    import planner  # for transition assignment; deferred so compositor stays import-light elsewhere

    beats = sorted(beats, key=lambda b: b["start"])
    # Backward compat: hand-written / older beats.json may lack per-beat
    # transitions — fill them in with the same deterministic variety the
    # planner would have produced.
    if any("transition" not in b for b in beats[:-1]):
        planner.assign_transitions(beats)
    for i, b in enumerate(beats):
        if _is_diagram(b) or _is_video(b):
            continue
        if not b.get("prepped_image_path"):
            raise ValueError(f"Beat {i} is missing prepped_image_path — run prep_images.py first.")
    for i in range(len(beats) - 1):
        if abs(beats[i]["end"] - beats[i + 1]["start"]) > 0.05:
            print(f"[compositor] warning: beat {i} ends at {beats[i]['end']} but beat {i+1} starts at "
                  f"{beats[i+1]['start']} — clips are concatenated back-to-back regardless of this gap/overlap.")

    if total_duration is None:
        total_duration = max(b["end"] for b in beats)

    talking_head_path = Path(talking_head_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    grid_bg_path = _ensure_grid_background()

    # Card inputs are added in the card-building loop below (image beats as
    # looped stills, diagram beats as pre-rendered gfx clips) so each beat's
    # input carries its transition-extended duration. Grid bg / ping-pong /
    # music inputs are appended AFTER them, keeping the index formulas below
    # (grid = 1+len(beats), etc.) true at filter-graph build time.
    cmd = [config.FFMPEG_BIN, "-y", "-i", str(talking_head_path)]
    grid_bg_input_idx = 1 + len(beats)

    _, _, bottom_w, bottom_h = config.BOTTOM_ZONE
    bottom_w, bottom_h = _even(bottom_w), _even(bottom_h)
    usable_duration = _probe_duration(talking_head_path) - config.TALKING_HEAD_TRIM_START_SECONDS
    needs_bottom_loop = usable_duration < total_duration
    if needs_bottom_loop:
        pingpong_path = _ensure_pingpong_clip(talking_head_path, bottom_w, bottom_h)
        bottom_input_idx = grid_bg_input_idx + 1
    else:
        bottom_input_idx = 0

    # Optional music bed input (PIPELINE_MUSIC_PATH or main.py's --music).
    music_input_idx: Optional[int] = None
    if config.MUSIC_PATH:
        music_path = Path(config.MUSIC_PATH)
        if music_path.exists():
            music_input_idx = grid_bg_input_idx + 1 + (1 if needs_bottom_loop else 0)
        else:
            print(f"[compositor] warning: MUSIC_PATH not found ({music_path}) — no music bed.")

    filter_lines: list[str] = []
    comp_w, comp_h = _card_dims()
    comp_w += config.CARD_BORDER_PX * 2 + config.CARD_SHADOW_OFFSET_PX
    comp_h += config.CARD_BORDER_PX * 2 + config.CARD_SHADOW_OFFSET_PX
    comp_w, comp_h = _even(comp_w), _even(comp_h)

    card_labels: list[tuple[str, float]] = []
    for i, beat in enumerate(beats):
        beat_len = beat["end"] - beat["start"]
        # Each xfade consumes `cf` seconds of overlap between two cards, so
        # every card (except the last) is rendered with its outgoing
        # transition appended as extra tail — otherwise the top track ends
        # Σ(transitions) seconds before the narration does.
        if i < len(beats) - 1:
            _, cf_out = _transition_spec(beats, i)
            duration = beat_len + cf_out
        else:
            duration = beat_len
        image_input_idx = i + 1
        card_w, card_h = _card_dims()
        if _is_diagram(beat):
            cw, ch = _card_dims()
            clip = motion_gfx.render_diagram_clip(beat, cw, ch, duration)
            cmd += ["-i", str(clip)]
            card_lines, card_label, cw, ch = _build_card_filters(i, image_input_idx, duration)
            # Diagram clips arrive pre-animated at exact card size — no
            # Ken Burns, just frame-rate/duration normalization into the
            # same [zp{i}] slot the card chain expects.
            card_lines[0] = (
                f"[{image_input_idx}:v]fps={config.FPS},"
                f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[zp{i}]"
            )
        elif _is_video(beat):
            # Top-zone video beat (e.g. a software screen recording): the
            # clip IS the visual — normalize and play it, no Ken Burns.
            cw, ch = _card_dims()
            clip = _prepare_video_clip(Path(beat["resolved_clip_path"]), cw, ch, duration)
            cmd += ["-i", str(clip)]
            card_lines, card_label, cw, ch = _build_card_filters(i, image_input_idx, duration)
            card_lines[0] = (
                f"[{image_input_idx}:v]fps={config.FPS},"
                f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[zp{i}]"
            )
        else:
            cmd += ["-loop", "1", "-framerate", str(config.FPS), "-t", f"{duration:.3f}", "-i", beat["prepped_image_path"]]
            card_lines, card_label, cw, ch = _build_card_filters(i, image_input_idx, duration)
            focus = beat.get("focus_point_card")
            focus_xy = (focus["x"], focus["y"]) if focus else None
            zoompan_expr = _zoompan_filter(beat["motion"], duration, card_w, card_h, focus=focus_xy)
            card_lines[0] = card_lines[0].replace("{ZOOMPAN}", zoompan_expr)

        filter_lines.extend(card_lines)

        text_lines, final_label = _build_text_filter(i, card_label, beat, duration, cw, ch)
        filter_lines.extend(text_lines)

        card_labels.append((final_label, duration))

    # Static inputs come after the per-beat card inputs — see the index
    # formulas above (grid bg, then optional ping-pong loop, then music).
    cmd += ["-loop", "1", "-framerate", str(config.FPS), "-i", str(grid_bg_path)]
    if needs_bottom_loop:
        cmd += ["-stream_loop", "-1", "-i", str(pingpong_path)]
    if music_input_idx is not None:
        cmd += ["-stream_loop", "-1", "-i", str(Path(config.MUSIC_PATH))]

    xfade_lines, top_track_label, top_track_duration = _chain_xfade(card_labels, comp_w, comp_h, beats)
    filter_lines.extend(xfade_lines)

    # Canvas base = dot-grid texture. The input is `-loop 1`'d already (an
    # infinite stream of the same still frame), so this just sizes/trims it
    # to the full output duration.
    filter_lines.append(
        f"[{grid_bg_input_idx}:v]scale={config.CANVAS_WIDTH}:{config.CANVAS_HEIGHT},"
        f"trim=duration={total_duration:.3f},setpts=PTS-STARTPTS,fps={config.FPS}[canvas]"
    )

    top_zone_x, top_zone_y, top_zone_w, top_zone_h = config.TOP_ZONE
    overlay_x = top_zone_x + (top_zone_w - comp_w) // 2
    overlay_y = top_zone_y + (top_zone_h - comp_h) // 2
    filter_lines.append(
        f"[canvas][{top_track_label}]overlay={overlay_x}:{overlay_y}:eof_action=pass[withtop]"
    )

    filter_lines.extend(_build_bottom_zone_filters(bottom_input_idx, needs_bottom_loop, bottom_w, bottom_h, total_duration))
    bx, by, _, _ = config.BOTTOM_ZONE
    filter_lines.append(f"[withtop][bottomclip]overlay={bx}:{by}:eof_action=repeat[withbottom]")

    # Captions sit on the full canvas (over card AND talking head) and are
    # applied BEFORE the finishing pass so they pick up the same grade,
    # bloom and grain as everything else — one unified look, not stickers.
    cap_lines, final_label = _build_caption_filters(beats, "withbottom",
                                                    config.CANVAS_WIDTH, config.CANVAS_HEIGHT)
    filter_lines.extend(cap_lines)
    finish_lines, final_label = _build_finishing_filters(final_label)
    filter_lines.extend(finish_lines)

    # Reels grammar: fade up from black under the hook, fade to black on the
    # payoff — applied over the fully finished frame.
    if beats and beats[0].get("role") == "hook":
        filter_lines.append(
            f"[{final_label}]fade=t=in:st=0:d={config.HOOK_FADE_IN_SECONDS:.2f}[hookfade]"
        )
        final_label = "hookfade"
    if beats and beats[-1].get("role") == "payoff":
        st = max(total_duration - config.PAYOFF_FADE_OUT_SECONDS, 0)
        filter_lines.append(
            f"[{final_label}]fade=t=out:st={st:.3f}:d={config.PAYOFF_FADE_OUT_SECONDS:.2f}[payofffade]"
        )
        final_label = "payofffade"
    filter_lines.append(f"[{final_label}]null[final]")

    # Narration audio mastered through the post chain (and mixed against the
    # optional ducked music bed); padded/trimmed to exactly total_duration so
    # it never desyncs from the video length.
    filter_lines.extend(_build_audio_filters(total_duration, mute_original_audio, music_input_idx))

    filter_complex = ";".join(filter_lines)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[final]", "-map", "[aout]",
        "-t", f"{total_duration:.3f}",
        "-r", str(config.FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    print(f"[compositor] rendering {len(beats)} beats -> {output_path} (total {total_duration:.1f}s)")
    subprocess.run(cmd, check=True)
    print(f"[compositor] done: {output_path}")


def render_from_files(beats_json_path: Path, talking_head_path: Path, output_path: Path,
                       mute_original_audio: bool = False) -> None:
    with open(beats_json_path) as f:
        beats = json.load(f)
    render(beats, Path(talking_head_path), Path(output_path), mute_original_audio=mute_original_audio)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render the explainer video from an existing beats.json.")
    parser.add_argument("--beats", default=str(config.BEATS_JSON_PATH))
    parser.add_argument("--video", required=True, help="Path to the talking-head video")
    parser.add_argument("--out", default=str(config.OUTPUT_DIR / "final.mp4"))
    parser.add_argument("--mute-original-audio", action="store_true",
                         help="Discard the talking-head video's own audio; output gets silence instead.")
    args = parser.parse_args()

    render_from_files(Path(args.beats), Path(args.video), Path(args.out), mute_original_audio=args.mute_original_audio)
