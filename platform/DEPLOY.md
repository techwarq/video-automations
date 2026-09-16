# Deploy the Clep backend

Image builds from the repo root (`Dockerfile`). Runtime needs Chromium
(base image), ffmpeg (apt), Pillow + Playwright (pip). No code changes per
host — config is env-only:

| var | required | what |
|---|---|---|
| `HOST` | no (default `0.0.0.0` in image) | bind address |
| `PORT` | platform sets it | Fly/Railway/Render inject their own |
| `CLEP_API_KEYS` | **yes for prod** | `clep_live_abc,clep_live_xyz` — gates all `/api/*` except `/api/health` |

State (`registry.json`, `jobs.json`, output MP4s) lives in `/data` — mount a
volume/disk there or jobs + registry reset on every redeploy.

## Fly.io (persistent volume, ~$2–5/mo)

```bash
fly launch --no-deploy          # accept defaults; app gets a fly.toml
fly volumes create clep_data --size 10 --region iad
# add to fly.toml:
#   [[mounts]]
#     source = "clep_data"
#     destination = "/data"
fly secrets set CLEP_API_KEYS=clep_live_...
fly deploy
curl https://<app>.fly.dev/api/health
```

## Railway (easiest, ephemeral disk unless volume added)

```bash
railway init                    # or New Project → Deploy from GitHub repo
railway variables set CLEP_API_KEYS=clep_live_...
railway up                      # Dockerfile detected automatically
```

Add a Volume mounted at `/data` in the service settings, otherwise MP4s and
the registry vanish on redeploy. Railway injects `PORT` automatically.

## Render (blueprint or manual)

Manual: New → Web Service → point at repo → Runtime Docker → add a Disk
mounted at `/data` → env `CLEP_API_KEYS=clep_live_...` → Deploy. Health check
path: `/api/health`.

## After deploy

```bash
export CLEP_API_URL=https://<your-host>
export CLEP_API_KEY=clep_live_...
plugins/clep/bin/clep health
plugins/clep/bin/clep scan https://your-app.com/dashboard
```

Then set `NEXT_PUBLIC_CLEP_API_URL=<your-host>` in `/Users/sonalinayak/clep`
so the dashboard talks to it.
