"""
Feature clip CLI: feature.json -> polished 2-5s product MP4.

Synthetic (no browser — UI mock rendered from feature.json):

    python main.py --feature demo/ai-research.feature.json --out output/ai-research.mp4
    python main.py --feature demo/generate-report.feature.json --aspect 9:16 \\
        --style cinematic --duration 3 --out output/report-portrait.mp4

Agent (real app — automated Screen Studio: record the live feature,
then edit the recording with auto-zoom + cursor):

    python main.py --url https://acme.ai/dashboard --name ai-research --out output/ai.mp4
    python main.py --url https://acme.ai/dashboard   # lists instrumented features, records nothing

Also reachable as: python allore.py --mode clep --feature ... / --url ... --name ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
import storyboard
import renderer


def _check_prereqs():
    problems = []
    if not Path(config.FFMPEG_BIN).exists():
        problems.append(f"ffmpeg not found at {config.FFMPEG_BIN} (set PIPELINE_FFMPEG_BIN).")
    if problems:
        for p in problems:
            print(f"[clep] ERROR: {p}", file=sys.stderr)
        sys.exit(1)


def run(feature_path: Path | None = None, out_path: Path | None = None,
        aspect: str | None = None, style: str | None = None,
        duration: float | None = None, url: str | None = None,
        name: str | None = None, query: str | None = None,
        steps_file: Path | None = None, fps: int | None = None,
        quality: str | None = None, chromium_args: list | None = None):
    _check_prereqs()
    if aspect and aspect not in config.ASPECTS:
        print(f"[clep] ERROR: --aspect must be one of {sorted(config.ASPECTS)}", file=sys.stderr)
        sys.exit(1)
    if style and style not in config.STYLES:
        print(f"[clep] ERROR: --style must be one of {list(config.STYLES)}", file=sys.stderr)
        sys.exit(1)

    # ── Agent path: live app ──────────────────────────────────────────
    if url and not name:
        import agent as agent_mod
        reg = agent_mod.discover(url)
        print(f"[clep] {reg['count']} feature(s) on {url}:")
        for f in reg["features"]:
            print(f"  - {f['name']} — input={f['has_input']} button={f['has_button']} "
                  f"button_label={f['button_label']!r} states={f['states']}")
        if not reg["features"]:
            print("[clep] tip: add data-clep=\"name\" to the app (see sdk/README.md), then re-run.")
        return None
    if url and name:
        import agent as agent_mod
        import polish as polish_mod
        steps = None
        if steps_file:
            raw_steps = json.loads(Path(steps_file).read_text())
            steps = raw_steps.get("steps", raw_steps) if isinstance(raw_steps, dict) else raw_steps
            print(f"[clep] multi-step plan: {len(steps)} step(s) from {steps_file}")
        cap_dir = config.CACHE_DIR / "captures" / name
        trace = agent_mod.record(url, name, cap_dir, query=query, steps=steps,
                                 chromium_args=chromium_args)
        out_path = Path(out_path or (config.OUTPUT_DIR / f"{name}.mp4"))
        return polish_mod.polish(cap_dir / "trace.json", out_path,
                                 aspect=aspect, style=style, duration=duration,
                                 fps=fps or 60, quality=quality)

    # ── Synthetic path: feature.json ──────────────────────────────────
    if not feature_path:
        print("[clep] ERROR: give --feature (synthetic) or --url/--name (agent).",
              file=sys.stderr)
        sys.exit(1)
    feature = json.loads(Path(feature_path).read_text())
    plan = storyboard.build_plan(feature, duration=duration, aspect=aspect, style=style)
    print(f"[clep] {plan['name']}: {plan['duration']:.1f}s {plan['aspect']} {plan['style']} "
          f"— {plan['ui']['query_text'][:40]!r} -> [{plan['ui']['button_label']}]")
    return renderer.render(plan, out_path, quality=quality)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Feature clips: synthetic (feature.json) or agent (live URL).")
    ap.add_argument("--feature", default=None, type=Path)
    ap.add_argument("--url", default=None, type=str, help="Live app URL (agent path).")
    ap.add_argument("--name", default=None, type=str, help="data-clep name to record (agent path).")
    ap.add_argument("--query", default=None, type=str, help="Text to type (agent path; default from SDK/placeholder).")
    ap.add_argument("--steps-file", default=None, type=Path, help="JSON multi-step plan {steps:[...]} (agent path; omit for auto).")
    ap.add_argument("--out", default=None, type=Path)
    ap.add_argument("--aspect", default=None, choices=sorted(config.ASPECTS))
    ap.add_argument("--style", default=None, choices=list(config.STYLES))
    ap.add_argument("--duration", default=None, type=float)
    ap.add_argument("--fps", default=None, type=int, help="Output fps (default 60 for agent path).")
    ap.add_argument("--quality", default=None, choices=list(config.QUALITIES), help="720p or 1080p (default 1080p).")
    ap.add_argument("--chromium-arg", dest="chromium_args", action="append", default=None,
                    help="Extra Chromium flag, test-only (repeatable). E.g. --chromium-arg=--disable-web-security.")
    a = ap.parse_args()
    out = a.out or (config.OUTPUT_DIR / "clep.mp4")
    run(a.feature, out, aspect=a.aspect, style=a.style, duration=a.duration,
        url=a.url, name=a.name, query=a.query, steps_file=a.steps_file, fps=a.fps,
        quality=a.quality, chromium_args=a.chromium_args)
