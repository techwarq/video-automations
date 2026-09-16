"""
Fullscreen motion-only compositor — chains kinetic clips (white/blue clean
typography) with per-beat xfade transitions, optional captions/grade, and
audio mastering. No talking head, no top/bottom zones — every beat is already
fullscreen at CANVAS_WIDTH x CANVAS_HEIGHT.
"""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config
import motion_gfx

def _is_kinetic(beat: dict) -> bool:
    return beat.get("visual_kind") in ("kinetic","clean") and bool(beat.get("kinetic") or beat.get("clean") or beat.get("mgfx"))

def _is_diagram(beat: dict) -> bool:
    return beat.get("visual_kind") == "diagram" and bool(beat.get("diagram"))

def _is_video_beat(beat: dict) -> bool:
    return beat.get("visual_kind") == "video_clip" and bool(beat.get("resolved_clip_path"))

def _prepare_video_clip(src: Path, w: int, h: int, duration: float) -> Path:
    out_dir = config.CACHE_DIR / "video_beats"
    out_dir.mkdir(parents=True, exist_ok=True)
    key_src = f"{src}|{w}x{h}|{duration:.3f}|{src.stat().st_mtime if src.exists() else 0}"
    out_path = out_dir / f"vid_{hashlib.md5(key_src.encode()).hexdigest()[:10]}_{src.stem}.mp4"
    if out_path.exists():
        return out_path
    fps = config.FPS
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
           f"crop={w}:{h},fps={fps},setsar=1")
    src_dur = _probe_duration(src)
    if src_dur >= duration + 0.1:
        graph = f"[0:v]{fit},trim=duration={duration:.3f},setpts=PTS-STARTPTS[v]"
    else:
        graph = (f"[0:v]{fit},split=2[vf][vrs];[vrs]reverse[vr];"
                 f"[vf][vr]concat=n=2:v=1:a=0,trim=duration={duration:.3f},setpts=PTS-STARTPTS[v]")
    cmd = [config.FFMPEG_BIN, "-y", "-v", "error", "-i", str(src),
           "-filter_complex", graph, "-map", "[v]",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-t", f"{duration:.3f}", str(out_path)]
    print(f"[compositor:motion] normalizing video clip {src.name} -> {out_path.name}")
    subprocess.run(cmd, check=True)
    return out_path

def _even(n: float) -> int:
    n = int(round(n)); return n if n%2==0 else n-1

def _escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\"); text = text.replace(":", "\\:"); text = text.replace("'", "’")
    return text

def _text_width(text: str, font_path: str, fontsize: int) -> int:
    font = ImageFont.truetype(font_path, fontsize)
    return max(font.getbbox(line)[2]-font.getbbox(line)[0] for line in text.split("\n"))

def _fit_text(text: str, font_path: str, base_fontsize: int, max_width: int) -> tuple[str,int]:
    fontsize = base_fontsize
    while fontsize > config.TEXT_MIN_FONTSIZE:
        if _text_width(text, font_path, fontsize) <= max_width:
            return text, fontsize
        fontsize -= 2
    fontsize = config.TEXT_MIN_FONTSIZE
    words = text.split()
    if len(words)>1 and _text_width(text, font_path, fontsize) > max_width:
        mid=len(words)//2
        wrapped=f"{' '.join(words[:mid])}\n{' '.join(words[mid:])}"
        if _text_width(wrapped, font_path, fontsize) <= max_width:
            return wrapped, fontsize
        return wrapped, fontsize
    return text, fontsize

def _probe_duration(path: Path) -> float:
    out = subprocess.run([config.FFPROBE_BIN,"-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())

def _transition_spec(beats: list[dict], i: int) -> tuple[str,float]:
    t = beats[i].get("transition") or {}
    ttype = t.get("type","fade")
    tdur=float(t.get("duration",config.CROSSFADE_DURATION))
    if ttype=="cut":
        ttype="fade"; tdur=config.TRANSITION_CUT_AS_FADE_SECONDS
    elif ttype not in config.VALID_XFADE_TYPES:
        print(f"[compositor:motion] warning unknown transition {ttype!r} -> fade"); ttype="fade"
    return ttype, tdur

def _chain_xfade(labels_durations: list[tuple[str,float]], beats: list[dict]) -> tuple[list[str],str,float]:
    lines=[]
    if len(labels_durations)==1:
        label,dur=labels_durations[0]; return lines,label,dur
    running_label, running_dur = labels_durations[0]
    for i in range(1,len(labels_durations)):
        next_label, next_dur = labels_durations[i]
        ttype,want=_transition_spec(beats,i-1)
        this_t=min(want, running_dur, next_dur)
        offset=running_dur - this_t
        out_label=f"xf{i}"
        lines.append(f"[{running_label}][{next_label}]xfade=transition={ttype}:duration={this_t:.3f}:offset={offset:.3f}[{out_label}]")
        running_dur=running_dur+next_dur-this_t
        running_label=out_label
    return lines, running_label, running_dur

def _build_finishing_filters(src_label: str) -> tuple[list[str],str]:
    lines=[]; label=src_label
    if config.GRADE_ENABLED:
        lut_path=Path(config.GRADE_LUT_PATH)
        if lut_path.exists(): grade=f"lut3d=file={lut_path}"
        else: grade=(f"eq=contrast={config.GRADE_CONTRAST}:saturation={config.GRADE_SATURATION}:gamma={config.GRADE_GAMMA},"
                     f"curves=all='{config.GRADE_CURVES_POINTS}',"
                     f"colorbalance=rs={config.GRADE_SHADOWS_RS}:bs={config.GRADE_SHADOWS_BS}:rm={config.GRADE_MIDTONES_RM}:bm={config.GRADE_MIDTONES_BM}:rh={config.GRADE_HIGHLIGHTS_RH}:bh={config.GRADE_HIGHLIGHTS_BH}")
        out=f"{src_label}_graded"; lines.append(f"[{label}]{grade}[{out}]"); label=out
    if config.BLOOM_ENABLED:
        a,b=f"{label}_ba",f"{label}_bb"; blur=f"{label}_blur"; out=f"{label}_bloom"
        lines.append(f"[{label}]split=2[{a}][{b}]")
        lines.append(f"[{b}]format=gbrp,gblur=sigma={config.BLOOM_SIGMA}[{blur}]")
        lines.append(f"[{a}]format=gbrp[{a}r];[{a}r][{blur}]blend=all_mode=screen:all_opacity={config.BLOOM_OPACITY},format=yuv420p[{out}]")
        label=out
    if config.FILM_GRAIN_STRENGTH>0:
        out=f"{label}_grain"; lines.append(f"[{label}]noise=alls={config.FILM_GRAIN_STRENGTH}:allf=t+u[{out}]"); label=out
    # vignette only if enabled strength>0; we apply always but angle can be disabled by checking if GRADE_ENABLED?
    # Keep simple: apply vignette always for subtle
    if config.GRADE_ENABLED or config.BLOOM_ENABLED or config.FILM_GRAIN_STRENGTH>0:
        out=f"{label}_vig"; lines.append(f"[{label}]vignette={config.VIGNETTE_ANGLE}[{out}]"); label=out
    return lines, label

def _build_caption_filters(beats: list[dict], src_label: str, canvas_w: int, canvas_h: int) -> tuple[list[str],str]:
    import captions as captions_mod
    if not config.CAPTIONS_ENABLED:
        return [], src_label
    groups=[]
    for beat in beats:
        if beat.get("caption_mode")=="off": continue
        groups.extend(captions_mod.build_captions(beat))
    if not groups: return [], src_label
    y_center=round(config.CAPTION_Y_CENTER_FRACTION*canvas_h)
    fade=config.CAPTION_FADE_IN; settle=config.CAPTION_SETTLE_PX
    lines=[]; label=src_label
    for i,g in enumerate(groups):
        text, fontsize = captions_mod.fit_caption_fontsize(g["text"], canvas_w)
        escaped=_escape_drawtext(text)
        s,e=g["start"],g["end"]
        p_in=f"min((t-{s:.3f})/{fade}\\,1)"
        eased_in=f"(1-pow(1-{p_in}\\,3))"
        y_expr=f"{y_center}-text_h/2+({settle}*(1-{eased_in}))"
        out_label=f"cap{i}"
        lines.append(f"[{label}]drawtext=fontfile='{config.CAPTION_FONT}':text='{escaped}':expansion=none:"
                     f"fontsize={fontsize}:fontcolor={config.CAPTION_ACCENT_COLOR if g['accent'] else config.CAPTION_TEXT_COLOR}:"
                     f"borderw={config.CAPTION_BORDER_W}:bordercolor={config.CAPTION_BORDER_COLOR}:"
                     f"shadowcolor={config.CAPTION_SHADOW_COLOR}:shadowx=0:shadowy={config.CAPTION_SHADOW_OFFSET_PX}:"
                     f"x='(w-text_w)/2':y='{y_expr}':alpha='min((t-{s:.3f})/{fade}\\,1)':"
                     f"enable='between(t\\,{s:.3f}\\,{e:.3f})'[{out_label}]")
        label=out_label
    return lines, label

def _voice_chain() -> str:
    return (f"highpass=f={config.AUDIO_HIGHPASS_HZ},"
            f"acompressor={config.AUDIO_COMPRESSOR},"
            f"deesser={config.AUDIO_DEESSER},"
            f"loudnorm={config.AUDIO_LOUDNORM},"
            f"aresample={config.AUDIO_TARGET_RATE}")

def _build_audio_filters(total_duration: float, audio_input_idx: Optional[int], music_input_idx: Optional[int], mute: bool=False) -> list[str]:
    D=total_duration; lines=[]
    # Determine input indices: if audio provided, its index = audio_input_idx
    has_audio = audio_input_idx is not None
    has_music = music_input_idx is not None

    if not has_audio and not has_music:
        if mute:
            lines.append(f"anullsrc=channel_layout=stereo:sample_rate={config.AUDIO_TARGET_RATE},atrim=duration={D:.3f}[aout]")
        else:
            # silent padded to duration
            lines.append(f"anullsrc=channel_layout=stereo:sample_rate={config.AUDIO_TARGET_RATE},atrim=duration={D:.3f}[aout]")
        return lines

    if has_music and not has_audio:
        fade_out=max(D-1.5,0)
        lines.append(f"[{music_input_idx}:a]aresample={config.AUDIO_TARGET_RATE},volume={config.MUSIC_VOLUME},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out:.3f}:d=1.5,atrim=duration={D:.3f},asetpts=PTS-STARTPTS[aout]")
        return lines

    if has_audio and not has_music:
        if mute:
            lines.append(f"anullsrc=channel_layout=stereo:sample_rate={config.AUDIO_TARGET_RATE},atrim=duration={D:.3f}[aout]")
        elif config.AUDIO_CHAIN_ENABLED:
            lines.append(f"[{audio_input_idx}:a]{_voice_chain()},apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[aout]")
        else:
            lines.append(f"[{audio_input_idx}:a]apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[aout]")
        return lines

    # both
    fade_out=max(D-1.5,0)
    lines.append(f"[{music_input_idx}:a]aresample={config.AUDIO_TARGET_RATE},volume={config.MUSIC_VOLUME},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out:.3f}:d=1.5,atrim=duration={D:.3f},asetpts=PTS-STARTPTS[mus]")
    if mute:
        lines.append(f"[mus]anull[aout]"); return lines
    lines.append(f"[{audio_input_idx}:a]asplit=2[vo][scraw]")
    lines.append(f"[scraw]aresample={config.AUDIO_TARGET_RATE}[sc]")
    if config.AUDIO_CHAIN_ENABLED:
        lines.append(f"[vo]{_voice_chain()},apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[vox]")
    else:
        lines.append(f"[vo]apad,atrim=0:{D:.3f},asetpts=PTS-STARTPTS[vox]")
    lines.append(f"[mus][sc]sidechaincompress={config.MUSIC_DUCK}[musduck]")
    lines.append(f"[vox][musduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
    return lines

def render(beats: list[dict], output_path: Path, audio_path: Path | None = None,
           total_duration: Optional[float]=None, mute_original_audio: bool=False,
           captions_enabled: Optional[bool]=None) -> None:
    import planner
    beats=sorted(beats, key=lambda b: b["start"])
    if any("transition" not in b for b in beats[:-1]):
        planner.assign_transitions(beats)
    # validate kinetic / diagram handling - no prepped_image needed for kinetic
    for i, b in enumerate(beats):
        if _is_diagram(b) or _is_kinetic(b) or _is_video_beat(b):
            continue
        if not b.get("prepped_image_path"):
            # fallback: try to render from on_screen_text as headline if image missing
            # Instead of failing, we will synthesize a kinetic headline on the fly
            print(f"[compositor:motion] beat {i} missing prepped_image — using kinetic headline fallback for '{b.get('on_screen_text') or b.get('narration_snippet')}'")
            # convert to kinetic on the fly
            b["visual_kind"]="kinetic"
            b["kinetic"]={"style":"light","layout":"headline","text": (b.get("on_screen_text") or b["narration_snippet"])[:120], "accent": None}
    for i in range(len(beats)-1):
        if abs(beats[i]["end"]-beats[i+1]["start"]) > 0.05:
            print(f"[compositor:motion] warning: gap between beat {i} and {i+1}")

    if total_duration is None:
        total_duration = max(b["end"] for b in beats)
    output_path=Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)

    # optional override for captions
    if captions_enabled is not None:
        config.CAPTIONS_ENABLED=captions_enabled

    w, h = config.CANVAS_WIDTH, config.CANVAS_HEIGHT
    w, h = _even(w), _even(h)

    cmd=[config.FFMPEG_BIN, "-y"]
    filter_lines: list[str]=[]
    card_labels: list[tuple[str,float]]=[]

    # Determine input indices: beat clips will be 0..n-1, then audio, then music
    # We need to first generate/fetch clips then add them as inputs.
    clip_paths = []
    for i, beat in enumerate(beats):
        beat_len = beat["end"]-beat["start"]
        if i < len(beats)-1:
            _, cf_out = _transition_spec(beats,i)
            duration = beat_len + cf_out
        else:
            duration = beat_len

        # Resolve clip for this beat
        if _is_kinetic(beat) or _is_diagram(beat):
            # fullscreen kinetic clip
            clip = motion_gfx.render_mgfx_clip(beat, w, h, duration)
            cmd += ["-i", str(clip)]
            clip_paths.append(clip)
            # No extra filter needed yet — xfade will handle
            # But we need to ensure fps and trim to exact duration (clip already correct, but normalize)
            # We'll create a label for this input's video stream
            # Use fps/trim/setpts to normalize
            label_in = f"v{i}"
            filter_lines.append(f"[{i}:v]fps={config.FPS},trim=duration={duration:.3f},setpts=PTS-STARTPTS[{label_in}]")
            card_labels.append((label_in, duration))
        elif _is_video_beat(beat):
            clip = _prepare_video_clip(Path(beat["resolved_clip_path"]), w, h, duration)
            cmd += ["-i", str(clip)]
            label_in = f"v{i}"
            filter_lines.append(f"[{i}:v]fps={config.FPS},trim=duration={duration:.3f},setpts=PTS-STARTPTS[{label_in}]")
            card_labels.append((label_in, duration))
        else:
            # image beat — looped still with optional zoompan, but fullscreen centered
            img_path = beat.get("prepped_image_path") or beat.get("selected_image") or beat.get("candidate_images",[None])[0]
            if not img_path or not Path(img_path).exists():
                # last fallback: render headline
                tmp_beat = {"id": beat["id"], "kinetic":{"style":"light","layout":"headline","text": (beat.get("on_screen_text") or beat.get("narration_snippet") or " ")[:120]}}
                clip = motion_gfx.render_mgfx_clip(tmp_beat, w, h, duration)
                cmd += ["-i", str(clip)]
                label_in = f"v{i}"
                filter_lines.append(f"[{i}:v]fps={config.FPS},trim=duration={duration:.3f},setpts=PTS-STARTPTS[{label_in}]")
                card_labels.append((label_in, duration))
                continue
            # scale to fill canvas with Ken Burns? For motion-only prefer simple scale/fill without card border
            # Use scale + crop to fill, then zoompan if motion specified? Keep simpler: centered still with fade
            # We'll add as looped image input then apply zoompan filter if motion not "none"
            cmd += ["-loop","1","-framerate",str(config.FPS),"-t",f"{duration:.3f}","-i", str(img_path)]
            label_in = f"v{i}"
            # Build zoompan or simple scale
            # For fullscreen we can do scale to fill + optional subtle zoom
            motion = beat.get("motion","zoom_in")
            # Reuse zoompan helper from original? Simplified: scale then loop
            # We replicate subtle zoom by using zoompan if motion in (zoom_in etc)
            # Use helper to generate zoompan expression then scale
            # But easier: just scale + crop and trim — no Ken Burns for fullscreen clean look
            # We'll just do scale -> crop
            filter_lines.append(f"[{len(clip_paths) if False else i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={config.FPS},setsar=1,trim=duration={duration:.3f},setpts=PTS-STARTPTS[{label_in}]")
            # Actually input index is i (since we appended sequentially)
            # Correct formula: input index = i (0-based beats)
            # So above should use [{i}:v]
            # We'll rebuild correctly: replace
            filter_lines[-1] = f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={config.FPS},setsar=1,trim=duration={duration:.3f},setpts=PTS-STARTPTS[{label_in}]"
            card_labels.append((label_in, duration))
            clip_paths.append(Path(img_path))

    # Now chain via xfade
    xfade_lines, top_label, top_dur = _chain_xfade(card_labels, beats)
    filter_lines.extend(xfade_lines)

    # Captions (if enabled) — on final video before finishing
    cap_lines, final_label = _build_caption_filters(beats, top_label, w, h)
    filter_lines.extend(cap_lines)

    finish_lines, final_label = _build_finishing_filters(final_label)
    filter_lines.extend(finish_lines)

    # Hooks fade in/out
    if beats and beats[0].get("role")=="hook":
        filter_lines.append(f"[{final_label}]fade=t=in:st=0:d={config.HOOK_FADE_IN_SECONDS:.2f}[hookfade]")
        final_label="hookfade"
    if beats and beats[-1].get("role")=="payoff":
        st=max(total_duration - config.PAYOFF_FADE_OUT_SECONDS, 0)
        filter_lines.append(f"[{final_label}]fade=t=out:st={st:.3f}:d={config.PAYOFF_FADE_OUT_SECONDS:.2f}[payofffade]")
        final_label="payofffade"
    filter_lines.append(f"[{final_label}]null[final]")

    # Audio handling
    audio_input_idx=None; music_input_idx=None
    n_video_inputs=len(beats)
    if audio_path and Path(audio_path).exists():
        audio_input_idx = n_video_inputs
        cmd += ["-i", str(audio_path)]
        n_video_inputs+=1
    if config.MUSIC_PATH and Path(config.MUSIC_PATH).exists():
        music_input_idx = n_video_inputs if audio_input_idx is not None else n_video_inputs
        # if we already added audio, music is next; else it's after video inputs
        if audio_input_idx is None:
            music_input_idx = len(beats)
        else:
            music_input_idx = len(beats)+1
        # But our cmd building order matters: we already appended audio, now append music
        if not (audio_path and Path(audio_path).exists() and music_input_idx==len(beats)):
            # we haven't added music yet
            pass
        # Simpler: recompute cmd music append
        # Check if music already added? We only added audio above. Need to add music now if exists.
        cmd += ["-stream_loop","-1","-i", str(Path(config.MUSIC_PATH))]
        # adjust music index to reflect actual position
        # after adding, its index is previous n_video_inputs
        # But we appended audio first if exists, so compute:
        # Actually cmd currently has video inputs 0..len(beats)-1, then audio at len(beats) if exists.
        # So music will be at len(beats)+(1 if audio else 0)
        music_input_idx = len(beats) + (1 if audio_path and Path(audio_path).exists() else 0)
    # Build audio filters
    # Note: if we used "-stream_loop -1" for music, its index is as computed, but ffmpeg needs -stream_loop before -i
    # We already did "-stream_loop -1 -i music" above correctly.
    # For audio mastering, handle both
    # If no audio but still need silent, _build_audio_filters handles it without input
    # Need to handle case where we added audio with normal -i (no loop) vs looped music

    filter_lines.extend(_build_audio_filters(total_duration, audio_input_idx, music_input_idx, mute=mute_original_audio))

    filter_complex=";".join(filter_lines)
    # Quality: use HQ/4K presets from config (crf 16 + slow for archival, 18 + medium for default)
    crf = str(getattr(config, "HQ_CRF", 18))
    preset = str(getattr(config, "HQ_PRESET", "medium"))
    # 4K benefits from higher bitrate ceiling even at same crf — libx264 handles it, but we also raise audio
    cmd += ["-filter_complex", filter_complex,
            "-map","[final]","-map","[aout]",
            "-t", f"{total_duration:.3f}",
            "-r", str(config.FPS),
            "-c:v","libx264","-pix_fmt","yuv420p","-color_range","tv","-preset",preset,"-crf",crf,
            "-c:a","aac","-b:a","192k",
            "-movflags","+faststart",str(output_path)]
    print(f"[compositor:motion] rendering {len(beats)} beats fullscreen {w}x{h} -> {output_path} ({total_duration:.1f}s)")
    print(f"[compositor:motion] filter_complex length {len(filter_complex)}")
    subprocess.run(cmd, check=True)
    print(f"[compositor:motion] done: {output_path}")

def render_from_files(beats_json_path: Path, output_path: Path, audio_path: Path | None = None, mute: bool=False) -> None:
    with open(beats_json_path) as f: beats=json.load(f)
    render(beats, Path(output_path), audio_path=Path(audio_path) if audio_path else None, mute_original_audio=mute)
