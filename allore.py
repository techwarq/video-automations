#!/usr/bin/env python3
"""
Allore video pipeline — single unified CLI over the two engines in this repo:

    pipeline/          talking-head explainer (script + talking-head video -> 9:16 final.mp4)
    pipeline_motion/   pure motion graphics / kinetic typography (script -> 16:9 or 9:16 final.mp4)

Pick the engine with --mode, and feed it either a script file or a raw prompt:

    # talking-head, from a script file
    python allore.py --mode talking-head --script pipeline/script.txt --video talking_head.mp4 --out out.mp4

    # talking-head, from a one-off prompt (no .txt file needed)
    python allore.py --mode talking-head --prompt "We give your agent search and fetch for free." \\
        --video talking_head.mp4 --out out.mp4

    # motion-only, from a script file, silent
    python allore.py --mode motion --script pipeline_motion/script_reference.txt --skip-planner --out out.mp4

    # motion-only, from a prompt, with narration + portrait
    python allore.py --mode motion --prompt "Search and fetch, 100% free. No subscriptions." \\
        --audio narration.mp3 --portrait --out out.mp4

Build once:
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    export OPENROUTER_API_KEY=sk-or-v1-...   # only needed unless --skip-planner
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_engine(mode: str):
    """Import the chosen pipeline's main.py as the top-level module `main`.

    Each pipeline imports its own sibling modules by bare name (`import
    config`, `import compositor`, ...), so only one engine can be loaded
    per process — that's fine here since a single CLI invocation only
    ever runs one mode.
    """
    pkg_dir = HERE / ("pipeline" if mode == "talking-head" else "pipeline_motion")
    sys.path.insert(0, str(pkg_dir))
    sys.modules.pop("main", None)
    engine = importlib.import_module("main")
    return engine, pkg_dir


def _resolve_script(pkg_dir: Path, script: Path | None, prompt: str | None) -> Path:
    if prompt is not None:
        cache_dir = pkg_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        script_path = cache_dir / f"_prompt_{int(time.time())}.txt"
        script_path.write_text(prompt.strip() + "\n")
        print(f"[allore] wrote --prompt text to {script_path}")
        return script_path
    return script


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Unified CLI for the talking-head and motion-graphics video pipelines.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--mode", required=True, choices=["talking-head", "motion"],
                    help="talking-head: script+video -> explainer video. motion: script(+audio) -> kinetic typography video.")

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--script", type=Path, help="Path to a script .txt file.")
    src.add_argument("--prompt", type=str, help="Raw text used directly as the script — no file needed.")

    p.add_argument("--out", type=Path, default=None, help="Output video path (default: <engine>/output/final*.mp4).")

    # talking-head only
    p.add_argument("--video", type=Path, default=None, help="[talking-head] talking-head footage (required for this mode).")
    p.add_argument("--candidates", type=int, default=None, help="[talking-head] Pinterest candidate images per beat.")
    p.add_argument("--mute-original-audio", action="store_true", help="[talking-head] discard the talking-head clip's own audio.")
    p.add_argument("--clips-dir", type=Path, default=None, help="[talking-head] directory of local screen-recording/product clips.")

    # motion only
    p.add_argument("--audio", type=Path, default=None, help="[motion] optional narration audio (mp3/wav); silent if omitted.")
    p.add_argument("--portrait", action="store_true", help="[motion] render 1080x1920 portrait instead of 1920x1080 landscape.")
    p.add_argument("--4k", dest="four_k", action="store_true", help="[motion] render true 4K instead of 1080p.")
    p.add_argument("--hq", action="store_true", help="[motion] high-quality encode (crf 16 slow); implied by --4k.")

    # shared
    p.add_argument("--skip-planner", action="store_true", help="Reuse existing beats.json instead of calling the LLM planner.")
    p.add_argument("--captions", action="store_true", help="Force captions on (motion mode default: off).")
    p.add_argument("--no-captions", action="store_true", help="Force captions off (talking-head mode default: on).")
    p.add_argument("--no-grade", action="store_true", help="Disable the grade/bloom/grain finishing pass.")
    p.add_argument("--music", type=str, default=None, help="Path to a music bed; looped and auto-ducked under narration.")
    p.add_argument("--target-seconds", type=float, default=None, help="Target runtime the AI director plans for.")

    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.mode == "talking-head":
        if not args.video:
            build_parser().error("--video is required for --mode talking-head")
        for flag, name in ((args.audio, "--audio"), (args.portrait, "--portrait"),
                            (args.four_k, "--4k"), (args.hq, "--hq")):
            if flag:
                print(f"[allore] warning: {name} is ignored in --mode talking-head", file=sys.stderr)
    else:
        if args.video:
            print("[allore] warning: --video is ignored in --mode motion", file=sys.stderr)
        if args.candidates is not None or args.clips_dir or args.mute_original_audio:
            print("[allore] warning: --candidates/--clips-dir/--mute-original-audio are ignored in --mode motion", file=sys.stderr)
        # These must be set before the engine's config.py is imported.
        if args.portrait:
            os.environ["PIPELINE_MOTION_PORTRAIT"] = "1"
        if args.four_k:
            os.environ["PIPELINE_4K"] = "1"
        if args.hq:
            os.environ["PIPELINE_HQ"] = "1"

    engine, pkg_dir = _load_engine(args.mode)
    script_path = _resolve_script(pkg_dir, args.script, args.prompt)

    if args.mode == "talking-head":
        out_path = args.out or (pkg_dir / "output" / "final.mp4")
        candidates = args.candidates if args.candidates is not None else engine.fetch_assets.CANDIDATES_PER_BEAT
        engine.run(
            script_path, args.video, out_path, candidates, args.skip_planner,
            mute_original_audio=args.mute_original_audio,
            captions_enabled=not args.no_captions,
            grade_enabled=not args.no_grade,
            music_path=args.music,
            target_seconds=args.target_seconds,
            clips_dir=args.clips_dir,
        )
    else:
        out_path = args.out or (pkg_dir / "output" / "final_mgfx.mp4")
        engine.run(
            script_path, out_path, args.audio, args.skip_planner,
            target_seconds=args.target_seconds,
            captions_enabled=args.captions,
            music_path=args.music,
            grade_enabled=not args.no_grade,
        )

    print(f"[allore] output: {out_path}")


if __name__ == "__main__":
    main()
