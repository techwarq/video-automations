"""
End-to-end CLI: script + talking-head video -> finished explainer video.

    python main.py --script script.txt --video talking_head.mp4 --out final.mp4
"""

import argparse
import sys
from pathlib import Path

import config
import planner
import fetch_assets
import select_images
import prep_images
import compositor


def _check_prereqs():
    problems = []
    if not config.OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY is not set (needed for planner.py and select_images.py).")
    if not Path(config.FFMPEG_BIN).exists():
        problems.append(f"ffmpeg not found at {config.FFMPEG_BIN} (set PIPELINE_FFMPEG_BIN).")
    if not Path(config.FFPROBE_BIN).exists():
        problems.append(f"ffprobe not found at {config.FFPROBE_BIN} (set PIPELINE_FFPROBE_BIN).")
    if problems:
        for p in problems:
            print(f"[main] ERROR: {p}", file=sys.stderr)
        sys.exit(1)


def run(script_path: Path, video_path: Path, out_path: Path, candidates_per_beat: int, skip_planner: bool,
        mute_original_audio: bool = False, captions_enabled: bool = True,
        grade_enabled: bool = True, music_path: str | None = None,
        target_seconds: float | None = None, clips_dir: Path | None = None):
    _check_prereqs()

    # Feature toggles (CLI overrides of config defaults) — read by the
    # planner/compositor at their stage.
    config.CAPTIONS_ENABLED = captions_enabled
    if not grade_enabled:
        config.GRADE_ENABLED = False
        config.BLOOM_ENABLED = False
        config.FILM_GRAIN_STRENGTH = 0
    if music_path:
        config.MUSIC_PATH = music_path
    if target_seconds:
        config.TARGET_DURATION_SECONDS = float(target_seconds)
    if clips_dir:
        config.CLIPS_DIR = Path(clips_dir)

    if not video_path.exists():
        print(f"[main] ERROR: talking-head video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    if skip_planner and config.BEATS_JSON_PATH.exists():
        print(f"[main] --skip-planner: reusing existing {config.BEATS_JSON_PATH}")
    else:
        print(f"[main] Stage 1/5: planner — AI director (hook/scene/asset/transition plan, "
              f"target {config.TARGET_DURATION_SECONDS:.0f}s)")
        planner.plan_from_file(script_path)

    print("[main] Stage 2/5: fetch_assets (Pinterest candidates + local clip resolution)")
    fetch_assets.fetch_from_file(count=candidates_per_beat)

    print("[main] Stage 3/5: select_images (vision scores, picks best, refetches weak pools)")
    select_images.select_from_file()

    print("[main] Stage 4/5: prep_images (crop/pad to card size)")
    prep_images.prep_from_file()

    print("[main] Stage 5/5: compositor (grade, captions, transitions, motion graphics, render)")
    with open(config.BEATS_JSON_PATH) as f:
        import json
        beats = json.load(f)

    missing = [b["id"] for b in beats
               if b.get("visual_kind") not in ("diagram", "video_clip")
               and not b.get("prepped_image_path")]
    if missing:
        print(f"[main] ERROR: beats {missing} have no prepped image (Pinterest search or selection "
              f"failed for them) — fix their image_query in {config.BEATS_JSON_PATH} and re-run with "
              f"--skip-planner, or remove them.", file=sys.stderr)
        sys.exit(1)

    video_missing = [b["id"] for b in beats
                     if b.get("visual_kind") == "video_clip" and not b.get("resolved_clip_path")]
    if video_missing:
        print(f"[main] ERROR: video beats {video_missing} have no resolved clip — fetch_assets should "
              f"have downgraded them to images. Check {config.CLIPS_DIR} and beats.json.", file=sys.stderr)
        sys.exit(1)

    compositor.render(beats, video_path, out_path, mute_original_audio=mute_original_audio)
    print(f"[main] done: {out_path}")


if __name__ == "__main__":
    parser_ = argparse.ArgumentParser(description="Generate a talking-head explainer video from a script.")
    parser_.add_argument("--script", required=True, type=Path)
    parser_.add_argument("--video", required=True, type=Path)
    parser_.add_argument("--out", default=str(config.OUTPUT_DIR / "final.mp4"), type=Path)
    parser_.add_argument("--candidates", type=int, default=fetch_assets.CANDIDATES_PER_BEAT,
                          help="Pinterest candidate images to fetch per beat.")
    parser_.add_argument("--skip-planner", action="store_true",
                          help="Reuse the existing beats.json instead of re-running the planner (for re-running later stages after fixing something by hand).")
    parser_.add_argument("--mute-original-audio", action="store_true",
                          help="Discard the talking-head video's own audio; output gets silence instead.")
    parser_.add_argument("--no-captions", action="store_true",
                          help="Disable word-synced kinetic captions.")
    parser_.add_argument("--no-grade", action="store_true",
                          help="Disable the global grade/bloom/grain finishing pass.")
    parser_.add_argument("--music", type=str, default=None,
                          help="Path to a music bed file; looped under the narration and auto-ducked.")
    parser_.add_argument("--target-seconds", type=float, default=None,
                          help="Target runtime the AI director plans for (default: config, 30s).")
    parser_.add_argument("--clips-dir", type=Path, default=None,
                          help="Directory of screen-recording/product clips the planner can schedule (default: assets/videos).")
    args = parser_.parse_args()

    run(args.script, args.video, args.out, args.candidates, args.skip_planner,
        mute_original_audio=args.mute_original_audio,
        captions_enabled=not args.no_captions,
        grade_enabled=not args.no_grade,
        music_path=args.music,
        target_seconds=args.target_seconds,
        clips_dir=args.clips_dir)
