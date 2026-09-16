"""
Motion-only pipeline config — pure kinetic typography / motion graphics,
no talking head. Matches the reference: clean white + vivid blue on light.

Supports both 16:9 landscape (default, 1920x1080 matching the reference
video at pipeline/download) and 9:16 portrait (set PIPELINE_MOTION_PORTRAIT=1)
for reels/shorts.

Canvas is FULLSCREEN — no top/bottom zones. Every beat renders as a
fullscreen card via motion_gfx_clean.
"""

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "cache"
ASSETS_DIR = PROJECT_ROOT / "assets"
OUTPUT_DIR = PROJECT_ROOT / "output"
BEATS_JSON_PATH = PROJECT_ROOT / "beats.json"
SELECTION_LOG_PATH = PROJECT_ROOT / "selection_log.json"

for d in (CACHE_DIR, ASSETS_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Canvas ───────────────────────────────────────────────────────────────
# 1080p (1920x1080) is default — true 4K via PIPELINE_4K=1 or --4k (3840x2160).
# Portrait remains 1080x1920 (or 2160x3840 in 4K portrait).
_PORTRAIT = os.environ.get("PIPELINE_MOTION_PORTRAIT", "").lower() in ("1", "true", "yes")
_FOURK = os.environ.get("PIPELINE_4K", "").lower() in ("1", "true", "yes") or os.environ.get("PIPELINE_MOTION_4K", "").lower() in ("1", "true", "yes")
_HQ = os.environ.get("PIPELINE_HQ", "").lower() in ("1", "true", "yes")

if _FOURK:
    if _PORTRAIT:
        CANVAS_WIDTH = 2160
        CANVAS_HEIGHT = 3840
    else:
        CANVAS_WIDTH = 3840
        CANVAS_HEIGHT = 2160
else:
    if _PORTRAIT:
        CANVAS_WIDTH = 1080
        CANVAS_HEIGHT = 1920
    else:
        CANVAS_WIDTH = 1920
        CANVAS_HEIGHT = 1080

FPS = 30
PORTRAIT = _PORTRAIT
FOUR_K = _FOURK
HQ = _HQ

# Quality presets — HQ = archival (crf 16, preset slow) vs default (crf 18, preset medium)
# 1080p assets at crf 20 look soft on text; Talo editorial needs crisp.
if _HQ or _FOURK:
    HQ_CRF = 16
    HQ_PRESET = "slow"
    MGFX_CRF = 14
else:
    HQ_CRF = 18
    HQ_PRESET = "medium"
    MGFX_CRF = 17

# For compat with compositor code that expects zones — define full canvas as zone
FULL_ZONE = (0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)

# ── Canvas background / palette (matches reference: white + vivid blue) ──
# Pillow RGB + ffmpeg color spec
CANVAS_BG_COLOR = "white"                      # ffmpeg color name
CANVAS_BG_COLOR_RGB = (255, 255, 255)          # Pillow RGB
CANVAS_BG_BLUE_RGB = (1, 12, 202)              # reference vivid blue #010CCA
CANVAS_BG_BLUE_HEX = "0x010CCA"                # ffmpeg spec
CANVAS_BG_LIGHT_GRAY_RGB = (245, 245, 250)     # card bg #F5F5FA (subtle off-white)
CANVAS_BG_LIGHT_GRAY_HEX = "0xF5F5FA"
CANVAS_GRID_DOT_RGB = (225, 228, 245)          # barely visible dots if grid enabled
CANVAS_GRID_SPACING_PX = 48
GRID_ENABLED = False                            # reference is plain white, no dots

# Card treatment — fullscreen, no border/shadow by default (clean).
# Kept configurable in case you want a “card on canvas” look for image beats.
CARD_BORDER_PX = 0
CARD_BORDER_COLOR = "white@0"
CARD_SHADOW_OFFSET_PX = 0
CARD_SHADOW_COLOR = "black@0"
CARD_SHADOW_BLUR = 0

# For image beats that still want to float as a centered card inside fullscreen
# (e.g. product screenshots), inset margin.
CARD_INSET_MARGIN = 64  # px from canvas edge when rendering image as centered card

# ── Motion / transitions ────────────────────────────────────────────────
KEN_BURNS_ZOOM_START = 1.0
KEN_BURNS_ZOOM_END = 1.12
KEN_BURNS_PAN_FRACTION = 0.10
KEN_BURNS_EASED = True

CROSSFADE_DURATION = 0.28

VALID_XFADE_TYPES = (
    "fade", "fadeblack", "fadewhite", "dissolve",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "radial", "distance",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "squeezeh", "squeezev", "zoomin",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
)
TRANSITION_CUT_AS_FADE_SECONDS = 0.07

# ── Final grade / finishing look ─────────────────────────────────────────
# Reference is clean, no film grade. Disable by default for crisp motion graphics.
GRADE_ENABLED = False
GRADE_CONTRAST = 1.04
GRADE_SATURATION = 1.06
GRADE_GAMMA = 1.0
GRADE_CURVES_POINTS = "0/0 0.5/0.5 1/1"
GRADE_SHADOWS_RS, GRADE_SHADOWS_BS = 0, 0
GRADE_MIDTONES_RM, GRADE_MIDTONES_BM = 0, 0
GRADE_HIGHLIGHTS_RH, GRADE_HIGHLIGHTS_BH = 0, 0
GRADE_LUT_PATH = ASSETS_DIR / "grade.cube"

BLOOM_ENABLED = False
BLOOM_SIGMA = 10
BLOOM_OPACITY = 0.10
FILM_GRAIN_STRENGTH = 0
VIGNETTE_ANGLE = "PI/6"

# ── Captions ─────────────────────────────────────────────────────────────
# Captions are optional for motion-only (big typography already carries the message).
# Enabled by default but renders subtle lower-third if needed.
CAPTIONS_ENABLED = False  # set True via CLI --captions to burn word-synced captions
CAPTION_WORDS_PER_GROUP = 3
CAPTION_FONTSIZE = 46 if not _PORTRAIT else 52
CAPTION_MIN_FONTSIZE = 32
CAPTION_TEXT_COLOR = "black"
CAPTION_ACCENT_COLOR = "0x010CCA"  # blue accent for numbers
CAPTION_BORDER_COLOR = "white"
CAPTION_BORDER_W = 4
CAPTION_SHADOW_COLOR = "black@0.18"
CAPTION_SHADOW_OFFSET_PX = 2
CAPTION_MAX_WIDTH_FRAC = 0.86
CAPTION_Y_CENTER_FRACTION = 0.88
CAPTION_FADE_IN = 0.12
CAPTION_SETTLE_PX = 10
CAPTIONS_UPPERCASE = False

# ── Audio post chain ─────────────────────────────────────────────────────
AUDIO_CHAIN_ENABLED = True
AUDIO_HIGHPASS_HZ = 75
AUDIO_COMPRESSOR = "threshold=-20dB:ratio=2.5:attack=10:release=150:makeup=3"
AUDIO_DEESSER = "i=0.5:m=0.5:f=0.5"
AUDIO_LOUDNORM = "I=-14:TP=-1.5:LRA=11"
AUDIO_TARGET_RATE = 48000

MUSIC_PATH = os.environ.get("PIPELINE_MUSIC_PATH")
MUSIC_VOLUME = 0.22
MUSIC_DUCK = "threshold=0.02:ratio=6:attack=25:release=350"

# ── Motion-graphics engine — clean / reference-matched styles ────────────
# Distinct from the hand-drawn dark/whiteboard styles of the talking-head pipeline.
# Reference palette: white bg, black text, vivid blue accent, light-gray cards.
# Talo editorial film palette (from brief):
#   paper off-white #FFFBF5 / cream, near-black, dark-olive #2B3329, muted-blue/periwinkle #8C9BCE,
#   acid-green accent only for WORKING/DONE/price #D6FF2C
TALO_ACCENT_RGB = (140, 155, 206)  # periwinkle #8C9BCE sampled from "Freelancer." in Image 1
TALO_ACCENT_HEX = "0x8C9BCE"
TALO_ACID_RGB = (214, 255, 44)    # acid lime #D6FF2C for live states
TALO_ACID_HEX = "0xD6FF2C"
TALO_PAPER_RGB = (253, 250, 243)  # warm paper #FDFAF3
TALO_PAPER_HEX = "0xFDFAF3"
TALO_OLIVE_RGB = (43, 51, 41)     # dark olive #2B3329
TALO_OLIVE_HEX = "0x2B3329"
TALO_LOGO_PATH = ASSETS_DIR / "talo_logo.png"  # white "talo" on black, see assets/talo_logo.png
TALO_LOGO_WHITE_PATH = ASSETS_DIR / "talo_logo_white.png"
# Watermark / overlay
TALO_WATERMARK_ENABLED = True
TALO_WATERMARK_OPACITY = 0.035  # 0..1 subtle paper watermark (dark logo on paper, white on black) — tuned down from 0.045 for editorial subtlety
TALO_WATERMARK_SCALE = 0.72     # fraction of canvas width
TALO_WATERMARK_ROT = -6.0       # gentle editorial tilt
# Editorial paper grain (optional subtle texture overlay via drawing; off by default, enable per-beat)
TALO_PAPER_GRAIN_STRENGTH = 0

# ── Clep theme — lime + cream (from clep homepage hero) ───────────────────
# Lime is the hero accent, cream is the calm canvas, dark is near-black for contrast.
# Palette sampled from image: lime #D6FF3B, cream #F5F5F1, ink #111111, green serif #4A7A2A
CLEP_LIME_RGB = (214, 255, 59)    # #D6FF3B — CTA lime
CLEP_LIME_HEX = "0xD6FF3B"
CLEP_LIME_DEEP_RGB = (189, 225, 52)  # slightly deeper for borders
CLEP_CREAM_RGB = (249, 249, 245)  # #F9F9F5 — page bg
CLEP_CREAM_HEX = "0xF9F9F5"
CLEP_CREAM_DARK_RGB = (238, 238, 233)  # card border on cream
CLEP_INK_RGB = (17, 17, 17)       # near-black body
CLEP_GREEN_RGB = (74, 122, 42)    # #4A7A2A — italic serif "clean spreadsheet"
CLEP_GREEN_HEX = "0x4A7A2A"
CLEP_ORANGE_RGB = (255, 167, 56)  # flag highlight #FFA738
CLEP_RED_RGB = (231, 76, 60)      # error red #E74C3C
CLEP_MUTED_RGB = (112, 112, 108)  # muted gray for secondary
CLEP_LOGO_PATH = ASSETS_DIR / "clep_logo.png"  # optional: white clep on black, fallback to text
CLEP_LOGO_WHITE_PATH = ASSETS_DIR / "clep_logo_white.png"

MGFX_STYLES = {
    "talo": {  # Talo editorial: warm paper bg, near-black fg, periwinkle accent — the hero look
        "bg": TALO_PAPER_RGB,
        "fg": (10, 10, 11),
        "accent": TALO_ACCENT_RGB,
        "accent2": TALO_ACID_RGB,
        "muted": (112, 108, 103),   # warm muted
        "card_bg": (255, 255, 251),
        "card_border": (232, 227, 218),
        "pill_bg": (240, 241, 250),
        "pill_fg": (92, 102, 150),
    },
    "talo_dark": {  # black card for talo logo / problem statement (Image 2)
        "bg": (6, 6, 7),
        "fg": (255, 255, 251),
        "accent": TALO_ACCENT_RGB,
        "accent2": TALO_ACID_RGB,
        "muted": (150, 150, 155),
        "card_bg": (22, 22, 24),
        "card_border": (38, 38, 42),
        "pill_bg": (255, 255, 255),
        "pill_fg": (6, 6, 7),
    },
    "paper": {  # explicit alias to talo paper
        "bg": TALO_PAPER_RGB,
        "fg": (10, 10, 11),
        "accent": TALO_ACCENT_RGB,
        "accent2": TALO_ACID_RGB,
        "muted": (112, 108, 103),
        "card_bg": (255, 255, 251),
        "card_border": (232, 227, 218),
        "pill_bg": (240, 241, 250),
        "pill_fg": (92, 102, 150),
    },
    "olive": {
        "bg": TALO_OLIVE_RGB,
        "fg": (248, 248, 242),
        "accent": TALO_ACID_RGB,
        "accent2": TALO_ACCENT_RGB,
        "muted": (170, 175, 165),
        "card_bg": (58, 66, 56),
        "card_border": (85, 95, 82),
        "pill_bg": TALO_ACID_RGB,
        "pill_fg": TALO_OLIVE_RGB,
    },
    "clep": {  # Clep: cream canvas, ink fg, lime accent hero
        "bg": CLEP_CREAM_RGB,
        "fg": CLEP_INK_RGB,
        "accent": CLEP_LIME_RGB,
        "accent2": CLEP_GREEN_RGB,
        "muted": CLEP_MUTED_RGB,
        "card_bg": (255, 255, 255),
        "card_border": CLEP_CREAM_DARK_RGB,
        "pill_bg": CLEP_CREAM_RGB,
        "pill_fg": CLEP_INK_RGB,
        "lime": CLEP_LIME_RGB,
        "green": CLEP_GREEN_RGB,
        "orange": CLEP_ORANGE_RGB,
        "red": CLEP_RED_RGB,
    },
    "clep_lime": {  # lime wash for hero enter
        "bg": CLEP_LIME_RGB,
        "fg": CLEP_INK_RGB,
        "accent": CLEP_INK_RGB,
        "accent2": CLEP_GREEN_RGB,
        "muted": (85, 95, 55),
        "card_bg": (255, 255, 255),
        "card_border": CLEP_LIME_DEEP_RGB,
        "pill_bg": (255, 255, 255),
        "pill_fg": CLEP_INK_RGB,
    },
    "clep_dark": {
        "bg": (12, 12, 12),
        "fg": (249, 249, 245),
        "accent": CLEP_LIME_RGB,
        "accent2": CLEP_ORANGE_RGB,
        "muted": (150, 150, 150),
        "card_bg": (32, 32, 32),
        "card_border": (48, 48, 48),
        "pill_bg": CLEP_LIME_RGB,
        "pill_fg": (12, 12, 12),
    },
    "light": {  # default: black text on white (hero headlines)
        "bg": (255, 255, 255),
        "fg": (16, 16, 16),
        "accent": (1, 12, 202),      # vivid blue (Allore reference)
        "muted": (110, 110, 125),
        "card_bg": (245, 245, 250),
        "card_border": (230, 232, 245),
        "pill_bg": (240, 241, 255),
        "pill_fg": (1, 12, 202),
    },
    "blue": {  # full-blue bg for stat moments ($0)
        "bg": (1, 12, 202),
        "fg": (255, 255, 255),
        "accent": (255, 255, 255),
        "muted": (200, 210, 255),
        "card_bg": (255, 255, 255),
        "card_border": (255, 255, 255),
        "pill_bg": (255, 255, 255),
        "pill_fg": (1, 12, 202),
    },
    "gray": {  # light gray cards section
        "bg": (248, 248, 252),
        "fg": (16, 16, 16),
        "accent": (1, 12, 202),
        "muted": (110, 110, 125),
        "card_bg": (255, 255, 255),
        "card_border": (232, 234, 245),
        "pill_bg": (1, 12, 202),
        "pill_fg": (255, 255, 255),
    },
    # Keep dark/whiteboard for compatibility if planner emits them
    "dark": {
        "bg": (13, 13, 16),
        "ink": (242, 242, 236),
        "fg": (242, 242, 236),
        "accent": (255, 212, 0),
        "muted": (155, 155, 163),
        "card_bg": (30, 30, 35),
        "card_border": (50, 50, 55),
        "pill_bg": (255, 212, 0),
        "pill_fg": (13, 13, 16),
    },
    "whiteboard": {
        "bg": (247, 245, 240),
        "ink": (28, 28, 30),
        "fg": (28, 28, 30),
        "accent": (1, 12, 202),
        "muted": (125, 125, 130),
        "card_bg": (255, 255, 255),
        "card_border": (220, 220, 225),
        "pill_bg": (1, 12, 202),
        "pill_fg": (255, 255, 255),
    },
}
# aliases for legacy key names
GFX_STYLES = MGFX_STYLES  # compat with pipeline code that imports GFX_STYLES
GFX_DEFAULT_STYLE = "light"
MGFX_DEFAULT_STYLE = "talo"

GFX_SUPERSAMPLE = 2 if not _FOURK else 1
GFX_STROKE_W_FRAC = 0.004
GFX_NODE_FONTSIZE_FRAC = 0.07
GFX_STAT_FONTSIZE_FRAC = 0.14
GFX_CALLOUT_FONTSIZE_FRAC = 0.06
GFX_DRAW_FRACTION = 0.75
GFX_BOIL = False  # crisp for clean style; enable per-beat if hand-drawn boil wanted
GFX_BOIL_EVERY_N_FRAMES = 3
GFX_WOBBLE_AMP_FRAC = 0.0015

# Clean typography rendering scale — 4K at 2x = 7680x4320 (132MP/frame, slow); use 1 for native 4K, 2 for 1080p
MGFX_SUPERSAMPLE = 2 if not _FOURK else 1
MGFX_TITLE_FONTSIZE_FRAC = 0.078   # of canvas height (landscape tuned)
MGFX_BODY_FONTSIZE_FRAC = 0.042
MGFX_STAT_FONTSIZE_FRAC = 0.22     # for $0 big stat
MGFX_CAPTION_FONTSIZE_FRAC = 0.028

# ── Fonts ────────────────────────────────────────────────────────────────
import pathlib
_FONT_DIR = Path("/System/Library/Fonts/Supplemental")

def _find_font(candidates: list[str], fallback: str) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    # Search fallback dir for any ttf
    for p in _FONT_DIR.glob("*.ttf"):
        if fallback.lower() in p.name.lower():
            return str(p)
    return str(_FONT_DIR / "Arial.ttf")

FONT_BOLD = os.environ.get("PIPELINE_FONT_BOLD", _find_font(
    [str(_FONT_DIR / "Arial Bold.ttf"), "/System/Library/Fonts/Helvetica.ttc"], "Arial Bold"))
FONT_BLACK = os.environ.get("PIPELINE_FONT_BLACK", _find_font(
    [str(_FONT_DIR / "Arial Black.ttf")], "Arial Black"))
FONT_REGULAR = os.environ.get("PIPELINE_FONT_REGULAR", str(_FONT_DIR / "Arial.ttf"))
FONT_HELVETICA = os.environ.get("PIPELINE_FONT_HELVETICA", str(_FONT_DIR / "Helvetica.ttc"))
# Talo editorial serif italic (top line "Have work to do?") — Georgia Italic matches Image 1
FONT_SERIF_ITALIC = os.environ.get("PIPELINE_FONT_SERIF_ITALIC", _find_font(
    [str(_FONT_DIR / "Georgia Italic.ttf"), str(_FONT_DIR / "Times New Roman Italic.ttf"), str(_FONT_DIR / "Baskerville.ttc")], "Georgia Italic"))
FONT_SERIF = os.environ.get("PIPELINE_FONT_SERIF", _find_font(
    [str(_FONT_DIR / "Georgia.ttf"), str(_FONT_DIR / "Times New Roman.ttf")], "Georgia"))

# Try Inter / SF if available (cleaner geometric sans matching reference)
_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/SFNSDisplay-Bold.otf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Inter-Bold.ttf",
]
_FONT_CANDIDATES_BLACK = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Inter-Black.ttf",
]

