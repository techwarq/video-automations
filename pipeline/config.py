"""
Central configuration for the explainer-video pipeline.
Canvas size, zone rects, font paths, and text style presets live here so
every stage (compositor, prep_images, planner) reads the same numbers.
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
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FPS = 30

# ── Zones ────────────────────────────────────────────────────────────────
# (x, y, w, h) rects within the 1080x1920 canvas.
# Top zone: motion graphics (images, text). Bottom zone: talking head.
TOP_ZONE = (0, 0, 1080, 850)
BOTTOM_ZONE = (0, 850, 1080, 1070)

# Inset the image "card" inside the top zone so it reads as floating,
# not edge-to-edge — leaves room for the border/shadow treatment.
TOP_ZONE_CARD_MARGIN = 40

# Cameras commonly settle auto-exposure/white-balance during the first
# fraction of a second of a recording (a visible brightness/color-temp
# jump between the very first frames and the rest of the clip). Skip past
# it before using the talking-head footage — otherwise a forward/reverse
# loop of the footage plays that jump twice per cycle.
TALKING_HEAD_TRIM_START_SECONDS = 0.5

# ── Canvas background / "card" treatment ────────────────────────────────
# Dark brand background with a subtle dot-grid texture behind the top-zone
# card, per the Vox/kinetic-explainer reference look.
CANVAS_BG_COLOR = "0x0D0D10"          # ffmpeg color spec (hex, 0x-prefixed)
CANVAS_BG_COLOR_RGB = (13, 13, 16)     # same color, as an (r,g,b) tuple for Pillow
CANVAS_GRID_DOT_RGB = (32, 32, 38)     # faint — barely visible, texture not decoration
CANVAS_GRID_SPACING_PX = 48

CARD_BORDER_PX = 6
CARD_BORDER_COLOR = "white@0.9"
CARD_SHADOW_OFFSET_PX = 18
CARD_SHADOW_COLOR = "black@0.6"
CARD_SHADOW_BLUR = 20

# ── Motion / transitions ────────────────────────────────────────────────
# Ken Burns zoom range applied over a beat's duration. Kept subtle/slow.
KEN_BURNS_ZOOM_START = 1.0
KEN_BURNS_ZOOM_END = 1.15
KEN_BURNS_PAN_FRACTION = 0.12  # fraction of image width/height panned across
# Ease the Ken Burns motion (cubic ease-in-out) instead of linear gliding —
# every NLE eases keyframes by default; linear moves read as "automated".
KEN_BURNS_EASED = True

CROSSFADE_DURATION = 0.25  # seconds, fallback default duration for transitions

# Per-beat transitions between cards. planner.assign_transitions() writes a
# {"type": str, "duration": float} onto every beat except the last; anything
# xfade accepts is valid here (verified against ffmpeg-full 9.x).
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
# "cut" is special-cased in compositor as a 2-frame fade — reads as a hard cut.
TRANSITION_CUT_AS_FADE_SECONDS = 0.07

# ── Final grade / finishing look ─────────────────────────────────────────
# One global look applied over the finished composite (images AND talking head)
# so everything shares a single "grade", the way a Resolve timeline gets one.
# Disable any piece by zeroing/flipping its flag.
GRADE_ENABLED = True
GRADE_CONTRAST = 1.06
GRADE_SATURATION = 1.09
GRADE_GAMMA = 1.015
# Gentle filmic S-curve (in/out luma pairs).
GRADE_CURVES_POINTS = "0/0 0.25/0.215 0.5/0.5 0.75/0.79 1/1"
# Teal-orange split tone: cool shadows, warm highlights (subtle).
GRADE_SHADOWS_RS, GRADE_SHADOWS_BS = -0.04, 0.06
GRADE_MIDTONES_RM, GRADE_MIDTONES_BM = 0.02, -0.03
GRADE_HIGHLIGHTS_RH, GRADE_HIGHLIGHTS_BH = 0.04, -0.05
# If a .cube LUT exists here it REPLACES the eq/curves/colorbalance stack.
GRADE_LUT_PATH = ASSETS_DIR / "grade.cube"

BLOOM_ENABLED = True       # halation: blurred copy screened back over the frame
BLOOM_SIGMA = 14
BLOOM_OPACITY = 0.13
FILM_GRAIN_STRENGTH = 5    # noise=alls strength; 0 disables
VIGNETTE_ANGLE = "PI/6"

# ── Word-synced captions (kinetic typography of the spoken script) ──────
CAPTIONS_ENABLED = True
CAPTION_WORDS_PER_GROUP = 3        # classic short-form caption grouping
CAPTION_FONTSIZE = 56
CAPTION_MIN_FONTSIZE = 38          # auto-shrink floor before giving up on fit
CAPTION_TEXT_COLOR = "white"
CAPTION_ACCENT_COLOR = "0xFFD400"  # groups containing numbers pop in this color
CAPTION_BORDER_COLOR = "black"
CAPTION_BORDER_W = 7
CAPTION_SHADOW_COLOR = "black@0.45"
CAPTION_SHADOW_OFFSET_PX = 4
CAPTION_MAX_WIDTH_FRAC = 0.88      # of canvas width
CAPTION_Y_CENTER_FRACTION = 0.80   # vertical center of the caption block (full canvas)
CAPTION_FADE_IN = 0.14             # seconds
CAPTION_SETTLE_PX = 16             # upward settle distance on entry
CAPTIONS_UPPERCASE = True

# ── Audio post chain ─────────────────────────────────────────────────────
# Narration is mastered, not passed through raw: HPF rumble guard, gentle
# compression, de-essing, then EBU R128 loudness normalization to the
# -14 LUFS short-form platform target.
AUDIO_CHAIN_ENABLED = True
AUDIO_HIGHPASS_HZ = 75
AUDIO_COMPRESSOR = "threshold=-20dB:ratio=2.5:attack=10:release=150:makeup=3"
AUDIO_DEESSER = "i=0.5:m=0.5:f=0.5"
AUDIO_LOUDNORM = "I=-14:TP=-1.5:LRA=11"
AUDIO_TARGET_RATE = 48000

# Optional music bed (set PIPELINE_MUSIC_PATH or --music). Looped under the
# narration and sidechain-ducked whenever the voice is active.
MUSIC_PATH = os.environ.get("PIPELINE_MUSIC_PATH")
MUSIC_VOLUME = 0.32
MUSIC_DUCK = "threshold=0.02:ratio=6:attack=25:release=350"

# ── Motion-graphics engine (animated whiteboard/dark-board diagrams) ────
# Beats with visual_kind="diagram" skip photos entirely: motion_gfx.py
# renders a hand-drawn explainer clip (boxes, arrows, labels that draw
# themselves on) which compositor uses as the card content.
GFX_STYLES = {
    "dark": {  # white ink on the brand dark background (matches the canvas)
        "bg": (13, 13, 16),
        "ink": (242, 242, 236),
        "accent": (255, 212, 0),
        "secondary": (155, 155, 163),
    },
    "whiteboard": {  # dark ink on off-white board
        "bg": (247, 245, 240),
        "ink": (28, 28, 30),
        "accent": (204, 62, 44),
        "secondary": (125, 125, 130),
    },
}
GFX_DEFAULT_STYLE = "dark"
GFX_SUPERSAMPLE = 2          # render at Nx and downscale (PIL lines are not anti-aliased)
GFX_STROKE_W_FRAC = 0.0055   # stroke width as fraction of card width
GFX_NODE_FONTSIZE_FRAC = 0.085   # of card height
GFX_STAT_FONTSIZE_FRAC = 0.16    # for callouts with big=true
GFX_CALLOUT_FONTSIZE_FRAC = 0.07
GFX_DRAW_FRACTION = 0.82     # fraction of the beat spent drawing; rest is hold
GFX_BOIL = True              # re-jitter wobble every N frames (hand-drawn "boil")
GFX_BOIL_EVERY_N_FRAMES = 3
GFX_WOBBLE_AMP_FRAC = 0.0022 # line wobble amplitude as fraction of card width

# ── Fonts ────────────────────────────────────────────────────────────────
_FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONT_BOLD = os.environ.get("PIPELINE_FONT_BOLD", str(_FONT_DIR / "Arial Bold.ttf"))
FONT_BLACK = os.environ.get("PIPELINE_FONT_BLACK", str(_FONT_DIR / "Arial Black.ttf"))
FONT_REGULAR = os.environ.get("PIPELINE_FONT_REGULAR", str(_FONT_DIR / "Arial.ttf"))

CAPTION_FONT = FONT_BLACK  # captions use the heaviest cut for legibility

GFX_NODE_FONT = FONT_BOLD       # motion-gfx node/label font
GFX_STAT_FONT = FONT_BLACK      # motion-gfx big callout font

# ── Text style presets ──────────────────────────────────────────────────
# Consumed by compositor.py's drawtext builder. Sizes tuned for a 1080-wide
# canvas viewed on a phone.
TEXT_STYLES = {
    "headline": {
        "font": FONT_BLACK,
        "fontsize": 72,
        "fontcolor": "white",
        "box": True,
        "boxcolor": "black@0.55",
        "boxborderw": 24,
        "line_spacing": 8,
        "default_position": "center",  # centered in top zone
    },
    "stat": {
        "font": FONT_BLACK,
        "fontsize": 110,
        "fontcolor": "white",
        "box": True,
        "boxcolor": "black@0.55",
        "boxborderw": 28,
        "line_spacing": 4,
        "default_position": "center",
    },
    "label": {
        "font": FONT_BOLD,
        "fontsize": 40,
        "fontcolor": "white",
        "box": True,
        "boxcolor": "black@0.5",
        "boxborderw": 14,
        "line_spacing": 4,
        "default_position": "bottom-left",  # bottom-left of top zone by default
    },
}

TEXT_FADE_DURATION = 0.35  # seconds, fade in/out
TEXT_SLIDE_PX = 30  # slight slide-in distance on entry
TEXT_MIN_FONTSIZE = 32  # floor for auto-shrink before falling back to line-wrap
TEXT_SAFE_MARGIN_PX = 48  # kept clear on each side of the card when fitting text

# ── Speaking pace (for planner's start/end estimate) ────────────────────
AVG_WPM = 150

# ── Reel direction (planner v2: "AI director") ───────────────────────────
# The planner plans the whole edit — hook, scenes, seconds, assets,
# diagrams, transitions — then the pipeline executes deterministically.
TARGET_DURATION_SECONDS = float(os.environ.get("PIPELINE_TARGET_SECONDS", "30"))
# Local library of screen-recording / product clips the planner can schedule
# into the top zone (visual_kind="video_clip"). Files are matched by filename
# keywords against the beat's video_query. Drop clips here, e.g.
#   assets/videos/allore_editor_demo.mp4
CLIPS_DIR = PROJECT_ROOT / "assets" / "videos"
# Reels grammar: hook opens hard, payoff closes clean.
HOOK_FADE_IN_SECONDS = 0.45      # video fades up from black under the hook
PAYOFF_FADE_OUT_SECONDS = 0.6    # closing fade to black on the payoff beat
HOOK_TEXT_SCALE = 1.12           # hook on_screen_text renders this much bigger
PACE_MIN, PACE_MAX = 0.9, 1.15   # how far the planner may stretch/squeeze a beat

# ── OpenRouter (LLM for planning + vision selection) ─────────────────────
# Qwen3.7 Flash is a text+image+video model, so one model/one API covers
# both planner.py (text) and select_images.py (vision).
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
PLANNER_MODEL = os.environ.get("PIPELINE_PLANNER_MODEL", "qwen/qwen3.7-flash")
VISION_MODEL = os.environ.get("PIPELINE_VISION_MODEL", "qwen/qwen3.7-flash")

# ── Vision-driven image quality loop (select_images) ────────────────────
# The vision model scores each candidate pool; if even the winner is weak it
# proposes a refined image_query and we re-search Pinterest with it before
# settling — bad pools self-heal instead of shipping a mediocre still.
SELECT_QUALITY_FLOOR = 7.5  # 0-10; winner below this triggers the refetch loop
SELECT_REFETCH_ROUNDS = 2   # max extra Pinterest searches per beat
SELECT_MAX_CANDIDATES = 4   # images sent per vision call (reasoning cost scales with count)
SELECT_WORKERS = 4          # parallel vision calls across beats
# Small gap between beats as a defensive margin against provider-side rate
# limits (the earlier empty-response failures turned out to be truncated
# reasoning output from too-low max_tokens, not this — but cheap to keep).
VISION_CALL_SPACING_SECONDS = float(os.environ.get("PIPELINE_VISION_CALL_SPACING", "1"))

# ── Pinterest image source ──────────────────────────────────────────────
# Real Pinterest scraping (PinterestBrowserService, Stagehand LOCAL mode)
# only works as a plain Node process — it needs to spawn a real Chrome and
# read process.env directly, neither of which the Cloudflare Workers
# runtime (workerd) supports, even under `wrangler dev`. So instead of
# calling the Worker's HTTP route, this shells out to a standalone TS
# script that runs outside the Worker. See image_source.py.
ALLORE_REPO_ROOT = PROJECT_ROOT.parent
PINTEREST_CLI_SCRIPT = ALLORE_REPO_ROOT / "scripts" / "pinterest-search-cli.ts"

# ── ffmpeg binaries ──────────────────────────────────────────────────────
# The plain Homebrew `ffmpeg` formula ships without libfreetype/libfontconfig,
# so it has no `drawtext` filter. `ffmpeg-full` (also via Homebrew) has it but
# installs keg-only, so it's not on PATH by default — point at it explicitly
# rather than requiring a global PATH/shell-rc change.
FFMPEG_BIN = os.environ.get("PIPELINE_FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE_BIN = os.environ.get("PIPELINE_FFPROBE_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
