"""
Clep CLI: natural-language product clips.

Three clip kinds, one command:

    # Walkthrough / portfolio overview / feature showcase (any URL, no
    # instrumentation needed — understands the page, then scroll-tours it):
    python main.py --prompt "walkthrough of my portfolio at http://x highlighting hero, work, contact"
    python allore.py --mode clep --prompt "/clep make video showing the portfolio" --out out.mp4

    # Launch video (maps the whole product end to end, then shoots the story):
    python main.py --prompt "product launch video for https://acme.ai" --out output/launch.mp4

    # Single feature demo (instrumented [data-clep="name"] app):
    python main.py --url https://acme.ai/dashboard --name ai-research --out output/ai.mp4

    # UI mockup (no browser — renders feature.json):
    python main.py --feature demo/ai-research.feature.json --out output/ai-research.mp4
    python main.py --prompt "mockup of Generate Report, cinematic, 5s" --out output/mock.mp4

Also reachable as: python allore.py --mode clep --prompt ... / --url ... --name ... / --feature ...
"""

from __future__ import annotations

import argparse
import json
import re
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


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "clip").lower()).strip("-")
    return (s or "clip")[:40]


def _mockup_feature_from_spec(spec) -> dict:
    title = (spec.query or spec.name or "Feature")
    title = title.replace("-", " ").title()[:40]
    return {
        "name": _slug(spec.name or title),
        "title": title,
        "url": spec.url or "app.clep.io",
        "element": {"x": 0.2, "y": 0.35, "w": 0.6, "h": 0.3},
        "steps": [
            {"type": "focus", "label": "Search input"},
            {"type": "type", "text": spec.query or "AI browser agents"},
            {"type": "click", "target": (spec.name or "Start")[:24]},
            {"type": "loading", "label": "Working…"},
            {"type": "result", "label": "Done"},
        ],
        "style": spec.style or "saas",
        "duration": spec.duration or 4.2,
        "aspect": spec.aspect or "16:9",
    }


