"""
Clep video service — Cloud Run. Owns the whole pipeline: planning (director),
recording (agent, Playwright), and editing/render (polish -> storyboard/renderer).

  python pipeline_clep/service.py [--port 8788]

Two roles, one process:

1. Poll loop (background thread): pulls job messages off a Cloudflare Queue
   via the HTTP Pull Consumer API, runs the full pipeline for each
   (director.parse -> agent.record/record_tour -> polish.polish), uploads the
   finished MP4 to R2, then POSTs the result back to the Worker API's
   /internal/jobs/<id> and acks the message.

2. Tiny HTTP surface (stdlib, matching the old platform/server.py style):
     GET  /health           liveness
     POST /internal/scan    synchronous agent.discover(url) — the one read
                             path that still needs live Chromium, so the
                             Worker proxies /api/features here instead of
                             running it itself.
   Both require X-Internal-Secret == CLEP_INTERNAL_SECRET (except /health).

Env:
  CF_ACCOUNT_ID, CF_QUEUE_ID, CF_API_TOKEN   Cloudflare Queues HTTP Pull API
  CLEP_WORKER_URL                            base URL of the platform/ Worker
  CLEP_INTERNAL_SECRET                       shared secret for both directions
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET   MP4 upload
  HOST, PORT                                 HTTP bind (health + scan)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import agent
import config
import director
import polish

OUTPUT_DIR = HERE / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_QUEUE_ID = os.environ.get("CF_QUEUE_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CLEP_WORKER_URL = os.environ.get("CLEP_WORKER_URL", "http://127.0.0.1:8787").rstrip("/")
CLEP_INTERNAL_SECRET = os.environ.get("CLEP_INTERNAL_SECRET", "")

_PULL_BASE = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/queues/{CF_QUEUE_ID}/messages"
)
POLL_BATCH_SIZE = int(os.environ.get("CLEP_POLL_BATCH", "5"))
POLL_IDLE_SLEEP = float(os.environ.get("CLEP_POLL_IDLE_SLEEP", "3"))


def _cf_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{_PULL_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {CF_API_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def _pull_messages(batch_size: int) -> list[dict]:
    resp = _cf_request("POST", "/pull", {"batch_size": batch_size, "visibility_timeout_ms": 600_000})
    return (resp.get("result") or {}).get("messages") or []


def _ack_message(lease_id: str) -> None:
    _cf_request("POST", "/ack", {"acks": [{"lease_id": lease_id}]})


def _callback(job_id: str, **fields) -> None:
    body = json.dumps(fields).encode()
    req = urllib.request.Request(
        f"{CLEP_WORKER_URL}/internal/jobs/{job_id}", data=body, method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Internal-Secret", CLEP_INTERNAL_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception as e:  # noqa: BLE001 — surfaced via log, message stays unacked for retry
        print(f"[service] callback failed for {job_id}: {e}", file=sys.stderr)
        raise


def _upload_to_r2(local_path: Path, key: str) -> None:
    import boto3

    account_id = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    s3.upload_file(str(local_path), bucket, key, ExtraArgs={"ContentType": "video/mp4"})


def _run_job(payload: dict) -> None:
    """Plan -> record -> edit/render one job, then report + upload.

    Ported from _run_clip_job in the old platform/server.py — same branch
    logic (prompt planning, tour vs feature, bg/size validation), just
    reporting status via callback POST instead of an in-process dict.
    """
    job_id = payload["job_id"]
    try:
        prompt = payload.get("prompt")
        kind = payload.get("kind") or ("tour" if prompt else "feature")
        if kind == "auto":
            kind = "tour" if prompt else ("feature" if payload.get("name") else "tour")
        if prompt:
            spec = director.parse(prompt, url=payload.get("url"), name=payload.get("name"))
            for k in ("url", "name", "style", "duration", "movement", "quality", "fps", "size", "bg"):
                if payload.get(k) is not None:
                    setattr(spec, k, payload[k])
            if payload.get("sections"):
                spec.sections = payload["sections"]
            if payload.get("query"):
                spec.query = payload["query"]
            if kind != "auto":
                spec.kind = kind
            payload = {**payload, "url": spec.url, "name": spec.name,
                       "style": spec.style or payload.get("style"),
                       "duration": spec.duration or payload.get("duration"),
                       "movement": spec.movement or payload.get("movement"),
                       "size": spec.size or payload.get("size"),
                       "bg": spec.bg or payload.get("bg"),
                       "kind": spec.kind, "sections": spec.sections,
                       "query": spec.query or payload.get("query")}
            kind = spec.kind

        try:
            config.resolve_bg(payload.get("bg"), payload.get("style") or "saas")
            if payload.get("size"):
                config.canvas(size=payload["size"])
        except ValueError as e:
            raise ValueError(f"bad bg/size: {e}")

        _callback(job_id, status="recording")
        url = payload.get("url")
        if not url:
            raise ValueError("url required (or put it in the prompt, e.g. 'tour of https://…')")
        style = payload.get("style")
        duration = payload.get("duration")
        movement = payload.get("movement")
        show_captions = payload.get("captions", True)

        if kind in ("tour", "launch") or not payload.get("name"):
            page: dict = {}
            try:
                page = agent.scan_content(url)
            except Exception:
                page = {}
            stops = director.ground_sections(payload.get("sections") or [], page.get("sections") or [])
            cap_dir = HERE / "cache" / "captures" / f"tour-{int(time.time()) % 1000000:06d}"
            trace = agent.record_tour(url, cap_dir, sections=stops or None,
                                       chromium_args=payload.get("chromium_args"))
            _callback(job_id, status="editing", trace=str(cap_dir / "trace.json"))
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out = OUTPUT_DIR / f"tour-{stamp}.mp4"
            polish.polish(cap_dir / "trace.json", out, style=style, duration=duration,
                           fps=int(payload.get("fps") or 60),
                           quality=payload.get("quality") or "1080p",
                           movement=movement or "calm", captions=bool(show_captions),
                           size=payload.get("size"), bg=payload.get("bg"))
            _upload_to_r2(out, f"outputs/{out.name}")
            _callback(job_id, status="done", out=f"/outputs/{out.name}")
            return

        name = payload["name"]
        cap_dir = HERE / "cache" / "captures" / name
        trace = agent.record(url, name, cap_dir,
                              query=payload.get("query"),
                              steps=payload.get("steps"),
                              chromium_args=payload.get("chromium_args"))
        _callback(job_id, status="editing", trace=str(cap_dir / "trace.json"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = OUTPUT_DIR / f"{name}-{stamp}.mp4"
        polish.polish(cap_dir / "trace.json", out,
                       style=payload.get("style"),
                       duration=payload.get("duration"),
                       fps=int(payload.get("fps") or 60),
                       quality=payload.get("quality") or "1080p",
                       movement=payload.get("movement") or "standard",
                       captions=bool(payload.get("captions", True)),
                       size=payload.get("size"), bg=payload.get("bg"))
        _upload_to_r2(out, f"outputs/{out.name}")
        _callback(job_id, status="done", out=f"/outputs/{out.name}")
    except Exception as e:  # noqa: BLE001 — reported to the Worker, not raised further
        try:
            _callback(job_id, status="error", error=f"{type(e).__name__}: {e}")
        except Exception:
            pass  # callback itself failed — leave the message unacked for redelivery


def _poll_loop() -> None:
    if not (CF_ACCOUNT_ID and CF_QUEUE_ID and CF_API_TOKEN):
        print("[service] CF_ACCOUNT_ID/CF_QUEUE_ID/CF_API_TOKEN not set — poll loop disabled", file=sys.stderr)
        return
    print(f"[service] polling queue {CF_QUEUE_ID} for jobs")
    while True:
        try:
            messages = _pull_messages(POLL_BATCH_SIZE)
        except Exception as e:  # noqa: BLE001
            print(f"[service] pull failed: {e}", file=sys.stderr)
            time.sleep(POLL_IDLE_SLEEP)
            continue
        if not messages:
            time.sleep(POLL_IDLE_SLEEP)
            continue
        for msg in messages:
            body = msg.get("body")
            payload = json.loads(body) if isinstance(body, str) else (body or {})
            try:
                _run_job(payload)
            finally:
                # Ack whether the job succeeded or reported a terminal error —
                # only a callback/pull *transport* failure should leave it
                # unacked for Cloudflare to redeliver.
                try:
                    _ack_message(msg["lease_id"])
                except Exception as e:  # noqa: BLE001
                    print(f"[service] ack failed for {payload.get('job_id')}: {e}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClepService/0.1"

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return bool(CLEP_INTERNAL_SECRET) and self.headers.get("X-Internal-Secret") == CLEP_INTERNAL_SECRET

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/health":
            return self._json({"ok": True, "service": "clep-video-service"})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/internal/scan":
            if not self._authorized():
                return self._json({"error": "unauthorized"}, 401)
            qs = urllib.parse.parse_qs(url.query)
            target = (qs.get("url") or [""])[0]
            if not target:
                return self._json({"error": "missing ?url="}, 400)
            try:
                return self._json(agent.discover(target))
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[service] {self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Clep video service — plan, record, edit/render.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8788")))
    ap.add_argument("--host", type=str, default=os.environ.get("HOST", "0.0.0.0"))
    a = ap.parse_args()

    threading.Thread(target=_poll_loop, daemon=True).start()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[service] http://{a.host}:{a.port}  (health: GET /health, scan: POST /internal/scan)")
    srv.serve_forever()
