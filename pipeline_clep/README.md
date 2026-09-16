# pipeline_clep — video layer for software products

One `data-clep` in → beautiful product clip out. No manual recording.

```
sdk/clep.js  ──data-clep──▶  agent  ──records──▶  raw webm + trace.json
 (instrument)   (Playwright: drives   ──polish──▶  16:9 MP4, 60fps
                 focus/type/click)                 (gradient backdrop,
                                                   floating window,
                                                   auto-zoom, cursor)
```

Single-pass streaming: Playwright webm → decode pipe → Pillow edit →
encode pipe. No intermediate files.

## Quickstart (agent — real app, 16:9)

```bash
# 1. instrument once: <button data-clep="ai-research"> (see sdk/README.md)
# 2. list what's instrumented (the Feature Registry):
python allore.py --mode clep --url https://acme.ai/dashboard

# 3. record the live feature + edit the recording, one command:
python allore.py --mode clep --url https://acme.ai/dashboard \
  --name ai-research --out pipeline_clep/output/ai.mp4
```

What the agent does: finds `[data-clep]`, focuses the input, types the
query (explicit `--query`, else SDK/input value), clicks the primary button,
waits for a real state/DOM change, logs cursor/click/bbox per step.
What polish does: the app window floats on a gradient canvas — wide on the
backdrop at open, eased push into each action (one key per focus/click),
ripple on every click, pull back out to close. Custom cursor (native hidden
at record time). 60fps by default (`--fps 30` for smaller files).

## Platform (dashboard — see features, Make Clip)

```bash
python platform/server.py --port 8787
# open http://127.0.0.1:8787
```

Scan an app URL → feature cards → **Make Clip** (query override, style,
optional steps JSON) → job records + edits → preview + download.
The SDK reports live too:

```html
<script src="@clep/sdk/clep.js" data-registry="http://localhost:8787/api/ingest"></script>
```

API: `GET /api/features?url=` · `GET /api/registry` · `POST /api/ingest` ·
`POST /api/clips {url, name, query?, style?, steps?}` · `GET /api/jobs[/<id>]`.

## Multi-step features (chained clicks across states)

Pass a steps plan (dashboard textarea, `--steps-file`, or API `steps`):

```json
[
  { "action": "type", "target": "input", "value": "AI browser agents" },
  { "action": "click", "target": "primary" },
  { "action": "wait", "for": "state:completed", "timeout": 6 },
  { "action": "click", "target": "action:export" },
  { "action": "wait", "for": "state:exported", "timeout": 4 }
]
```

Targets resolve inside `data-clep=name`: `input` · `primary` ·
`action:<name>` · `text:<label>` · `file` (upload only) · any CSS selector.
Waits: `change` (any state/DOM/text shift) · `state:<value>` · `time:<sec>`.
Upload: `{"action":"upload","target":"file","file":"demo/x.pdf"}` drives a
real OS-level file pick into the scoped file input (paths resolve against
the repo root). Omit steps for the auto arc (type → click → wait). The camera keys one
eased move per focus/click and every click gets its own ripple.
Example: `demo/multistep.example.json`.

## Synthetic fallback (no browser)

Hand-write `demo/*.feature.json` (or SDK-export it) and render the UI mock:

```bash
python allore.py --mode clep --feature pipeline_clep/demo/ai-research.feature.json \
  --out pipeline_clep/output/ai-research.mp4
```

Flags: `--style {minimal,saas,cinematic,apple}` `--duration 2–12` `--fps {30,60}`
`--quality {720p,1080p}` (default 1080p → 1920×1080).

## Tested on the real Clep app

`demo/clep-app-flow.json` drives the live DemoWidget (tab → upload a real
PDF → conversion states → CSV download) with two attributes placed in the
app — exactly what `<Clep>` renders:

```tsx
<div data-clep="demo-convert" data-clep-state={phase}>…</div>
```

```bash
python allore.py --mode clep --url http://127.0.0.1:3000/ --name demo-convert \
  --steps-file pipeline_clep/demo/clep-app-flow.json --quality 1080p \
  --out pipeline_clep/output/clep-app-1080p.mp4
```

## Instrument a real app

```html
<script src="sdk/clep.js"></script>
<button data-clep="ai-research" data-clep-action="primary">Research</button>
```

Then in devtools: `window.__CLEP__.download("ai-research")` → save as
`demo/<name>.feature.json` → render. See `sdk/README.md`. Live example:
the real Clep app's DemoWidget carries `data-clep="demo-convert"`
(`components/DemoWidget.tsx`) — scan it, Make Clip, done.

## From a live URL (v1, optional)

```bash
pipeline/venv/bin/pip install playwright && pipeline/venv/bin/playwright install chromium
python pipeline_clep/recorder.py --url https://acme.ai/dashboard --name ai-research \
  --out-feature pipeline_clep/demo/ai-research.feature.json \
  --out-shot pipeline_clep/demo/ai-research.png
```

Records the SDK bbox + interaction replay into feature.json; with `--out-shot`
the renderer camera-tours your real pixels instead of the synthetic mock.
Without Playwright the synthetic path (zero extra deps) always works.

## Files

| file | role |
|---|---|
| `agent.py` | Playwright cameraman: `discover()` registry + `record()` raw webm + trace.json |
| `polish.py` | Screen-Studio edit of the raw capture (camera, cursor, ripple, ring, pill) |
| `config.py` | canvas, style palettes, ffmpeg/fonts |
| `storyboard.py` | phase timing + camera/cursor plan (synthetic path + polish timing base) |
| `renderer.py` | synthetic UI mock + shared cursor/pill drawing |
| `recorder.py` | one-shot stills capture → screenshot for the synthetic base (optional) |
| `main.py` | CLI (`run(feature, out, ..., url, name, query)`) |
| `demo/` | instrumented `index.html` demo page + 2 hand-written feature.jsons |
