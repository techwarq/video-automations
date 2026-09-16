---
name: clep
description: Turn a plain-English request ("make a clip of the signup flow", "record the AI research demo") into a rendered product video. Instruments data-clep attributes if the feature isn't marked up yet, then scans and renders via the hosted Clep backend. Use whenever the user asks for a demo clip, product video, or to record/clip/capture a feature — this is the only Clep command needed, don't ask the user to run a separate instrument step first.
version: 0.2.0
---

# Clep — one command, feature request to MP4

The user describes a flow in plain English. You figure out the rest: check
if it's already markup'd, instrument it if not, render it, report back where
the video landed. Never make the user invoke a separate step manually.

## 0. Read the ask

Extract from the user's request:
- **App URL** — if not given, ask, or infer from a dev server already running
  in this repo (e.g. `localhost:3000`).
- **Feature** — what flow/element (kebab-case a short name, e.g. "signup
  flow" → `signup`).
- **What to type/click** (optional) — becomes `--query` or a steps plan.
- **Look** (optional) — style/fps/quality; default `saas` / 60fps / 1080p.

Backend reachability: `http://localhost`/`127.0.0.1` in the URL means the
*backend's* localhost, not necessarily the user's terminal — fine when both
run on the same machine (local dev), otherwise needs a public URL. If
`CLEP_API_URL` isn't set, assume the default `http://127.0.0.1:8787`. If the
backend requires `CLEP_API_KEY` (401 from any call) and it's not set, stop and
tell the user to get it from the dashboard → API Keys. Don't invent keys.

## 1. Check instrumentation — scan first, always

```bash
${CLAUDE_PLUGIN_ROOT}/bin/clep scan <url>
```

If the feature name (or something clearly matching what the user described)
is already in the output, skip straight to step 3 (render).

## 2. Instrument, only if missing

No npm SDK — plain HTML attributes, found by the platform's Playwright agent.

```html
<button data-clep="ai-research" data-clep-action="primary">Start Research</button>
<div data-clep="ai-research" data-clep-state="result">…</div>
```

```tsx
<div data-clep="demo-convert" data-clep-state={phase}>
  <button data-clep-action="primary" onClick={run}>Convert</button>
  <input data-clep-action="input" placeholder="Search…" />
</div>
```

Rules:

1. `data-clep="feature-name"` (kebab-case) scopes the feature. All nodes
   sharing a name merge into one feature (union bbox).
2. `data-clep-action="primary"` marks the main button. `action:<name>` marks
   secondary buttons (e.g. `data-clep-action="export"`).
3. `data-clep-state="<state>"` marks states the camera waits for (`empty`,
   `generating`, `completed`, `exported`, …). Put it on the root AND any inner
   node that changes — the agent watches the whole subtree.
4. Keep one visible text input per feature when the flow needs typing (skip
   `type="file"` / `type="hidden"`).
5. Don't restyle to accommodate Clep. Attributes only, minimal diff.

Workflow: read the component files, locate input + primary button + result
states, reuse the existing state variable for `data-clep-state`. Then
re-run `clep scan <url>` to confirm the name now appears — if it doesn't, the
selector isn't rendering (conditional render, wrong route, auth wall) and
that has to be fixed before rendering.

**Multi-step flows** (type → click → wait → click export): build a steps plan
instead of relying on the auto arc.

```json
[
  { "action": "type", "target": "input", "value": "AI browser agents" },
  { "action": "click", "target": "primary" },
  { "action": "wait", "for": "state:completed", "timeout": 6 },
  { "action": "click", "target": "action:export" },
  { "action": "wait", "for": "state:exported", "timeout": 4 }
]
```

Targets resolve inside `[data-clep=name]`: `input`, `primary`,
`action:<name>`, `text:<label>`, `file` (upload only), or any CSS selector.

## 3. Render

```bash
${CLAUDE_PLUGIN_ROOT}/bin/clep clip \
  --url <url> --name <feature-name> \
  --query "<text to type, if any>" \
  --style saas --fps 60 --quality 1080p \
  --wait
```

Omit `--query`/`--steps-file` for the auto arc (type → click → wait-for-change).
Use `--steps-file plan.json` for the chained flow built in step 2. Add
`--out <path>` only if the user asked for a local file — otherwise the video
already lands in the platform UI and that's enough.

`--wait` polls until `done`/`error` and prints the MP4 URL. Job states:
`queued → recording → editing → done | error`. On `error`, surface the
backend message verbatim (usually: feature not found at URL, a `wait` state
timed out, or the URL is unreachable from the backend) and suggest the fix.

## 4. Report back

Tell the user, in one short message:
- The video is live in the dashboard (`<CLEP_API_URL>/`, e.g.
  `http://127.0.0.1:8787/`) — it shows up under "Clip jobs" with an inline
  player and a Download MP4 link, no action needed.
- If `--out` was used, the local file path too.
- Style/fps/quality used, so they know what to ask for differently next time.

Re-renders are one command — never re-record by hand. Output is a 2–5s 16:9
MP4, gradient backdrop + floating window + auto-zoom + custom cursor + click
ripple by default.
