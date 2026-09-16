# @clep/sdk — video layer for software products

Instrument once, generate 2–5s feature clips without manual recording.

## 1. Instrument (30 seconds)

Vanilla:

```html
<script src="@clep/sdk/clep.js" data-registry="http://localhost:8787/features"></script>
<button data-clep="ai-research" data-clep-action="primary">Start Research</button>
```

React:

```tsx
import { Clep } from "@clep/react";

<Clep name="ai-research">
  <ResearchButton />
</Clep>
```

Optional semantics:

```html
<div data-clep="generate-report" data-clep-state="result">…</div>
<button data-clep="generate-report" data-clep-action="primary">Generate</button>
```

The SDK registers per feature: `name → DOM rect (normalized) → component path → interactions → states`.
Inspect live: `window.__CLEP__.features()`, export: `window.__CLEP__.download("ai-research")`.

## 2. Feature file (what the renderer consumes)

`window.__CLEP__.export("ai-research")` produces `ai-research.feature.json`:

```json
{
  "name": "ai-research",
  "title": "AI Research",
  "url": "https://acme.ai/dashboard",
  "viewport": { "w": 1440, "h": 900 },
  "element": { "x": 0.2, "y": 0.35, "w": 0.6, "h": 0.3 },
  "interactions": [
    { "type": "click", "target": "primary" },
    { "type": "input", "target": "search" },
    { "type": "submit" },
    { "type": "loading" },
    { "type": "result" }
  ],
  "states": ["empty", "generating", "completed"],
  "steps": [
    { "type": "focus", "label": "Search input" },
    { "type": "type", "text": "AI browser agents" },
    { "type": "click", "target": "Research" },
    { "type": "loading", "label": "Research running" },
    { "type": "result", "label": "Sources appear" }
  ]
}
```

`steps` is what the demo ships with — the SDK fills `interactions`/`states` live,
and `pipeline_clep` converts either into a storyboard. Hand-write `steps` for the MVP.

## 3. Generate clip

```bash
python allore.py --mode clep --feature pipeline_clep/demo/ai-research.feature.json --out out.mp4
python allore.py --mode clep --feature pipeline_clep/demo/generate-report.feature.json \
  --aspect 9:16 --style cinematic --duration 3 --out out-portrait.mp4
```
