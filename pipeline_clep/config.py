"""
pipeline_clep config — camera-tracked product-clip renderer.

Self-contained (does NOT import pipeline/pipeline_motion config) so
`allore.py --mode clep` stays decoupled from the explainer engines.
Only needs Pillow + ffmpeg on PATH (ffmpeg-full preferred for yuv420p).
"""

import os
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


def canvas(aspect: str = "16:9", quality: str | None = None) -> tuple[int, int]:
    w, h = ASPECTS.get(aspect, ASPECTS["16:9"])
    if (quality or QUALITY) == "1080p":
        w, h = int(w * 1.5), int(h * 1.5)
    return (w // 2) * 2, (h // 2) * 2

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
BACKDROPS = {
    "saas": [(196, 181, 253), (251, 207, 232), (191, 219, 254)],   # violet → pink → blue
    "minimal": [(232, 232, 238), (248, 248, 250), (224, 228, 236)],  # soft gray
    "cinematic": [(52, 28, 96), (16, 12, 38), (96, 36, 120)],        # deep purple
    "apple": [(199, 210, 254), (243, 244, 246), (186, 230, 253)],    # blue-gray
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
