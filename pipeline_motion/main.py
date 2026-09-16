"""
Motion-only CLI: script -> kinetic typography video (no talking head).

    python main.py --script script.txt --out final.mp4
    python main.py --script script.txt --audio narration.mp3 --out final.mp4
    python main.py --script script.txt --out vertical.mp4 --portrait
"""

import argparse
import sys
from pathlib import Path
import os

# Allow quality/orientation flags to set env before importing config
_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument("--portrait", action="store_true")
_early_parser.add_argument("--4k", dest="four_k", action="store_true")
_early_parser.add_argument("--hq", action="store_true", help="High quality (crf 16 slow)")
_early_args, _ = _early_parser.parse_known_args()
if _early_args.portrait:
    os.environ["PIPELINE_MOTION_PORTRAIT"] = "1"
if _early_args.four_k:
    os.environ["PIPELINE_4K"] = "1"
if _early_args.hq:
    os.environ["PIPELINE_HQ"] = "1"

import config
import planner
import compositor

def _check_prereqs(skip_planner: bool = False):
    problems=[]
    if not skip_planner and not config.OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY not set (needed for planner). Use --skip-planner with an existing beats.json, or set OPENROUTER_API_KEY.")
    if not Path(config.FFMPEG_BIN).exists():
        problems.append(f"ffmpeg not found at {config.FFMPEG_BIN} (set PIPELINE_FFMPEG_BIN).")
    if not Path(config.FFPROBE_BIN).exists():
        problems.append(f"ffprobe not found at {config.FFPROBE_BIN} (set PIPELINE_FFPROBE_BIN).")
    if problems:
        for p in problems: print(f"[main:motion] ERROR: {p}", file=sys.stderr)
        sys.exit(1)