def run(feature_path: Path | None = None, out_path: Path | None = None,
        aspect: str | None = None, style: str | None = None,
        duration: float | None = None, url: str | None = None,
        name: str | None = None, query: str | None = None,
        steps_file: Path | None = None, fps: int | None = None,
        quality: str | None = None, chromium_args: list | None = None,
        prompt: str | None = None, kind: str | None = None,
        sections: list[str] | None = None, movement: str | None = None,
        captions: bool = True, size: str | None = None, bg: str | None = None):
    _check_prereqs()
    if aspect and aspect not in config.ASPECTS:
        print(f"[clep] ERROR: --aspect must be one of {sorted(config.ASPECTS)}", file=sys.stderr)
        sys.exit(1)
    if size:
        try:
            config.canvas(size=size)
        except ValueError as e:
            print(f"[clep] ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    if bg:
        try:
            config.resolve_bg(bg, style or "saas")
        except ValueError as e:
            print(f"[clep] ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    if style and style not in config.STYLES:
        print(f"[clep] ERROR: --style must be one of {list(config.STYLES)}", file=sys.stderr)
        sys.exit(1)
    if movement and movement not in ("calm", "standard", "dynamic"):
        print("[clep] ERROR: --movement must be calm|standard|dynamic", file=sys.stderr)
        sys.exit(1)
    if kind and kind not in ("auto", "tour", "feature", "mockup", "launch"):
        print("[clep] ERROR: --kind must be auto|tour|feature|mockup", file=sys.stderr)
        sys.exit(1)

    # ── Natural-language path: prompt -> grounded shoot plan ──────────
    if prompt:
        import director as director_mod
        import agent as agent_mod
        import polish as polish_mod

        spec = director_mod.parse(prompt, url=url, name=name)
        # Explicit flags win over parsed words.
        if kind and kind != "auto":
            spec.kind = kind
        if url:
            spec.url = url
        if name:
            spec.name = name
        if query:
            spec.query = query
        if sections:
            spec.sections = list(sections)
        if aspect:
            spec.aspect = aspect
        if style:
            spec.style = style
        if duration:
            spec.duration = duration
        if movement:
            spec.movement = movement
        if fps:
            spec.fps = fps
        if quality:
            spec.quality = quality
        if size:
            spec.size = size
        if bg:
            spec.bg = bg
        spec.captions = captions

        # Narrow tour -> feature when the prompt names a real instrumented
        # feature on the page.
        page: dict = {}
        if spec.url and spec.kind in ("tour", "feature", "launch"):
            try:
                page = agent_mod.scan_content(spec.url)
            except Exception as e:  # grounding is best-effort, never fatal
                print(f"[clep] page scan skipped ({type(e).__name__}: {e})")
                page = {}
            if spec.kind == "tour" and spec.name and page.get("features"):
                names = {f.get("name") for f in page["features"]}
                if spec.name in names:
                    spec.kind = "feature"

        print(director_mod.plan(spec, page)["text"])

        if spec.kind == "mockup":
            feature = _mockup_feature_from_spec(spec)
            if spec.feature_file:
                try:
                    feature = json.loads(Path(spec.feature_file).read_text())
                except OSError as e:
                    print(f"[clep] ERROR: cannot read {spec.feature_file}: {e}", file=sys.stderr)
                    sys.exit(1)
            plan = storyboard.build_plan(
                feature, duration=spec.duration, aspect=spec.aspect,
                style=spec.style, size=spec.size)
            out = Path(out_path or (config.OUTPUT_DIR / f"{plan['name']}.mp4"))
            return renderer.render(plan, out, quality=spec.quality, size=spec.size)

        if not spec.url:
            print("[clep] ERROR: the prompt needs a URL "
                  "(e.g. --prompt \"tour of https://acme.ai showing pricing\").",
                  file=sys.stderr)
            sys.exit(1)

        if spec.kind == "feature":
            if not spec.name:
                feats = [f.get("name") for f in (page.get("features") or [])]
                if len(feats) == 1:
                    spec.name = feats[0]
                    print(f"[clep] using the page's only feature: {spec.name}")
                else:
                    print("[clep] ERROR: which feature? "
                          + (f"page has: {', '.join(feats[:8])}. " if feats else "no data-clep found. ")
                          + "name it (“demo of FEATURE at URL”) or drop the feature words for a full tour.",
                          file=sys.stderr)
                    sys.exit(1)
            steps = None
            if steps_file:
                raw_steps = json.loads(Path(steps_file).read_text())
                steps = raw_steps.get("steps", raw_steps) if isinstance(raw_steps, dict) else raw_steps
            cap_dir = config.CACHE_DIR / "captures" / spec.name
            agent_mod.record(spec.url, spec.name, cap_dir, query=spec.query,
                             steps=steps, chromium_args=chromium_args)
            out = Path(out_path or (config.OUTPUT_DIR / f"{spec.name}.mp4"))
            return polish_mod.polish(
                cap_dir / "trace.json", out, aspect=spec.aspect, style=spec.style,
                duration=spec.duration, fps=spec.fps or 60,
                quality=spec.quality, movement=spec.movement or "standard",
                captions=spec.captions, size=spec.size, bg=spec.bg)

        # kind == launch: end-to-end product story. The map is built first
        # (named requests, then every remaining section in page order, ending
        # near the CTA) — nothing is shot until the whole arc exists.
        if spec.kind == "launch":
            stops = director_mod.build_launch_stops(
                spec.sections, (page.get("sections") or []))
            cap_dir = config.CACHE_DIR / "captures" / f"launch-{_slug(spec.url)}"
            n_stops = max(len(stops), 1)
            if spec.duration and n_stops:
                per_section = min(4.0, max(1.4, (spec.duration - 2.5) / n_stops))
            else:
                per_section = 2.2
            agent_mod.record_tour(spec.url, cap_dir,
                                  sections=stops or None, per_section=per_section,
                                  chromium_args=chromium_args)
            out = Path(out_path or (config.OUTPUT_DIR / "launch.mp4"))
            return polish_mod.polish(
                cap_dir / "trace.json", out, aspect=spec.aspect, style=spec.style,
                duration=spec.duration, fps=spec.fps or 60,
                quality=spec.quality, movement=spec.movement or "calm",
                captions=spec.captions, size=spec.size, bg=spec.bg)

        # kind == tour: scroll showcase, no instrumentation required.
        stops = director_mod.ground_sections(spec.sections, (page.get("sections") or []))
        cap_dir = config.CACHE_DIR / "captures" / f"tour-{_slug(spec.url)}"
        n_stops = max(len(stops), 1)
        if spec.duration and n_stops:
            # Fit holds into the requested runtime (keep ~2.5s overhead).
            per_section = min(4.0, max(1.4, (spec.duration - 2.5) / n_stops))
        else:
            per_section = 2.4
        agent_mod.record_tour(spec.url, cap_dir,
                              sections=stops or None, per_section=per_section,
                              chromium_args=chromium_args)
        out = Path(out_path or (config.OUTPUT_DIR / "tour.mp4"))
        return polish_mod.polish(
            cap_dir / "trace.json", out, aspect=spec.aspect, style=spec.style,
            duration=spec.duration, fps=spec.fps or 60,
            quality=spec.quality, movement=spec.movement or "calm",
            captions=spec.captions, size=spec.size, bg=spec.bg)

    # ── Agent path: live app ──────────────────────────────────────────
    if url and not name:
        import agent as agent_mod
        reg = agent_mod.discover(url)
        print(f"[clep] {reg['count']} feature(s) on {url}:")
        for f in reg["features"]:
            print(f"  - {f['name']} — input={f['has_input']} button={f['has_button']} "
                  f"button_label={f['button_label']!r} states={f['states']}")
        if not reg["features"]:
            print("[clep] no data-clep found — two options:")
            print(f"[clep]   tour (no instrumentation): --prompt \"walkthrough of {url} showing hero, pricing\"")
            print("[clep]   feature demo: add data-clep=\"name\" to the app (see sdk/README.md), then re-run.")
        else:
            print("[clep] tip: --prompt \"demo of NAME at URL\" plans + records it, "
                  "or pass --name directly.")
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
                                 fps=fps or 60, quality=quality,
                                 movement=movement or "standard",
                                 captions=captions, size=size, bg=bg)

    # ── Synthetic path: feature.json ──────────────────────────────────
    if not feature_path:
        print("[clep] ERROR: give --prompt (natural language), --feature (synthetic) "
              "or --url/--name (agent).", file=sys.stderr)
        sys.exit(1)
    feature = json.loads(Path(feature_path).read_text())
    plan = storyboard.build_plan(feature, duration=duration, aspect=aspect,
                                 style=style, size=size)
    print(f"[clep] {plan['name']}: {plan['duration']:.1f}s {plan['aspect']} {plan['style']} "
          f"— {plan['ui']['query_text'][:40]!r} -> [{plan['ui']['button_label']}]")
    return renderer.render(plan, out_path, quality=quality, size=size)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Product clips: natural-language (--prompt), live URL, or synthetic mock.")
    ap.add_argument("--prompt", default=None, type=str,
                    help="Natural language, e.g. \"/clep walkthrough of https://x showing hero, pricing, 10s calm\".")
    ap.add_argument("--kind", default=None, choices=["auto", "tour", "feature", "mockup", "launch"],
                    help="Override the prompt's clip kind (default: inferred).")
    ap.add_argument("--sections", default=None, type=str,
                    help="Comma-separated tour stops, e.g. \"hero, pricing, contact\" (overrides prompt).")
    ap.add_argument("--movement", default=None, choices=["calm", "standard", "dynamic"],
                    help="Camera movement (default: calm for tours, standard for features).")
    ap.add_argument("--captions", dest="captions", action="store_true", default=True,
                    help="Section captions on tours (default on).")
    ap.add_argument("--no-captions", dest="captions", action="store_false",
                    help="Turn section captions off.")
    ap.add_argument("--feature", default=None, type=Path)
    ap.add_argument("--url", default=None, type=str, help="Live app URL (agent path).")
    ap.add_argument("--name", default=None, type=str, help="data-clep name to record (agent path).")
    ap.add_argument("--query", default=None, type=str, help="Text to type (agent path; default from SDK/placeholder).")
    ap.add_argument("--steps-file", default=None, type=Path, help="JSON multi-step plan {steps:[...]} (agent path; omit for auto).")
    ap.add_argument("--out", default=None, type=Path)
    ap.add_argument("--aspect", default=None, choices=sorted(config.ASPECTS))
    ap.add_argument("--size", default=None, type=str,
                    help="Exact canvas WxH, e.g. 1120x640 for a landing-page card (overrides --aspect/--quality).")
    ap.add_argument("--bg", default=None, type=str,
                    help="Backdrop override: preset (saas, minimal, cinematic, apple, blush), "
                         "custom gradient (#aaa,#bbb[,#ccc]), or solid (#hex / solid:#hex).")
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
        quality=a.quality, chromium_args=a.chromium_args, prompt=a.prompt,
        kind=a.kind, sections=[s.strip() for s in a.sections.split(",")] if a.sections else None,
        movement=a.movement, captions=a.captions, size=a.size, bg=a.bg)
