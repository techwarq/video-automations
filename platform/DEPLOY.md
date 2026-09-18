# Deploy the Clep backend

Two independent deploys now, not one:

- **`platform/`** — the API (auth, registry, job bookkeeping). A Cloudflare
  Worker (Hono), backed by D1 + R2 + Queues.
- **`pipeline_clep/`** — the video service (plan -> record -> edit/render).
  Runs on Cloud Run (needs Chromium + ffmpeg), pulls jobs from the same
  Cloudflare Queue.

They talk to each other over HTTP with a shared secret (`CLEP_INTERNAL_SECRET`)
— never anything Firestore- or disk-state-specific, so either side can be
redeployed independently.

## 1. Cloudflare resources (one-time)

```bash
cd platform
npm install

wrangler d1 create clep-db                 # paste the returned database_id into wrangler.toml
wrangler d1 migrations apply clep-db --remote

wrangler r2 bucket create clep-outputs
wrangler queues create clep-jobs

wrangler secret put CLEP_INTERNAL_SECRET   # same value goes on the Cloud Run side
wrangler secret put CLEP_AUTH_SECRET       # HMAC key for session tokens
# CLEP_API_KEYS can stay in wrangler.toml [vars], or `wrangler secret put CLEP_API_KEYS` to keep it out of source control
```

Also create an R2 API token (Cloudflare dashboard -> R2 -> Manage API tokens)
with read/write on `clep-outputs` — the Cloud Run side uploads via R2's
S3-compatible API, not the Workers binding.

And a Cloudflare API token with `Queues Edit` permission — Cloud Run polls
the queue over the HTTP Pull Consumer API, not as a native Workers consumer.

## 2. Deploy the Worker (`platform/`)

```bash
wrangler deploy
curl https://<worker>.workers.dev/api/health
```

## 3. Deploy the video service (`pipeline_clep/`) — Cloud Run

`--no-cpu-throttling` is required, not optional: the poll loop and any
in-flight render need CPU between requests to `/health`, and Cloud Run
throttles CPU to near-zero once a response is sent unless this is set.

```bash
gcloud run deploy clep-video-service \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --cpu 4 --memory 4Gi \
  --no-cpu-throttling \
  --min-instances 1 --max-instances 3 \
  --timeout 600 --port 8788 \
  --set-env-vars CF_ACCOUNT_ID=...,CF_QUEUE_ID=...,CF_API_TOKEN=...,CLEP_WORKER_URL=https://<worker>.workers.dev,CLEP_INTERNAL_SECRET=...,R2_ACCOUNT_ID=...,R2_ACCESS_KEY_ID=...,R2_SECRET_ACCESS_KEY=...,R2_BUCKET=clep-outputs
curl https://<service>.run.app/health
```

`--min-instances 1` matters here in a way it didn't for the old combined
server: with `min-instances 0`, nothing is polling the queue between jobs,
so the first job after a cold start waits for a scale-up trigger that a pull
loop (as opposed to an inbound request) doesn't provide. Multiple instances
are safe now — job/registry state lives in D1, not local disk, so
`--max-instances` no longer needs to be pinned to 1.

`--allow-unauthenticated` is required at the Cloud Run/IAM layer so `/health`
and `/internal/scan` are reachable; `/internal/scan` is still gated
app-side by `CLEP_INTERNAL_SECRET`.

## After deploy

```bash
export CLEP_API_URL=https://<worker>.workers.dev
export CLEP_API_KEY=clep_live_...
plugins/clep/bin/clep health
plugins/clep/bin/clep scan https://your-app.com/dashboard
```

Then set `NEXT_PUBLIC_CLEP_API_URL=<worker-url>` in `/Users/sonalinayak/clep`
so the dashboard talks to it.
