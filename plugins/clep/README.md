# clep — Claude Code plugin

Video layer for software products. Claude instruments your app with
`data-clep` attributes, the hosted backend drives the live feature in Chromium
and edits the capture into a 1080p60 product clip.

No npm SDK to install. No local pipeline. Just the plugin + an API key.

## Install

```
/plugin marketplace add <you>/allore-pipelines
/plugin install clep@clep-marketplace
```

Local dev (no marketplace):

```bash
claude --plugin-dir ./plugins/clep
```

## Setup

```bash
export CLEP_API_URL=https://your-backend.example.com  # default http://127.0.0.1:8787
export CLEP_API_KEY=clep_live_...                     # dashboard → API Keys
```

## Use (in Claude Code)

One command. Say what you want:

```
/clep:clep make a clip of the signup flow at http://localhost:3000, cinematic style
```

Claude checks if the feature is already marked up (`data-clep` attributes),
instruments it if not, renders the MP4, and reports back where it landed —
it's always in the platform dashboard, plus a local file if you ask for one.
No separate "instrument first" step.

Direct CLI equivalents (`${CLAUDE_PLUGIN_ROOT}/bin/clep`, stdlib only):

```bash
bin/clep health
bin/clep scan https://your-app.com/dashboard
bin/clep clip --url https://your-app.com/dashboard --name ai-research --wait --out ./ai.mp4
bin/clep jobs
```

## Backend

Hosted from `platform/server.py` (`pipeline_clep` engine). Lock it down with:

```bash
CLEP_API_KEYS=clep_live_abc,clep_live_xyz HOST=0.0.0.0 PORT=8787 \
  pipeline/venv/bin/python platform/server.py
```

`GET /api/health` is open; all other `/api/*` require `X-API-Key` (or
`Authorization: Bearer`) when keys are set. Local dev (no keys) stays open.

## Validate

```bash
claude plugin validate ./plugins/clep
```
