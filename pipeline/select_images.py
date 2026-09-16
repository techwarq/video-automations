"""
For each beat, send its candidate images to Qwen3.7 Flash (vision, via
OpenRouter) along with the narration_snippet and have it pick the single
best match. Adds `selected_image` (local path) and `focus_point` ({x,y} as
0-1 fractions of the image, or null) to each beat, and logs reasoning to
selection_log.json for auditing.

Beyond picking, this stage now runs a vision-driven quality loop:
  - Every candidate is scored (relevance / clarity / composition /
    authenticity, 0-10 each) so selection decisions are auditable numbers,
    not vibes.
  - If even the winner's overall score is under config.SELECT_QUALITY_FLOOR,
    the model proposes a refined image_query; we re-search Pinterest with it,
    merge non-duplicate results into the pool (fetch_assets.add_candidates),
    and re-select. Weak image pools self-heal once instead of shipping a
    mediocre still.

focus_point is what makes this more than a slideshow: it's the pixel
location of the specific detail the narration line is actually about (a
labeled part in a diagram, a specific object in the frame). prep_images.py
uses it to crop toward that detail instead of blind-centering, and to draw
a pointer arrow for `label`-style beats; compositor.py uses it to aim the
eased Ken Burns zoom/pan at that point instead of the image's dead center.
"""

import base64
import io
import json
import re
import time
from pathlib import Path

from openai import OpenAI
from PIL import Image

import config
import fetch_assets
from image_source import pinterest_search

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1)
    return json.loads(raw)

VISION_MAX_DIM = 1024  # downscale before sending to control API cost/latency

SYSTEM_PROMPT = """You are selecting the single best reference image for one beat of a short \
explainer video, and pinpointing exactly where in that image the narration line is pointing.

You'll be shown several candidate images (numbered) and the line of narration they need to \
visually support.

Step 1 — score every candidate from 0-10 on each axis:
- relevance: does it concretely match what the line is describing?
- clarity: is the subject legible at a glance, not confusing/busy?
- composition: on-screen text will be overlaid, so does it have open/calm areas rather than a \
wall of competing detail?
- authenticity: real photo/diagram vs generic stock look, low quality, or watermarked?

Step 2 — pick the winner (highest overall), then judge the POOL: if even the winner scores below \
{quality_floor}/10 overall, set quality_ok=false AND write a refined_query — a more concrete, \
more literal search term that would surface better images (usually: drop editorializing adjectives, \
name the exact object, add a medium word like "diagram", "photo", "cross section"). If the pool is \
good enough, quality_ok=true and refined_query=null.

BRAND CHECK (non-negotiable): if the narration line or query names a specific real-world \
company/product/person, set brand_match=true ONLY if the winning candidate actually depicts \
THAT brand (its logo, product, or official identity). A different brand's logo — however \
similar in style — is an automatic reject: set brand_match=false AND make refined_query \
"<brand name> logo" (brand name verbatim). If no specific brand is named, brand_match=true.

Step 3 — locate the focus point in the WINNING image. Find the ONE specific detail the narration \
line is describing (a labeled component in a diagram, a specific object, a region showing the \
described phenomenon). Give its location as a fraction of the image's width and height (0.0-1.0, \
origin top-left). This drives an arrow annotation and an eased zoom-in, so it must be the exact \
spot a viewer's eye should land on — not the center by default, not a vague area. If the image \
genuinely has no single relevant focal detail (broad establishing shot), set focus_point to null.

Respond with ONLY a raw JSON object (no markdown fences, no commentary):
{"scores": [{"index": <int>, "relevance": <0-10>, "clarity": <0-10>, "composition": <0-10>, \
"authenticity": <0-10>}, ...],
"selected_index": <int, 0-based index of the best candidate>,
"focus_point": {"x": <float 0-1>, "y": <float 0-1>} or null,
"brand_match": <bool>,
"quality_ok": <bool>,
"refined_query": "<string>" or null,
"reasoning": "<1-2 sentence explanation of the pick and the focus point>"}
"""


def _system_prompt() -> str:
    return SYSTEM_PROMPT.replace("{quality_floor}", str(config.SELECT_QUALITY_FLOOR))