def run(script_path: Path, out_path: Path, audio_path: Path | None,
        skip_planner: bool, target_seconds: float | None,
        captions_enabled: bool, music_path: str | None,
        grade_enabled: bool):
    _check_prereqs(skip_planner=skip_planner)
    # Overrides
    if target_seconds:
        config.TARGET_DURATION_SECONDS = float(target_seconds)
    if music_path:
        config.MUSIC_PATH = music_path
    if not grade_enabled:
        config.GRADE_ENABLED=False; config.BLOOM_ENABLED=False; config.FILM_GRAIN_STRENGTH=0
    config.CAPTIONS_ENABLED = captions_enabled

    if skip_planner and config.BEATS_JSON_PATH.exists():
        print(f"[main:motion] --skip-planner: reusing {config.BEATS_JSON_PATH}")
    else:
        print(f"[main:motion] Stage 1/2: planner (kinetic director, target {config.TARGET_DURATION_SECONDS:.0f}s, {'portrait 9:16' if config.PORTRAIT else 'landscape 16:9'} {config.CANVAS_WIDTH}x{config.CANVAS_HEIGHT} {'4K' if getattr(config, 'FOUR_K', False) else '1080p'} {'HQ' if getattr(config, 'HQ', False) else ''})")
        planner.plan_from_file(script_path)

    # Auto-detect if beats contain image/video beats needing asset fetching
    import json
    with open(config.BEATS_JSON_PATH) as f:
        beats=json.load(f)
    needs_images = any(b.get("visual_kind")=="image" and not b.get("prepped_image_path") for b in beats)
    needs_video = any(b.get("visual_kind")=="video_clip" and not b.get("resolved_clip_path") for b in beats)
    if needs_images or needs_video:
        print("[main:motion] Note: beats contain image/video_clip beats but motion-only pipeline is image-optional.")
        print("[main:motion] Trying to fetch Pinterest candidates for those beats (if configured)...")
        try:
            # Reuse pipeline's fetch/select/prep if available (symlink or import via path)
            # We import from sibling pipeline directory
            import sys
            sys.path.insert(0, str(config.PROJECT_ROOT.parent / "pipeline"))
            import fetch_assets as fetch_mod
            import select_images as select_mod
            import prep_images as prep_mod
            # Need to ensure those modules read this pipeline_motion's beats.json — they use config.BEATS_JSON_PATH
            # So we temporarily monkey-patch their config reference? Easiest: copy beats.json to pipeline/beats.json, run steps, then copy back
            pipeline_beats = config.PROJECT_ROOT.parent / "pipeline" / "beats.json"
            # Backup existing if needed
            import shutil
            # Keep motion beats as source of truth
            # For fetch/select/prep we need to use pipeline config paths, but we can just run them in pipeline_motion context
            # Simpler: run fetch using pipeline_motion's own config by importing fetch_assets from pipeline but overriding its config
            # Hack: set pipeline config BEATS_JSON to motion beats
            import config as pipeline_config
            # Actually fetch_assets imports config from pipeline, not motion, so they'd look at pipeline/beats.json
            # We'll mirror beats to pipeline
            shutil.copy(config.BEATS_JSON_PATH, pipeline_beats)
            print(f"[main:motion] mirrored beats to {pipeline_beats} for asset fetch")
            fetch_mod.fetch_from_file(count=8)
            select_mod.select_from_file()
            prep_mod.prep_from_file()
            # Copy results back
            shutil.copy(pipeline_beats, config.BEATS_JSON_PATH)
            # Also need to copy prepped images? prep_images writes to pipeline/assets — we need to copy mapping
            # The beats now have prepped_image_path pointing to pipeline/assets — that's okay if compositor can read it
            # But to be clean, copy those files to motion assets
            with open(config.BEATS_JSON_PATH) as mf: updated=json.load(mf)
            for b in updated:
                p = b.get("prepped_image_path")
                if p and Path(p).exists():
                    # If path is inside pipeline/assets, mirror to pipeline_motion/assets
                    src=Path(p)
                    dst=config.ASSETS_DIR / src.name
                    if not dst.exists():
                        shutil.copy(src, dst)
                        b["prepped_image_path"]=str(dst)
            with open(config.BEATS_JSON_PATH,"w") as out: json.dump(updated,out,indent=2)
            print("[main:motion] image fetch completed, beats updated")
        except Exception as e:
            print(f"[main:motion] warning: image fetch failed ({e}) — falling back to kinetic headline for those beats.")
            # Downgrade image beats to kinetic headline so compositor doesn't fail
            import json as js
            with open(config.BEATS_JSON_PATH) as f: beats=js.load(f)
            for b in beats:
                if b.get("visual_kind")=="image" and not b.get("prepped_image_path"):
                    b["visual_kind"]="kinetic"
                    b["kinetic"]={"style":"light","layout":"headline","text": (b.get("on_screen_text") or b.get("narration_snippet",""))[:120], "accent": None}
                    b["on_screen_text"]=None
            with open(config.BEATS_JSON_PATH,"w") as f: js.dump(beats,f,indent=2)

    print("[main:motion] Stage 2/2: compositor (fullscreen kinetic render)")
    with open(config.BEATS_JSON_PATH) as f: beats=json.load(f)
    # Determine audio path
    ap = Path(audio_path) if audio_path else None
    if ap and not ap.exists():
        print(f"[main:motion] warning: audio not found {ap} — rendering silent")
        ap=None
    compositor.render(beats, out_path, audio_path=ap, mute_original_audio=False, captions_enabled=captions_enabled)
    print(f"[main:motion] done: {out_path}")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Generate motion-only kinetic typography video from script.")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--audio", required=False, type=Path, default=None, help="Optional narration audio (mp3/wav). If omitted, silent video timed to script duration.")
    parser.add_argument("--out", default=str(config.OUTPUT_DIR / "final_mgfx.mp4"), type=Path)
    parser.add_argument("--skip-planner", action="store_true", help="Reuse existing beats.json")
    parser.add_argument("--target-seconds", type=float, default=None)
    parser.add_argument("--captions", action="store_true", help="Burn word-synced captions (off by default for motion-only)")
    parser.add_argument("--no-grade", action="store_true", help="Disable grade/bloom/grain (already off by default)")
    parser.add_argument("--music", type=str, default=None, help="Music bed path")
    parser.add_argument("--portrait", action="store_true", help="Render 1080x1920 portrait instead of 1920x1080 landscape")
    parser.add_argument("--4k", dest="four_k", action="store_true", help="Render 3840x2160 4K (or 2160x3840 portrait-4K) — true 4K from vector, not upscale")
    parser.add_argument("--hq", action="store_true", help="High-quality 1080p: crf 16 slow (default 18 medium); implied by --4k")
    args=parser.parse_args()

    run(args.script, args.out, args.audio, args.skip_planner,
        target_seconds=args.target_seconds,
        captions_enabled=args.captions,
        music_path=args.music,
        grade_enabled=not args.no_grade)
