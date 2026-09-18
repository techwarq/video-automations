/**
 * Clep platform API — Cloudflare Worker (Hono). Auth, feature registry, and
 * job bookkeeping only. The actual video pipeline (plan -> record -> edit
 * /render) runs as its own service in pipeline_clep/ on Cloud Run; this
 * Worker just enqueues jobs onto a Cloudflare Queue for it and records
 * status callbacks. Route table matches the old platform/server.py 1:1 so
 * plugins/clep/bin/clep needs no changes.
 *
 * Served at https://api.clep.abstraklabs.com/v1 — every route below hangs
 * off that /v1 prefix (e.g. /v1/api/health), via Hono's basePath.
 *
 * Routes:
 *   GET  /v1                        {service, message}
 *   GET  /v1/api/health              no auth — liveness
 *   GET  /v1/api/features?url=...    proxies to Cloud Run /internal/scan (needs live Chromium) + registry merge, 60s cache
 *   GET  /v1/api/registry            everything known
 *   POST /v1/api/ingest              SDK live export {name, title, url, element, ...}
 *   POST /v1/api/clips               -> {job_id} — enqueues onto the clep-jobs queue
 *   GET  /v1/api/jobs / /v1/api/jobs/:id
 *   GET  /v1/outputs/:file           finished MP4s, streamed from R2
 *   POST /v1/auth/signup /v1/auth/login, GET /v1/auth/me
 *   POST /v1/internal/jobs/:id       Cloud Run's completion callback (X-Internal-Secret)
 *
 * Auth: CLEP_API_KEYS (comma list) or a D1-backed per-user api_key gate all
 * /api/* except /api/health. Empty CLEP_API_KEYS = open (local dev).
 */

import { Hono } from "hono";
import { cors } from "hono/cors";
import type { Env, JobRow } from "./types";
import { isAuthorized, createUser, verifyLogin, getUserByToken, AuthError } from "./auth";
import { loadRegistry, mergeFeature } from "./registry";

const VERSION = "0.3.0";

const app = new Hono<{ Bindings: Env }>().basePath("/v1");

app.use("*", cors());

app.use("/api/*", async (c, next) => {
  if (c.req.path === "/v1/api/health") return next();
  if (!(await isAuthorized(c.env, c.req.raw.headers))) {
    return c.json({ error: "unauthorized: missing or invalid API key" }, 401);
  }
  return next();
});

app.get("/", (c) => c.json({ service: "clep-platform", message: "API only — see /api/health", version: VERSION }));

app.get("/api/health", (c) => c.json({ ok: true, service: "clep-platform", version: VERSION }));

app.get("/api/registry", async (c) => c.json(await loadRegistry(c.env.DB)));

// 60s cache, same TTL _scan() used in platform/server.py, module-scope so it
// survives across requests on a warm isolate.
const scanCache = new Map<string, { at: number; data: unknown }>();

app.get("/api/features", async (c) => {
  const target = c.req.query("url");
  if (!target) return c.json({ error: "missing ?url=" }, 400);

  const cached = scanCache.get(target);
  if (cached && Date.now() - cached.at < 60_000) return c.json(cached.data);

  let res: Response;
  try {
    res = await fetch(`${c.env.CLOUD_RUN_SCAN_URL}/internal/scan?url=${encodeURIComponent(target)}`, {
      method: "POST",
      headers: { "X-Internal-Secret": c.env.CLEP_INTERNAL_SECRET },
    });
  } catch (e) {
    return c.json({ error: `scan service unreachable: ${e instanceof Error ? e.message : e}` }, 502);
  }
  if (!res.ok) {
    const body = await res.text();
    return c.json({ error: `scan failed: HTTP ${res.status} ${body}` }, 502);
  }
  const data = (await res.json()) as { features?: Record<string, unknown>[] };
  for (const f of data.features ?? []) {
    await mergeFeature(c.env.DB, target, f, "scan");
  }
  scanCache.set(target, { at: Date.now(), data });
  return c.json(data);
});

app.post("/api/ingest", async (c) => {
  const body = await c.req.json<Record<string, any>>().catch(() => ({}) as Record<string, any>);
  const feat = body.feature ?? body;
  if (!feat.name) return c.json({ error: "feature.name required" }, 400);
  await mergeFeature(c.env.DB, body.url || feat.url || "unknown", feat, "sdk");
  return c.json({ ok: true });
});