CAPTION_FONT = FONT_BLACK
GFX_NODE_FONT = FONT_BOLD
GFX_STAT_FONT = FONT_BLACK

# Clean engine fonts — prefer geometric sans for reference match
MGFX_FONT_BOLD = FONT_BOLD
MGFX_FONT_BLACK = FONT_BLACK
MGFX_FONT_REGULAR = FONT_REGULAR
MGFX_FONT_SERIF_ITALIC = FONT_SERIF_ITALIC
MGFX_FONT_SERIF = FONT_SERIF
# Talo aliases
MGFX_FONT_TALO_SERIF_ITALIC = FONT_SERIF_ITALIC
MGFX_FONT_TALO_SANS_BOLD = FONT_BOLD
MGFX_FONT_TALO_SANS_BLACK = FONT_BLACK

# ── Text style presets (kept for compat; motion-only uses fullscreen layouts) ──
TEXT_STYLES = {
    "headline": {
        "font": FONT_BLACK,
        "fontsize": 84 if not _PORTRAIT else 72,
        "fontcolor": "black",
        "box": False,
        "boxcolor": "white@0",
        "boxborderw": 0,
        "line_spacing": 10,
        "default_position": "center",
    },
    "stat": {
        "font": FONT_BLACK,
        "fontsize": 140 if not _PORTRAIT else 110,
        "fontcolor": "0x010CCA",
        "box": False,
        "boxcolor": "white@0",
        "boxborderw": 0,
        "line_spacing": 6,
        "default_position": "center",
    },
    "label": {
        "font": FONT_BOLD,
        "fontsize": 48 if not _PORTRAIT else 40,
        "fontcolor": "black",
        "box": False,
        "boxcolor": "white@0",
        "boxborderw": 0,
        "line_spacing": 6,
        "default_position": "center",
    },
}

