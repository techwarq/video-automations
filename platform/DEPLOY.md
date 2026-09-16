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

## Cloud Run (no persistence, scales to zero — cheapest for low-volume/testing)

`/data` is **not** backed by a volume here — registry/jobs/output MP4s reset
on every redeploy and on cold start after scale-to-zero. Fine for testing;
add a GCS volume mount (`--add-volume`/`--add-volume-mount`, Cloud Run's
native bucket-as-filesystem feature) before relying on this for real users.

`--no-cpu-throttling` is required, not optional: `/api/clips` returns
immediately and the actual recording/render runs in a background thread.
Cloud Run's default throttles CPU to near-zero once the response is sent, so
without this flag the job stalls until another request wakes the instance.
`--max-instances 1` matters too — job/registry state is in-memory plus local
disk, not shared across instances.

```bash
gcloud run deploy clep-platform \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --cpu 4 --memory 4Gi \
  --no-cpu-throttling \
  --min-instances 0 --max-instances 1 \
  --timeout 600 --port 8787 \
  --set-env-vars CLEP_API_KEYS=clep_live_...
curl https://<service>.run.app/api/health
```

`--min-instances 0` is the cost control — the service bills ~$0 while idle
and only spins up (charged) while actually serving a request or running a
job. `--allow-unauthenticated` is required at the Cloud Run/IAM layer even
though the app is still locked down by `CLEP_API_KEYS` above — otherwise
Google IAM auth blocks requests before they ever reach the app.

## After deploy

```bash
export CLEP_API_URL=https://<your-host>
export CLEP_API_KEY=clep_live_...
plugins/clep/bin/clep health
plugins/clep/bin/clep scan https://your-app.com/dashboard
```

Then set `NEXT_PUBLIC_CLEP_API_URL=<your-host>` in `/Users/sonalinayak/clep`
so the dashboard talks to it.