def _prep_image_for_api(path: str) -> str:
    """Returns a data: URI (OpenAI/OpenRouter image_url format)."""
    img = Image.open(path)
    img = img.convert("RGB")
    img.thumbnail((VISION_MAX_DIM, VISION_MAX_DIM))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _parse_focus_point(raw: dict) -> dict | None:
    fp = raw.get("focus_point")
    if not fp:
        return None
    try:
        x, y = float(fp["x"]), float(fp["y"])
        if 0 <= x <= 1 and 0 <= y <= 1:
            return {"x": x, "y": y}
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _parse_scores(raw: dict, n_candidates: int) -> list[dict]:
    scores = []
    for s in raw.get("scores", []) or []:
        try:
            idx = int(s.get("index"))
            if 0 <= idx < n_candidates:
                scores.append({
                    "index": idx,
                    "relevance": float(s.get("relevance", 0)),
                    "clarity": float(s.get("clarity", 0)),
                    "composition": float(s.get("composition", 0)),
                    "authenticity": float(s.get("authenticity", 0)),
                })
        except (TypeError, ValueError):
            continue
    return scores


def _winner_overall(scores: list[dict], selected_index: int) -> float | None:
    for s in scores:
        if s["index"] == selected_index:
            axes = [s[k] for k in ("relevance", "clarity", "composition", "authenticity")]
            return sum(axes) / len(axes)
    return None


