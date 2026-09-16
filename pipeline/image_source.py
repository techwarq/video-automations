"""
Pinterest image search — not a stub. Wraps the existing Allore backend's
PinterestBrowserService, but calls it via a standalone Node/tsx script
(scripts/pinterest-search-cli.ts) rather than the Worker's HTTP route.

Why not just POST to the Worker's /pinterest/search endpoint: Stagehand's
LOCAL mode (which drives a real local Chrome for scraping) needs to spawn a
child process and read process.env.CHROME_PATH directly. The Cloudflare
Workers runtime (workerd) — even under `wrangler dev` — provides neither,
so that route only works with BROWSERBASE_API_KEY (a paid remote-browser
service). Since this machine has a local Chrome, PINTEREST_COOKIE, and
PINTEREST_EMAIL/PASSWORD already configured in the backend's .env, it's
more direct to drive the same PinterestBrowserService class from a plain
Node process instead.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import config


def pinterest_search(query: str, count: int = 5) -> list[str]:
    """Returns up to `count` image URLs for `query`, high-res where available."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["npx", "tsx", str(config.PINTEREST_CLI_SCRIPT), query, str(count), str(out_path)],
            cwd=config.ALLORE_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print(f"[image_source] pinterest-search-cli exited {result.returncode} for query {query!r}:\n{result.stderr[-2000:]}")

        if not out_path.exists():
            return []
        with open(out_path) as f:
            urls = json.load(f)
        return urls[:count]
    finally:
        out_path.unlink(missing_ok=True)
