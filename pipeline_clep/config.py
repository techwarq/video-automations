"""
pipeline_clep config — camera-tracked product-clip renderer.

Self-contained (does NOT import pipeline/pipeline_motion config) so
`allore.py --mode clep` stays decoupled from the explainer engines.
Only needs Pillow + ffmpeg on PATH (ffmpeg-full preferred for yuv420p).
"""

import os
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "cache"
OUTPUT_DIR = PROJECT_ROOT / "output"
DEMO_DIR = PROJECT_ROOT / "demo"
for d in (CACHE_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

FPS = 30

# Output canvases (even numbers for yuv420p). 16:9 is the primary canvas.
# QUALITY scales everything: 720p -> 1280x720, 1080p -> 1920x1080 (default).
ASPECTS = {
    "16:9": (1280, 720),
    "1:1": (960, 960),
    "4:5": (864, 1080),
    "9:16": (720, 1280),
}
QUALITIES = ("720p", "1080p")
QUALITY = os.environ.get("CLEP_QUALITY", "1080p")


def canvas(aspect: str = "16:9", quality: str | None = None,
           size: str | None = None) -> tuple[int, int]:
    # Explicit size (landing-page cards, e.g. "1120x640") wins over
    # aspect+quality. Dimensions are forced even (yuv420p requirement).
    if size:
        m = re.match(r"\s*(\d{3,4})\s*x\s*(\d{3,4})\s*$", size)
        if not m:
            raise ValueError(f"bad --size {size!r}: want WxH, e.g. 1120x640")
        w, h = int(m.group(1)), int(m.group(2))
        if not (160 <= w <= 4096 and 160 <= h <= 4096):
            raise ValueError(f"bad --size {size!r}: each side must be 160-4096")
        return (w // 2) * 2, (h // 2) * 2
    w, h = ASPECTS.get(aspect, ASPECTS["16:9"])
    if (quality or QUALITY) == "1080p":
        w, h = int(w * 1.5), int(h * 1.5)
    return (w // 2) * 2, (h // 2) * 2


def aspect_class(w: int, h: int) -> str:
    """Nearest camera-tuning bucket for an arbitrary canvas (custom --size)."""
    r = w / max(h, 1)
    if r >= 1.5:
        return "16:9"
    if r >= 1.05:
        return "3:2"
    if r >= 0.85:
        return "1:1"
    if r >= 0.65:
        return "4:5"
    return "9:16"


def _hex_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"bad color {s!r}: want #rgb or #rrggbb")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def resolve_bg(bg: str | None, style: str) -> tuple[tuple, tuple, tuple]:
    """--bg / API {bg} -> 3-stop gradient. Preset, custom, or solid.

    Accepted forms:
      "blush"                 any BACKDROPS preset (independent of --style)
      "#F5E6F0,#FFFFFF"       custom 2-stop gradient (mid = average)
      "#F5E6F0,#FDF7FA,#F0D6E4"  custom 3-stop gradient
      "solid:#FFF5F7" / "#FFF5F7" (single color = flat background)
    """
    if not bg:
        return BACKDROPS.get(style, BACKDROPS["saas"])
    b = bg.strip()
    if b in BACKDROPS:
        return BACKDROPS[b]
    solid = b[6:] if b.lower().startswith("solid:") else (b if b.startswith("#") else None)
    if solid is not None and "," not in solid:
        c = _hex_color(solid)
        return (c, c, c)
    parts = [p.strip() for p in b.split(",") if p.strip()]
    if len(parts) in (2, 3) and all(p.startswith("#") for p in parts):
        cols = [_hex_color(p) for p in parts]
        if len(cols) == 2:
            mid = tuple((cols[0][i] + cols[1][i]) // 2 for i in range(3))
            return (cols[0], mid, cols[1])
        return (cols[0], cols[1], cols[2])
    raise ValueError(
        f"bad --bg {bg!r}: want preset {sorted(BACKDROPS)} | #hex,#hex[,#hex] | solid:#hex")

STYLES = ("minimal", "saas", "cinematic", "apple")

# Style palettes for the synthetic app mock.
# cinematic/apple/saas/minimal differ in chrome + accent, same layout.
PALETTES = {
    "saas": {
        "page": (245, 247, 251), "chrome": (255, 255, 255),
        "ink": (17, 24, 39), "muted": (107, 114, 128),
        "accent": (37, 99, 235), "accent_ink": (255, 255, 255),
        "card": (255, 255, 255), "card_border": (226, 232, 240),
        "input_bg": (249, 250, 251), "result_bg": (239, 246, 255),
        "progress_track": (229, 231, 235), "progress_fill": (37, 99, 235),
    },
    "minimal": {
        "page": (255, 255, 255), "chrome": (255, 255, 255),
        "ink": (10, 10, 10), "muted": (115, 115, 115),
        "accent": (10, 10, 10), "accent_ink": (255, 255, 255),
        "card": (250, 250, 250), "card_border": (229, 229, 229),
        "input_bg": (255, 255, 255), "result_bg": (245, 245, 245),
        "progress_track": (229, 229, 229), "progress_fill": (10, 10, 10),
    },
    "cinematic": {
        "page": (12, 13, 16), "chrome": (22, 24, 29),
        "ink": (245, 245, 247), "muted": (150, 152, 160),
        "accent": (214, 255, 59), "accent_ink": (12, 13, 16),
        "card": (28, 30, 36), "card_border": (48, 51, 60),
        "input_bg": (18, 20, 24), "result_bg": (35, 39, 47),
        "progress_track": (48, 51, 60), "progress_fill": (214, 255, 59),
    },
    "apple": {
        "page": (242, 242, 247), "chrome": (255, 255, 255),
        "ink": (28, 28, 30), "muted": (120, 120, 128),
        "accent": (0, 122, 255), "accent_ink": (255, 255, 255),
        "card": (255, 255, 255), "card_border": (230, 230, 236),
        "input_bg": (242, 242, 247), "result_bg": (234, 243, 255),
        "progress_track": (229, 229, 234), "progress_fill": (0, 122, 255),
    },
}

# Camera direction
CAM_PUSH_IN = 1.65   # max zoom onto the data-clep element
CAM_PULL_BACK = 1.32  # result hold zoom (slight pull-back so result breathes)
CAM_ESTABLISH = 1.0

CURSOR_SIZE = 22  # px at 1280-wide baseline, scaled per canvas

# ── Backdrop: Screen-Studio-style gradient canvas the app window floats on.
# 3-stop diagonal (top-left, center, bottom-right), sampled per style.
# Overridable per clip with --bg / API {bg}: a preset name below, a custom
# gradient "#c0,c1[,c2]", or a flat "solid:#hex" (e.g. to match a landing page).
BACKDROPS = {
    "saas": [(196, 181, 253), (251, 207, 232), (191, 219, 254)],   # violet → pink → blue
    "minimal": [(232, 232, 238), (248, 248, 250), (224, 228, 236)],  # soft gray
    "cinematic": [(52, 28, 96), (16, 12, 38), (96, 36, 120)],        # deep purple
    "apple": [(199, 210, 254), (243, 244, 246), (186, 230, 253)],    # blue-gray
    "blush": [(250, 226, 238), (253, 245, 250), (240, 214, 230)],    # pale pink (light landing pages)
}
WINDOW_PAD_X_FRAC = 0.06   # canvas margin around the fitted window (zoom 1.0)
WINDOW_PAD_Y_FRAC = 0.09
WINDOW_RADIUS = 18         # px at 1280-wide baseline

def _which_or(env_key: str, hard_default: str, exe: str) -> str:
    return os.environ.get(env_key) or shutil.which(exe) or hard_default


FFMPEG_BIN = _which_or("PIPELINE_FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "ffmpeg")
FFPROBE_BIN = _which_or("PIPELINE_FFPROBE_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe", "ffprobe")

# Fonts (macOS). Renderer falls back gracefully.
_FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONT_BOLD = os.environ.get("PIPELINE_FONT_BOLD", str(_FONT_DIR / "Arial Bold.ttf"))
FONT_BLACK = os.environ.get("PIPELINE_FONT_BLACK", str(_FONT_DIR / "Arial Black.ttf"))
FONT_REGULAR = os.environ.get("PIPELINE_FONT_REGULAR", str(_FONT_DIR / "Arial.ttf"))