app.post("/api/clips", async (c) => {
  const body = await c.req.json<Record<string, any>>().catch(() => ({}) as Record<string, any>);
  if (!body.url && !body.prompt) return c.json({ error: "url or prompt required" }, 400);
  const kind = body.kind || "auto";
  if (!body.name && !body.prompt && kind !== "tour") {
    return c.json({ error: "name required (or kind:tour / prompt)" }, 400);
  }

  const jid = `job-${String(Date.now() % 1_000_000).padStart(6, "0")}`;
  const created = Date.now() / 1000;
  await c.env.DB.prepare(
    `INSERT INTO jobs (id, status, created, url, name, prompt, kind, style, quality, out, error, trace)
     VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)`,
  )
    .bind(
      jid,
      created,
      body.url ?? null,
      body.name ?? null,
      body.prompt ?? null,
      kind,
      body.style || "saas",
      body.quality || "1080p",
    )
    .run();

  await c.env.CLEP_JOBS.send({ job_id: jid, ...body });

  return c.json({ job_id: jid, status: "queued" });
});

app.get("/api/jobs", async (c) => {
  const { results } = await c.env.DB.prepare("SELECT * FROM jobs ORDER BY created DESC").all<JobRow>();
  return c.json({ jobs: results ?? [] });
});

app.get("/api/jobs/:id", async (c) => {
  const job = await c.env.DB.prepare("SELECT * FROM jobs WHERE id = ?").bind(c.req.param("id")).first<JobRow>();
  if (!job) return c.json({ error: "unknown job" }, 404);
  return c.json(job);
});

app.get("/outputs/:file", async (c) => {
  const file = c.req.param("file");
  if (!file.endsWith(".mp4")) return c.json({ error: "not found" }, 404);
  const obj = await c.env.OUTPUTS.get(`outputs/${file}`);
  if (!obj) return c.json({ error: "not found" }, 404);
  return new Response(obj.body, {
    headers: { "Content-Type": "video/mp4", "Content-Length": String(obj.size) },
  });
});

app.post("/auth/signup", async (c) => {
  const body = await c.req.json<Record<string, any>>().catch(() => ({}) as Record<string, any>);
  const { email, password, name } = body;
  if (!email || !password || !name) return c.json({ error: "email, password, and name required" }, 400);
  try {
    return c.json(await createUser(c.env.DB, c.env.CLEP_AUTH_SECRET, email, password, name));
  } catch (e) {
    if (e instanceof AuthError) return c.json({ error: e.message }, 409);
    return c.json({ error: `${e instanceof Error ? e.constructor.name : "Error"}: ${e instanceof Error ? e.message : e}` }, 500);
  }
});

app.post("/auth/login", async (c) => {
  const body = await c.req.json<Record<string, any>>().catch(() => ({}) as Record<string, any>);
  const { email, password } = body;
  if (!email || !password) return c.json({ error: "email and password required" }, 400);
  const result = await verifyLogin(c.env.DB, c.env.CLEP_AUTH_SECRET, email, password);
  if (!result) return c.json({ error: "invalid email or password" }, 401);
  return c.json(result);
});

app.get("/auth/me", async (c) => {
  let token = (c.req.header("Authorization") || "").trim();
  if (token.toLowerCase().startsWith("bearer ")) token = token.slice(7).trim();
  const user = token ? await getUserByToken(c.env.DB, c.env.CLEP_AUTH_SECRET, token) : null;
  if (!user) return c.json({ error: "unauthorized" }, 401);
  return c.json(user);
});

// Cloud Run's completion callback — gated by a shared secret, not a user
// API key. Deny-by-default if the secret isn't configured.
app.post("/internal/jobs/:id", async (c) => {
  const secret = c.env.CLEP_INTERNAL_SECRET;
  const given = c.req.header("X-Internal-Secret") || "";
  if (!secret || given !== secret) return c.json({ error: "unauthorized" }, 401);

  const body = await c.req.json<{ status?: string; out?: string; error?: string; trace?: string }>().catch(
    () => ({}) as Record<string, never>,
  );
  const result = await c.env.DB.prepare(
    "UPDATE jobs SET status = COALESCE(?, status), out = ?, error = ?, trace = COALESCE(?, trace) WHERE id = ?",
  )
    .bind(body.status ?? null, body.out ?? null, body.error ?? null, body.trace ?? null, c.req.param("id"))
    .run();
  if (!result.meta.changes) return c.json({ error: "unknown job" }, 404);
  return c.json({ ok: true });
});

app.notFound((c) => c.json({ error: "not found" }, 404));

export default app;
