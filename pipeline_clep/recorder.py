"""
Recorder (v1): capture a REAL feature from a live URL with Playwright.

  python -m recorder --url https://acme.ai/dashboard --name ai-research \
      --out demo/ai-research.feature.json --screenshot demo/ai-research.png

What it does:
  1. Opens the page, waits for [data-clep="<name>"] (SDK-instrumented app).
  2. Records the element's normalized bbox -> feature.element.
  3. Replays a minimal interaction (scroll into view, hover, click primary
     action, wait for network idle) while screenshotting -> base image.
  4. Pulls window.__CLEP__.export(name) when the SDK is present; otherwise
     synthesizes steps from the DOM (input + button labels).

Graceful fallback: if Playwright isn't installed, prints the pip install
command and exits 2 — the synthetic renderer (no screenshot) still works
without it, so the MVP never blocks on this module.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def record(url: str, name: str, out_feature: Path, out_shot: Path | None,
           width: int = 1440, height: int = 900, wait: float = 1.5) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[recorder] Playwright not installed. Install with:")
        print("    pipeline/venv/bin/pip install playwright && pipeline/venv/bin/playwright install chromium")
        raise SystemExit(2)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(int(wait * 1000))

        sel = f'[data-clep="{name}"]'
        try:
            page.wait_for_selector(sel, timeout=8000)
        except Exception:
            browser.close()
            raise RuntimeError(f'[recorder] no element matching {sel} at {url} — is the SDK installed?')

        box = page.locator(sel).first.bounding_box()
        vw, vh = page.viewport_size["width"], page.viewport_size["height"]
        element = {"x": round(max(box["x"], 0) / vw, 4), "y": round(max(box["y"], 0) / vh, 4),
                   "w": round(box["width"] / vw, 4), "h": round(box["height"] / vh, 4)}

        # SDK export when present (interactions/states/component for free).
        sdk = page.evaluate(
            """(name) => (window.__CLEP__ && window.__CLEP__.export(name)) || null""", name) or {}

        # Minimal replay: hover -> screenshot -> click primary -> settle -> screenshot.
        page.locator(sel).first.scroll_into_view_if_needed()
        page.locator(sel).first.hover()
        page.wait_for_timeout(400)
        action = page.locator(f'{sel} [data-clep-action="primary"], {sel} button').first
        btn_label = ""
        try:
            btn_label = (action.inner_text() or "").strip()[:24]
            action.click(timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        if out_shot:
            out_shot = Path(out_shot)
            out_shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_shot), full_page=False)
            print(f"[recorder] screenshot -> {out_shot}")

        inp = page.locator(f"{sel} input, {sel} [contenteditable]").first
        placeholder = ""
        try:
            placeholder = (inp.get_attribute("placeholder") or inp.get_attribute("name") or "")[:40]
        except Exception:
            pass
        browser.close()

    feature = {
        "name": name,
        "title": (sdk.get("title") if isinstance(sdk, dict) else None) or name.replace("-", " ").title(),
        "url": url,
        "viewport": {"w": width, "h": height},
        "element": element,
        "interactions": (sdk.get("interactions") if isinstance(sdk, dict) else None) or [],
        "states": (sdk.get("states") if isinstance(sdk, dict) else None) or [],
        "steps": [
            {"type": "focus", "label": placeholder or "Search input"},
            {"type": "type", "text": placeholder or "AI browser agents"},
            {"type": "click", "target": btn_label or "Start"},
            {"type": "loading", "label": "Working…"},
            {"type": "result", "label": "Done"},
        ],
    }
    if out_shot:
        feature["screenshot"] = str(out_shot)
    out_feature = Path(out_feature)
    out_feature.parent.mkdir(parents=True, exist_ok=True)
    out_feature.write_text(json.dumps(feature, indent=2))
    print(f"[recorder] feature -> {out_feature}")
    return feature


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record a data-clep feature from a live URL.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-feature", default="demo/recorded.feature.json")
    ap.add_argument("--out-shot", default=None)
    a = ap.parse_args()
    record(a.url, a.name, Path(a.out_feature), Path(a.out_shot) if a.out_shot else None)