def select_for_beat(client: OpenAI, beat: dict, prefer_new: bool = False) -> dict:
    """
    One vision call over the current candidate pool. Returns selection plus
    quality verdict:
      {"selected_image", "focus_point", "reasoning", "scores",
       "quality_ok", "refined_query", "brand_match"}

    prefer_new: after a refetch, the fresh candidates sit at the END of
    candidate_images — show those instead of the head of the pool, otherwise
    re-select rounds would review the exact same images that already failed.
    """
    pool = beat.get("candidate_images", [])
    if not pool:
        return {"selected_image": None, "focus_point": None,
                 "reasoning": "No candidate images were found for this beat.",
                 "scores": [], "quality_ok": False, "refined_query": None,
                 "brand_match": None}
    candidates = pool[-config.SELECT_MAX_CANDIDATES:] if prefer_new else pool[:config.SELECT_MAX_CANDIDATES]

    content = [
        {"type": "text", "text": f"Narration line for this beat:\n\"{beat['narration_snippet']}\"\n\nCandidates:"}
    ]
    for i, path in enumerate(candidates):
        try:
            data_uri = _prep_image_for_api(path)
        except Exception as e:
            print(f"[select_images] beat {beat['id']}: could not read candidate {i} ({path}): {e}")
            continue
        content.append({"type": "text", "text": f"Candidate {i}:"})
        content.append({"type": "image_url", "image_url": {"url": data_uri}})

    max_attempts = 4
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=config.VISION_MODEL,
                max_tokens=10000,  # Qwen3.7 Flash burns ~7-7.5k tokens on internal reasoning for a 3-image compare
                messages=[
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": content},
                ],
            )
            raw = (response.choices[0].message.content or "").strip()
            if not raw:
                # Empty content with no exception is what rate-limiting/transient
                # provider hiccups look like on OpenRouter — worth a retry rather
                # than immediately treating it as "the model has no opinion."
                raise ValueError("empty response content")

            parsed = _extract_json(raw)
            idx = int(parsed["selected_index"])
            if not (0 <= idx < len(candidates)):
                raise ValueError(f"selected_index {idx} out of range for {len(candidates)} candidates")

            refined = parsed.get("refined_query") or None
            return {
                "selected_image": candidates[idx],
                "focus_point": _parse_focus_point(parsed),
                "reasoning": parsed.get("reasoning", ""),
                "scores": _parse_scores(parsed, len(candidates)),
                # Older/looser model responses may omit the verdicts — assume
                # OK so schema drift degrades to plain selection, not a crash.
                "quality_ok": bool(parsed.get("quality_ok", True)),
                "brand_match": bool(parsed.get("brand_match", True)),
                "refined_query": refined.strip() if isinstance(refined, str) and refined.strip() else None,
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_error = e
            if attempt < max_attempts:
                backoff = 2 ** attempt  # 2s, 4s, 8s
                print(f"[select_images] beat {beat['id']}: attempt {attempt}/{max_attempts} failed ({e}), "
                      f"retrying in {backoff}s...")
                time.sleep(backoff)

    print(f"[select_images] beat {beat['id']}: all {max_attempts} attempts failed ({last_error}), "
          f"defaulting to first candidate, no focus point.")
    return {"selected_image": candidates[0], "focus_point": None,
             "reasoning": f"Fallback: model response unparseable after {max_attempts} attempts ({last_error}).",
             "scores": [], "quality_ok": False, "refined_query": None, "brand_match": None}


def _refetch_with_refined_query(beat: dict, refined_query: str) -> int:
    """Re-searches Pinterest with the vision-proposed query and merges new,
    non-duplicate candidates into the beat. Returns how many were added."""
    urls = pinterest_search(refined_query, count=fetch_assets.CANDIDATES_PER_BEAT)
    if not urls:
        return 0

    import tempfile
    paths = []
    with tempfile.TemporaryDirectory(dir=str(config.CACHE_DIR)) as tmp:
        tmp_dir = Path(tmp)
        for i, url in enumerate(urls):
            dest = tmp_dir / f"refined_{i}.{fetch_assets._extension_from_url(url)}"
            if fetch_assets._download(url, dest):
                paths.append(dest)
        added = fetch_assets.add_candidates(beat, [str(p) for p in paths])
    if added:
        beat.setdefault("refinements", []).append({"query": refined_query, "added": added})
    return added


def _process_beat(client: OpenAI, beat: dict) -> dict:
    """
    Vision-select one beat, including the vision-driven self-heal loop
    (weak pool or wrong-brand winner -> refined query -> re-search ->
    re-select over the NEW candidates). Returns the result dict; mutates
    beat with the final selection.

    Hard rule: if the beat names a brand and no round produced a brand_match,
    the selection is withdrawn entirely (selected_image=None) — shipping a
    different company's logo is worse than shipping no image (main.py will
    refuse to render and point at the beat's image_query).
    """
    result = select_for_beat(client, beat)
    rounds_used = 0
    while (result["selected_image"] and rounds_used < config.SELECT_REFETCH_ROUNDS
           and (result.get("brand_match") is False
                or (not result["quality_ok"] and result.get("refined_query")))):
        refined_q = result.get("refined_query")
        if result.get("brand_match") is False and not refined_q:
            # Model flagged the brand but gave no query — reuse the beat's own.
            refined_q = beat.get("image_query")
        if not refined_q:
            break
        print(f"[select_images] beat {beat['id']}: pool weak"
              f"{'/wrong brand' if result.get('brand_match') is False else ''} per vision — "
              f"refetching with refined query {refined_q!r}")
        added = _refetch_with_refined_query(beat, refined_q)
        rounds_used += 1
        if not added:
            print(f"[select_images] beat {beat['id']}: refined search added nothing new — keeping original pick.")
            break
        time.sleep(config.VISION_CALL_SPACING_SECONDS)
        print(f"[select_images] beat {beat['id']}: {added} new candidates — re-selecting over those.")
        result = select_for_beat(client, beat, prefer_new=True)

    if result.get("brand_match") is False and result.get("selected_image"):
        print(f"[select_images] beat {beat['id']}: HARD REJECT — winner depicts a different brand; "
              f"withdrawing selection (fix image_query in beats.json and re-run).")
        result["selected_image"] = None
        result["focus_point"] = None
        result["quality_ok"] = False
        result["reasoning"] = (result.get("reasoning", "") +
                               " | HARD REJECT: no candidate depicted the named brand.").strip()
    result["refetch_rounds_used"] = rounds_used
    return result


def _log_entry(beat: dict, result: dict) -> dict:
    return {
        "beat_id": beat["id"],
        "image_query": beat.get("image_query"),
        "narration_snippet": beat["narration_snippet"],
        "candidates": beat.get("candidate_images", []),
        "selected_image": result.get("selected_image"),
        "focus_point": result.get("focus_point"),
        "scores": result.get("scores", []),
        "quality_ok": result.get("quality_ok"),
        "brand_match": result.get("brand_match"),
        "refined_query": result.get("refined_query"),
        "refetch_rounds_used": result.get("refetch_rounds_used", 0),
        "reasoning": result.get("reasoning", ""),
    }


def _skip_entry(beat: dict, reason: str) -> tuple[dict, dict]:
    beat["selected_image"] = None
    beat["focus_point"] = None
    result = {"selected_image": None, "focus_point": None, "reasoning": reason,
              "scores": [], "quality_ok": None, "refined_query": None, "refetch_rounds_used": 0}
    return result, _log_entry(beat, result)


def select_all(beats: list[dict],
               checkpoint: tuple[Path, Path] | None = None) -> tuple[list[dict], list[dict]]:
    """
    Vision-selects every image beat. Runs SELECT_WORKERS beats in parallel
    (calls are IO-bound) and checkpoints beats.json + the selection log after
    each completion, so an interrupted run resumes where it left off
    (beats with a `selected_image` key already present are skipped).
    """
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set — required for select_images.py.")
    client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)

    log: dict[int, dict] = {}
    todo: list[int] = []
    for beat in beats:
        if "selected_image" in beat:
            print(f"[select_images] beat {beat['id']}: already selected — skipping (resume).")
            continue
        if beat.get("visual_kind") == "diagram":
            result, entry = _skip_entry(beat, "Diagram beat — rendered by motion_gfx, no image selection.")
            log[beat["id"]] = entry
            print(f"[select_images] beat {beat['id']}: diagram beat — skipped.")
            continue
        if beat.get("visual_kind") == "video_clip" and beat.get("resolved_clip_path"):
            result, entry = _skip_entry(beat, f"Video clip beat — using {beat['resolved_clip_path']}.")
            log[beat["id"]] = entry
            print(f"[select_images] beat {beat['id']}: video clip beat — skipped.")
            continue
        todo.append(beat["id"])

    by_id = {b["id"]: b for b in beats}

    def _save_checkpoint() -> None:
        if checkpoint:
            beats_path, log_path = checkpoint
            with open(beats_path, "w") as f:
                json.dump(beats, f, indent=2)
            with open(log_path, "w") as f:
                json.dump([log[k] for k in sorted(log)], f, indent=2)

    def _work(beat_id: int) -> None:
        beat = by_id[beat_id]
        if todo.index(beat_id) > 0 or log:
            time.sleep(config.VISION_CALL_SPACING_SECONDS)
        result = _process_beat(client, beat)
        beat["selected_image"] = result["selected_image"]
        beat["focus_point"] = result["focus_point"]
        log[beat["id"]] = _log_entry(beat, result)
        overall = _winner_overall(result.get("scores", []), _safe_index(beat, result))
        overall_s = f"{overall:.1f}/10" if overall is not None else "?"
        print(f"[select_images] beat {beat['id']}: selected {result['selected_image']!r} "
              f"(overall {overall_s}) focus_point={result['focus_point']} — {result['reasoning']}")
        _save_checkpoint()

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, config.SELECT_WORKERS)) as pool:
        list(pool.map(_work, todo))

    _save_checkpoint()
    ordered_log = [log[k] for k in sorted(log)]
    return beats, ordered_log


def _safe_index(beat: dict, result: dict) -> int:
    candidates = beat.get("candidate_images", []) or []
    sel = result.get("selected_image")
    try:
        return candidates.index(sel)
    except ValueError:
        return -1


def select_from_file(beats_json_path: Path = config.BEATS_JSON_PATH,
                      log_path: Path = config.SELECTION_LOG_PATH) -> list[dict]:
    with open(beats_json_path) as f:
        beats = json.load(f)
    beats, log = select_all(beats, checkpoint=(beats_json_path, log_path))
    with open(beats_json_path, "w") as f:
        json.dump(beats, f, indent=2)
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    return beats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Vision-select the best candidate image per beat.")
    parser.add_argument("--beats", default=str(config.BEATS_JSON_PATH))
    parser.add_argument("--log", default=str(config.SELECTION_LOG_PATH))
    args = parser.parse_args()

    select_from_file(Path(args.beats), Path(args.log))