TEXT_FADE_DURATION = 0.30
TEXT_SLIDE_PX = 24
TEXT_MIN_FONTSIZE = 28
TEXT_SAFE_MARGIN_PX = 80 if not _PORTRAIT else 48

# ── Speaking pace ────────────────────────────────────────────────────────
AVG_WPM = 150

# ── Reel direction ───────────────────────────────────────────────────────
TARGET_DURATION_SECONDS = float(os.environ.get("PIPELINE_TARGET_SECONDS", "30"))
CLIPS_DIR = PROJECT_ROOT / "assets" / "videos"
HOOK_FADE_IN_SECONDS = 0.35
PAYOFF_FADE_OUT_SECONDS = 0.55
HOOK_TEXT_SCALE = 1.10
PACE_MIN, PACE_MAX = 0.9, 1.15

# ── LLM ─────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
PLANNER_MODEL = os.environ.get("PIPELINE_PLANNER_MODEL", "qwen/qwen3.7-flash")
VISION_MODEL = os.environ.get("PIPELINE_VISION_MODEL", "qwen/qwen3.7-flash")

# ── Vision selection (optional for image beats) ──────────────────────────
SELECT_QUALITY_FLOOR = 7.5
SELECT_REFETCH_ROUNDS = 2
SELECT_MAX_CANDIDATES = 4
SELECT_WORKERS = 4
VISION_CALL_SPACING_SECONDS = float(os.environ.get("PIPELINE_VISION_CALL_SPACING", "1"))

# ── Pinterest ────────────────────────────────────────────────────────────
ALLORE_REPO_ROOT = PROJECT_ROOT.parent
PINTEREST_CLI_SCRIPT = ALLORE_REPO_ROOT / "scripts" / "pinterest-search-cli.ts"

# ── ffmpeg ───────────────────────────────────────────────────────────────
FFMPEG_BIN = os.environ.get("PIPELINE_FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE_BIN = os.environ.get("PIPELINE_FFPROBE_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
