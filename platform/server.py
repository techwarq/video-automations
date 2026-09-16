"""
Clep platform — see your features, make clips. No build step.

  python platform/server.py [--port 8787]

Stdlib only (http.server + threading). The SDK points here:

  <script src="@clep/sdk/clep.js" data-registry="http://localhost:8787/api/ingest"></script>

Routes:
  GET  /                        dashboard UI
  GET  /api/health              no auth — hosted liveness check
  GET  /api/features?url=...    scan a live app (agent.discover, 60s cache) + registry merge
  GET  /api/registry            everything known (scans + SDK live ingests)
  POST /api/ingest              SDK live export  {name, title, url, element, ...}
  POST /api/clips               {url, name, query?, style?, steps?} -> {job_id}
  GET  /api/jobs                job list (newest first)
  GET  /api/jobs/<id>           job detail {status, out, error}
  GET  /outputs/<file>          finished MP4s

Auth: set CLEP_API_KEYS=key1,key2 to require X-API-Key (or Bearer) on /api/*.
Empty = open (local dev). HOST/PORT envs supported for hosting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "pipeline_clep"))

import agent
import polish

REGISTRY_PATH = HERE / "registry.json"
OUTPUT_DIR = REPO / "pipeline_clep" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_PATH = HERE / "jobs.json"

# Hosted auth: comma-separated static keys, e.g. CLEP_API_KEYS=clep_live_abc,clep_live_xyz.
# Empty (default) = open, for local dev. When set, /api/* (except /api/health)
# requires X-API-Key or Authorization: Bearer.
def _allowed_keys() -> set[str]:
    raw = os.environ.get("CLEP_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_scan_cache: dict[str, tuple[float, dict]] = {}
_registry_lock = threading.Lock()


def _load_jobs() -> None:
    if JOBS_PATH.exists():
        try:
            for j in json.loads(JOBS_PATH.read_text()).get("jobs", []):
                if isinstance(j, dict) and j.get("id"):
                    if j.get("status") in ("queued", "recording", "editing"):
                        j["status"] = "error"
                        j["error"] = "interrupted: server restarted mid-job"
                    _jobs[j["id"]] = j
        except Exception:
            pass


def _save_jobs() -> None:
    try:
        with _jobs_lock:
            jobs = sorted(_jobs.values(), key=lambda j: j.get("created", 0), reverse=True)[:200]
        JOBS_PATH.write_text(json.dumps({"jobs": jobs}, indent=2))
    except Exception:
        pass


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text())
        except Exception:
            pass
    return {"features": []}


def _save_registry(reg: dict) -> None:
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2))


def _merge_feature(url: str, feat: dict, source: str) -> None:
    with _registry_lock:
        reg = _load_registry()
        key = f"{url}||{feat.get('name')}"
        rec = {
            "key": key, "url": url, "name": feat.get("name"),
            "title": feat.get("title") or (feat.get("name") or "").replace("-", " ").title(),
            "element": feat.get("element"),
            "component": feat.get("component") or [],
            "interactions": (feat.get("interactions") or [])[-20:],
            "states": feat.get("states") or [],
            "has_input": feat.get("has_input"),
            "has_button": feat.get("has_button"),
            "button_label": feat.get("button_label") or "",
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
        }
        feats = [f for f in reg["features"] if f.get("key") != key]
        feats.insert(0, rec)
        reg["features"] = feats[:200]
        _save_registry(reg)


def _scan(url: str) -> dict:
    now = time.monotonic()
    if url in _scan_cache and now - _scan_cache[url][0] < 60:
        return _scan_cache[url][1]
    reg = agent.discover(url)
    for f in reg["features"]:
        _merge_feature(url, f, source="scan")
    _scan_cache[url] = (now, reg)
    return reg


def _run_clip_job(job_id: str, payload: dict) -> None:
    def _set(**kw):
        with _jobs_lock:
            _jobs[job_id].update(kw)
        _save_jobs()

    try:
        _set(status="recording")
        url, name = payload["url"], payload["name"]
        cap_dir = REPO / "pipeline_clep" / "cache" / "captures" / name
        trace = agent.record(url, name, cap_dir,
                             query=payload.get("query"),
                             steps=payload.get("steps"),
                             chromium_args=payload.get("chromium_args"))
        _merge_feature(url, {"name": name, "title": trace.get("title"),
                             "states": trace.get("states_seen"),
                             "interactions": trace.get("events")}, source="clep")
        _set(status="editing", trace=str(cap_dir / "trace.json"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = OUTPUT_DIR / f"{name}-{stamp}.mp4"
        polish.polish(cap_dir / "trace.json", out,
                      style=payload.get("style"),
                      duration=payload.get("duration"),
                      fps=int(payload.get("fps") or 60),
                      quality=payload.get("quality") or "1080p")
        _set(status="done", out=f"/outputs/{out.name}", file=str(out))
    except Exception as e:  # noqa: BLE001 — surfaced to the dashboard
        _set(status="error", error=f"{type(e).__name__}: {e}")


class Handler(BaseHTTPRequestHandler):
    server_version = "ClepPlatform/0.1"

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_OPTIONS(self):  # CORS preflight for SDK/app POSTs
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.end_headers()

    def _authorized(self) -> bool:
        allowed = _allowed_keys()
        if not allowed:
            return True
        api_key = (self.headers.get("X-API-Key") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            auth = auth[7:].strip()
        return api_key in allowed or auth in allowed

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json({"error": "unauthorized: missing or invalid API key"}, 401)
        return False

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path == "/api/health":
            return self._json({"ok": True, "service": "clep-platform", "version": "0.2.0"})
        if url.path.startswith("/api/") and not self._require_auth():
            return
        if url.path == "/":
            self._serve_file(HERE / "dashboard.html", "text/html")
        elif url.path == "/api/registry":
            self._json(_load_registry())
        elif url.path == "/api/features":
            target = (qs.get("url") or [""])[0]
            if not target:
                return self._json({"error": "missing ?url="}, 400)
            try:
                self._json(_scan(target))
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif url.path == "/api/jobs":
            with _jobs_lock:
                jobs = sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)
            self._json({"jobs": jobs})
        elif url.path.startswith("/api/jobs/"):
            jid = url.path.rsplit("/", 1)[-1]
            with _jobs_lock:
                job = _jobs.get(jid)
            if not job:
                return self._json({"error": "unknown job"}, 404)
            self._json(job)
        elif url.path.startswith("/outputs/"):
            f = OUTPUT_DIR / url.path.rsplit("/", 1)[-1]
            if not f.exists() or f.suffix != ".mp4":
                return self._json({"error": "not found"}, 404)
            self._serve_file(f, "video/mp4")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path.startswith("/api/") and not self._require_auth():
            return
        if url.path == "/api/ingest":
            body = self._read_json()
            feat = body.get("feature", body)
            if not feat.get("name"):
                return self._json({"error": "feature.name required"}, 400)
            _merge_feature(body.get("url") or feat.get("url") or "unknown", feat, source="sdk")
            return self._json({"ok": True})
        if url.path == "/api/clips":
            body = self._read_json()
            if not body.get("url") or not body.get("name"):
                return self._json({"error": "url and name required"}, 400)
            jid = f"job-{int(time.time() * 1000) % 1000000:06d}"
            with _jobs_lock:
                _jobs[jid] = {"id": jid, "status": "queued", "created": time.time(),
                              "url": body["url"], "name": body["name"],
                              "style": body.get("style") or "saas",
                              "quality": body.get("quality") or "1080p",
                              "out": None, "error": None}
            _save_jobs()
            t = threading.Thread(target=_run_clip_job, args=(jid, body), daemon=True)
            t.start()
            return self._json({"job_id": jid, "status": "queued"})
        return self._json({"error": "not found"}, 404)

    def _serve_file(self, path: Path, ctype: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write(f"[platform] {self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Clep platform — feature registry + clip jobs.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    ap.add_argument("--host", type=str, default=os.environ.get("HOST", "127.0.0.1"))
    a = ap.parse_args()
    _load_jobs()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[platform] dashboard http://{a.host}:{a.port}  (registry: {REGISTRY_PATH})")
    print("[platform] SDK ingest: POST /api/ingest  (point data-registry at it)")
    print(f"[platform] auth: {'open (local dev — set CLEP_API_KEYS to lock down)' if not _allowed_keys() else f'{len(_allowed_keys())} key(s) loaded'}")
    print("[platform] health: GET /api/health")
    srv.serve_forever()
