# Pipeline Motion — Pure Motion Graphics (no talking head)

Clean kinetic typography pipeline matching the reference video (`/Users/sonalinayak/Downloads/ncADtJ_qYhuQxmjD.mp4`):

- 1920×1080 landscape (default, 16:9) — switch to 1080×1920 portrait with `--portrait` or `PIPELINE_MOTION_PORTRAIT=1`
- White background + vivid blue `#010CCA` (sampled from reference), bold sans
- Fullscreen beats — no top/bottom zones, no Pinterest fetch needed
- 7 layout templates: `headline`, `search_typing`, `badge_cards`, `stat_blue`, `grid_cards`, `bars`, `diagram` (clean)
- xfade transitions, silent or narration audio, optional captions/grade
- Reuses same LLM planner + ffmpeg stack as `pipeline/` but for typography/UI mocks

```
pipeline/              — talking-head explainer (9:16, top card + bottom video)
pipeline_motion/       — THIS ONE — pure motion graphics (16:9 or 9:16, fullscreen)
```

## Quick start (no API key needed — uses reference beats)

```bash
# render the reference-style demo (7 beats, 35s, 1920x1080)
cp beats_reference.json beats.json
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt   # or reuse ../pipeline/venv

# silent demo from the canned beats (no LLM)
python main.py --script script_reference.txt --out output/reference_demo.mp4 --skip-planner

# with your own narration audio
python main.py --script script_reference.txt --audio narration.mp3 --out output/with_audio.mp4 --skip-planner
```

## From a new script (requires OPENROUTER_API_KEY)

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
echo "We give your agent search and fetch 100% free. No subscriptions. No quotas. Ask anything, get real sources in seconds. One search, four pages, zero dollars." > my_script.txt

# landscape (default 1920x1080)
python main.py --script my_script.txt --out output/my_video.mp4

# portrait reel (1080x1920)
python main.py --script my_script.txt --out output/my_vertical.mp4 --portrait

# with music bed, captions, custom target duration
python main.py --script my_script.txt --out output/with_music.mp4 --music /path/to/bed.mp3 --captions --target-seconds 28
```

## How it works

1. **planner.py** — single LLM call (Qwen3.7 Flash via OpenRouter) turns the full script into `beats.json`. Each beat is a `visual_kind="kinetic"` with a `kinetic: {style, layout, ...}` spec. Prompt guides hook/body/payoff structure and the 7 kinetic layouts.
2. **motion_gfx.py** — renders each beat as a fullscreen H.264 clip via PIL at 2× supersample + ffmpeg pipe. Clean sans, no wobble, eased fade/slide + typewriter. Cached by `kinetic|size|duration` hash in `cache/beat_{id}/`.
3. **compositor.py** — chains the clips with per-beat `xfade` transitions, adds optional captions/grade, and muxes narration audio (mastered to -14 LUFS with sidechain-ducked music if provided) or silent `anullsrc`. Output is 1080p, `libx264` CRF20, `aac` 192k.

Image/video_clip beats still work (scaled to fullscreen, no Ken Burns), but the default is kinetic so no Pinterest fetch is needed. If a beats.json contains stray `image` beats without `prepped_image_path`, they auto-downgrade to a headline fallback.

## Layouts

| layout | style | spec keys | reference frame |
|---|---|---|---|
| headline | light | `text` (+ `\n` for 2 lines), `accent` (blue substring) | f1: "We give your agent search & fetch 100% FREE." |
| search_typing | light | `query` | f5: "What happened in AI t\|" typing |
| badge_cards | light | `badge`, `cards:[{source,title,time}]` | f8, f18: "Searching live news …" + 2 cards |
| stat_blue | blue | `stat`, `check`, `lines` | f3: "$0 ✓ No subscriptions. No quotas." |
| grid_cards | gray | `count`, `pill` | f18 bottom: 4 skeletons + pill "1 search · 4 pages · $0.00" |
| bars | light/gray | `count` | f2: 4 horizontal bars (transition filler) |
| diagram | light/blue/gray | `diagram:{nodes,arrows,callouts}` | clean system flow (QUERY→SEARCH→FETCH→ANSWER) |

Styles: `light` (white bg, black fg, blue accent), `blue` (blue bg, white fg), `gray` (light gray bg). Also accepts `dark`/`whiteboard` for compat, rendered crisp (no hand-drawn wobble).

## Config

`config.py` centralizes canvas, palette, transitions, grade, audio, LLM, and font paths. Override via env:

- `PIPELINE_MOTION_PORTRAIT=1` → 1080×1920 portrait
- `PIPELINE_TARGET_SECONDS=35` → planner target duration
- `PIPELINE_FFMPEG_BIN` / `PIPELINE_FFPROBE_BIN` → custom ffmpeg
- `PIPELINE_FONT_BOLD` etc. → custom fonts
- `OPENROUTER_API_KEY`, `PLANNER_MODEL` → LLM

## Files

```
pipeline_motion/
  config.py          # canvas, palette, styles, transitions, audio, LLM
  motion_gfx.py      # PIL→ffmpeg kinetic renderer (headline, search, badge, stat, grid, bars, diagram)
  planner.py         # LLM director tuned for kinetic layouts
  compositor.py      # fullscreen xfade chain + audio mastering
  captions.py        # word-synced caption groups (off by default for motion-only)
  main.py            # CLI ( --script, --audio?, --out, --portrait, --captions, --music, --skip-planner)
  beats_reference.json  # 7-beat canned demo matching the reference video
  script_reference.txt  # transcript for the demo
  output/reference_demo.mp4  # pre-rendered 35s demo (1920x1080)
  cache/             # per-beat mgfx clips (hashed)
  assets/videos/     # optional local screen-record clips for video_clip beats
```

## Inside `pipeline/` (talking-head) vs `pipeline_motion/` (this)

| | pipeline/ | pipeline_motion/ |
|---|---|---|
| canvas | 1080×1920 9:16 | 1920×1080 16:9 (or 1080×1920 with --portrait) |
| zones | top card + bottom talking head (ping-pong loop) | fullscreen, no zones |
| visuals | Pinterest images + hand-drawn diagrams + video clips | kinetic typography/UI mocks + clean diagrams |
| audio | talking-head video audio mastered | provided mp3/wav or silent |
| grade | filmic S-curve + bloom + grain (on) | clean, grade off by default |
| captions | on by default (word-synced) | off by default (typography is the caption) |

## Wrapper inside `pipeline/`

For the "so here make another one" request, a thin wrapper exists at:

```
pipeline/pure_motion/   → symlink/mirror of pipeline_motion for `cd pipeline && python pure_motion/main.py ...`
pipeline/motion_only/
```

Both point to the same engine; use whichever path you prefer.

## Tips

- For product promos, keep the hook as `headline` with a blue accent (`100% FREE.`, `$0`, `1 search · 4 pages`) — it stops the scroll.
- One `stat_blue` beat per video is enough; overusing blue dilutes punch.
- `search_typing` works best with a short query (≤32 chars); longer queries auto-truncate-wrap.
- For vertical reels, the same beats auto-fit 1080×1920 (font sizes scale). Test with `--portrait`.
- To add a real screen recording, set `visual_kind:"video_clip"` + `video_query:"editor demo"` and drop the clip in `assets/videos/`.
