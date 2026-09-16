"""
Script -> full edit plan -> beats.json.

Planner v2 is the "AI director": one Qwen3.7-Flash call reads the script and
plans the ENTIRE edit the way a short-form reel editor would —
  - story structure: HOOK (first beat grabs in <=2s), BODY, PAYOFF (closer)
  - per beat: what the viewer sees (Pinterest image / hand-drawn animated
    diagram / local screen-recording clip), in which second, with which
    camera move, which outgoing transition, which on-screen text
  - pacing hints per beat (clamped so captions stay locked to the voice)

Python then deterministically executes that plan: it derives start/end times
(word-count based, nudged by the planner's pacing hints within PACE_MIN/
PACE_MAX so caption sync never drifts far), validates/coerces every field,
and writes beats.json. Deterministic stages downstream (fetch, select, prep,
motion_gfx, compositor) never make creative decisions — the plan is law.
"""

import json
import re
from pathlib import Path

from openai import OpenAI

import config

VALID_TEXT_STYLES = {"headline", "stat", "label"}
VALID_MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right"}
VALID_VISUAL_KINDS = {"image", "diagram", "video_clip"}
VALID_ROLES = {"hook", "body", "payoff"}
VALID_DIAGRAM_STYLES = {"dark", "whiteboard"}
VALID_NODE_SHAPES = {"rect", "oval", "plain"}
VALID_ARROW_PATHS = {"straight", "elbow"}
VALID_CAPTION_MODES = {"on", "off"}

# Transitions the planner may schedule (compositor maps "cut" to a 2-frame
# fade). The palette is injected into the prompt with editorial guidance.
TRANSITION_MENU = [
    ("cut", 0.07, "hard cut — punchy, use after the hook line or between rapid beats"),
    ("fade", 0.25, "neutral default dissolve"),
    ("fadeblack", 0.32, "dip to black — a new thought starts"),
    ("fadewhite", 0.32, "dip to white — impact/emphasis moment"),
    ("dissolve", 0.35, "slow dissolve — time passing"),
    ("smoothleft", 0.30, "directional wipe — forward momentum"),
    ("smoothright", 0.30, "directional wipe — forward momentum"),
    ("slideleft", 0.28, "slide — playful energy"),
    ("slideright", 0.28, "slide — playful energy"),
    ("circleopen", 0.34, "circle reveal — spotlight a detail"),
    ("hblur", 0.28, "whip-pan blur — high energy, scene change"),
    ("zoomin", 0.30, "zoom punch — slam into the next visual"),
    ("distance", 0.30, "3D push — dramatic shift"),
    ("squeezev", 0.28, "vertical squeeze — snappy rhythm"),
]
VALID_TRANSITIONS = {t[0] for t in TRANSITION_MENU}


def _transition_menu_text() -> str:
    return "\n".join(f'  - "{n}" ({d}s): {desc}' for n, d, desc in TRANSITION_MENU)


SYSTEM_PROMPT = """You are a top-tier short-form reel director and editor (Vox / Johnny Harris / \
top-tier product-promo style). You read a narration script and plan the ENTIRE edit as a JSON \
array of beats. The pipeline executes your plan literally — every field you write becomes a \
real edit decision. Target total runtime: {target_seconds} seconds.

STORY STRUCTURE (non-negotiable):
- Beat 1 (sometimes 1-2) is the HOOK: role="hook". It must stop the scroll — biggest claim, \
boldest visual, punchy on_screen_text. Keep it under ~2.5s of narration.
- The last beat is the PAYOFF: role="payoff" (CTA, conclusion, or the strongest line).
- Everything between is role="body".

NARRATION (non-negotiable):
- The `narration_snippet` fields, concatenated IN ORDER with single spaces, must reconstruct \
the ENTIRE input script EXACTLY (every word, nothing skipped/added/reordered). Captions are \
rendered word-synced from these snippets, so they must match the speech as-is.
- Split at natural sentence/clause boundaries. A beat's narration is usually 4-14 words; \
hook and payoff can be shorter.
- `duration_hint`: your editorial guess of how many seconds this line takes on screen. \
Python times captions from actual word counts; your hint nudges pacing within ±15%.

VISUALS — choose ONE `visual_kind` per beat:
1. "image" — a still from Pinterest image search. For CONCRETE visual subjects (a product, \
a place, a person, a screenshot-like photo). Always give `image_query` (short, concrete, \
visually specific — never abstract). Even diagram/video beats should include a fallback \
`image_query` in case their primary visual can't be sourced.
2. "diagram" — an animated hand-drawn whiteboard sketch YOU design. Best for processes, flows, \
comparisons, systems, "how it works" — usually stronger than a stock photo. Set \
`image_query` too (fallback), `on_screen_text` null (the diagram carries the text).
   - `diagram.style`: "dark" (white ink on dark) or "whiteboard" (dark ink on off-white). \
Vary them; "dark" for tech/schematic, "whiteboard" for schoolhouse/explainer.
   - `diagram.nodes`: 2-5 boxes/ovals/bare labels. x,y = CENTER fractions (0-1, top-left \
origin); w,h = size fractions. shape: "rect" | "oval" | "plain". emph:true = accent color, \
use on 1-2 elements max. Labels SHORT (1-3 words, uppercase). Keep everything inside 0.08-0.92, \
no overlaps, leave >=0.08 gaps for arrows. List in draw order.
   - `diagram.arrows`: connect node ids, path "straight"|"elbow", optional 1-3 word `label`.
   - `diagram.callouts`: free text; big:true = huge stat text.
3. "video_clip" — a clip from the local recordings library (e.g. software working, a product \
demo, gameplay). Use when showing the thing IN MOTION beats any still. `video_query` = short \
keywords that would match the clip's FILENAME (e.g. "editor demo", "app screen recording"). \
Also give fallback `image_query`. If no clip matches, the pipeline silently falls back to the image.

MOTION (image beats only): `motion` = "zoom_in" | "zoom_out" | "pan_left" | "pan_right". \
Vary consecutive beats — never the same motion twice in a row.

TRANSITIONS — `transition` on every beat except the last: how it cuts INTO the next beat. \
Pick from the menu below with editorial intent (cut after the hook, whip/zoom for energy \
spikes, fadeblack for topic shifts, slow dissolve before the payoff):
{transition_menu}

ON-SCREEN TEXT (image beats): `on_screen_text` = 2-5 word kinetic phrase pulled from the line \
(key noun/number), or null to let the visual breathe. `text_style`: "headline" (bold claim), \
"stat" (big number), "label" (small annotation). The HOOK must have on_screen_text.

CAPTIONS: word-synced captions are rendered over the whole video from the narration. \
`caption_mode`: "on" (default) or "off" — set "off" only on the hook if its big on_screen_text \
would fight the captions.

Respond with ONLY a raw JSON array (no markdown fences, no commentary), objects with exactly:
narration_snippet, role, visual_kind, image_query, video_query, on_screen_text, text_style, \
motion, transition {{"type","duration"}}, caption_mode, duration_hint,
plus `diagram` object for diagram beats. Use null for fields that don't apply.
"""


def _system_prompt() -> str:
    return (SYSTEM_PROMPT
            .replace("{target_seconds}", str(int(config.TARGET_DURATION_SECONDS)))
            .replace("{transition_menu}", _transition_menu_text()))


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_json(raw: str) -> list:
    raw = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    return json.loads(raw)


def _normalize_transition(beat: dict, index: int) -> None:
    t = beat.get("transition") or {}
    ttype = t.get("type", "fade")
    if ttype not in VALID_TRANSITIONS:
        print(f"[planner] warning: beat {index} transition {ttype!r} not in menu — using 'fade'.")
        ttype = "fade"
    try:
        tdur = float(t.get("duration", 0.25))
    except (TypeError, ValueError):
        tdur = 0.25
    beat["transition"] = {"type": ttype, "duration": round(tdur, 3)}


def _normalize_diagram(beat: dict, index: int) -> dict:
    """Validates/coerces a diagram spec; returns a cleaned copy. Raises only
    when the spec is unusable AND no fallback exists."""
    raw = beat.get("diagram") or {}
    cleaned: dict = {
        "style": raw.get("style") if raw.get("style") in VALID_DIAGRAM_STYLES else "dark",
        "nodes": [], "arrows": [], "callouts": [],
    }
    seen_ids = set()
    for i, n in enumerate(raw.get("nodes", []) or []):
        if not isinstance(n, dict) or not str(n.get("label", "")).strip():
            print(f"[planner] beat {index}: dropping invalid node {i}")
            continue
        nid = str(n.get("id") or f"n{len(cleaned['nodes'])}")
        if nid in seen_ids:
            nid = f"{nid}_{i}"
        seen_ids.add(nid)
        cleaned["nodes"].append({
            "id": nid,
            "label": str(n["label"]).strip().upper()[:24],
            "x": min(max(float(n.get("x", 0.5)), 0.02), 0.98),
            "y": min(max(float(n.get("y", 0.4)), 0.02), 0.98),
            "w": min(max(float(n.get("w", 0.26)), 0.08), 0.6),
            "h": min(max(float(n.get("h", 0.2)), 0.06), 0.5),
            "shape": n.get("shape") if n.get("shape") in VALID_NODE_SHAPES else "rect",
            "emph": bool(n.get("emph")),
        })
    for a in raw.get("arrows", []) or []:
        if not isinstance(a, dict):
            continue
        src, dst = str(a.get("from", "")), str(a.get("to", ""))
        if src not in seen_ids or dst not in seen_ids or src == dst:
            print(f"[planner] beat {index}: dropping arrow {src!r}->{dst!r} (unknown/self node)")
            continue
        cleaned["arrows"].append({
            "from": src, "to": dst,
            "label": (str(a["label"]).strip().upper()[:16] if a.get("label") else None),
            "path": a.get("path") if a.get("path") in VALID_ARROW_PATHS else "straight",
        })
    for c in raw.get("callouts", []) or []:
        if isinstance(c, dict) and str(c.get("text", "")).strip():
            cleaned["callouts"].append({
                "text": str(c["text"]).strip().upper()[:28],
                "x": min(max(float(c.get("x", 0.5)), 0.05), 0.95),
                "y": min(max(float(c.get("y", 0.85)), 0.05), 0.95),
                "big": bool(c.get("big")), "emph": bool(c.get("emph")),
            })
    if not cleaned["nodes"] and not cleaned["callouts"]:
        raise ValueError(f"Beat {index}: diagram spec has no usable nodes/callouts.")
    return cleaned


def _validate_and_fix(beat: dict, index: int) -> dict:
    kind = beat.get("visual_kind", "image")
    if kind not in VALID_VISUAL_KINDS:
        print(f"[planner] warning: beat {index} has invalid visual_kind {kind!r} — defaulting to 'image'.")
        kind = beat["visual_kind"] = "image"

    if kind == "diagram":
        try:
            beat["diagram"] = _normalize_diagram(beat, index)
        except ValueError as e:
            print(f"[planner] warning: {e} — downgrading beat {index} to an image beat.")
            kind = beat["visual_kind"] = "image"
            beat.pop("diagram", None)

    if kind == "diagram":
        beat["on_screen_text"] = None
    elif not beat.get("image_query"):
        # Every non-diagram beat needs an image fallback (video beats too).
        if kind == "video_clip":
            print(f"[planner] warning: video beat {index} has no image_query fallback — using video_query.")
            beat["image_query"] = beat.get("video_query") or "software demo screen"
        else:
            raise ValueError(f"Beat {index} is missing image_query — cannot proceed without a search term.")

    role = beat.get("role", "body")
    beat["role"] = role if role in VALID_ROLES else "body"
    # Editorial guardrails: first beat = hook, last = payoff.
    # (fixed after ordering in plan())
    beat["caption_mode"] = beat.get("caption_mode") if beat.get("caption_mode") in VALID_CAPTION_MODES else "on"

    if not beat.get("narration_snippet", "").strip():
        raise ValueError(f"Beat {index} has an empty narration_snippet.")
    if beat.get("text_style") not in VALID_TEXT_STYLES:
        print(f"[planner] warning: beat {index} has invalid text_style {beat.get('text_style')!r} — defaulting to 'headline'.")
        beat["text_style"] = "headline"
    if beat.get("motion") not in VALID_MOTIONS:
        print(f"[planner] warning: beat {index} has invalid motion {beat.get('motion')!r} — defaulting to 'zoom_in'.")
        beat["motion"] = "zoom_in"
    _normalize_transition(beat, index)
    return beat


def assign_transitions(beats: list[dict]) -> list[dict]:
    """Fallback transition choreography for beats.json written by hand or by
    older planner versions (no transition field). The AI director's own
    transitions are respected when present."""
    _TRANSITION_ROTATION = [
        ("fade", 0.25), ("smoothleft", 0.30), ("fadeblack", 0.32),
        ("cut", 0.07), ("circleopen", 0.34), ("dissolve", 0.35), ("hblur", 0.28),
    ]
    n = len(beats)
    rot_idx = 0
    for i in range(n - 1):
        if isinstance(beats[i].get("transition"), dict) and beats[i]["transition"].get("type") in VALID_TRANSITIONS:
            rot_idx += 1
            continue
        beat_dur = beats[i]["end"] - beats[i]["start"]
        if beat_dur <= 2.2:
            beats[i]["transition"] = {"type": "cut", "duration": 0.07}
        else:
            ttype, tdur = _TRANSITION_ROTATION[rot_idx % len(_TRANSITION_ROTATION)]
            rot_idx += 1
            beats[i]["transition"] = {"type": ttype, "duration": float(tdur)}
    return beats


def _build_beats(raw_beats: list[dict], script: str = "") -> list[dict]:
    """Validation + timing + structure — everything after the LLM call."""
    raw_beats = [_validate_and_fix(b, i) for i, b in enumerate(raw_beats)]

    # Structural roles: force first=hook, last=payoff (director intent wins
    # for everything in between).
    raw_beats[0]["role"] = "hook"
    raw_beats[-1]["role"] = "payoff"
    if len(raw_beats) > 1 and raw_beats[0].get("on_screen_text") is None and raw_beats[0]["visual_kind"] != "diagram":
        # A hook without on-screen text is a wasted hook — pull the first
        # few words of its narration as the text.
        words = raw_beats[0]["narration_snippet"].split()
        raw_beats[0]["on_screen_text"] = " ".join(words[:4]).upper()
        raw_beats[0]["text_style"] = "headline"

    # Sanity-check reconstruction: warn (don't fail) on drift, since timing
    # is derived per-beat from word counts regardless of exact text match.
    reconstructed = " ".join(b["narration_snippet"].strip() for b in raw_beats)
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    if norm(reconstructed) != norm(script):
        print("[planner] warning: concatenated narration_snippets don't exactly match the input "
              "script (whitespace/wording drift) — captions will still follow the beats, but "
              "review beats.json before relying on it.")

    beats = []
    cursor = 0.0
    for i, b in enumerate(raw_beats):
        words = _word_count(b["narration_snippet"])
        word_based = max(words / config.AVG_WPM * 60.0, 1.0)
        # The director's pacing hint nudges timing, clamped so captions
        # never drift far from the voice.
        try:
            hint = float(b.get("duration_hint") or 0)
        except (TypeError, ValueError):
            hint = 0
        pace = min(max(hint / word_based, config.PACE_MIN), config.PACE_MAX) if hint > 0 else 1.0
        duration = word_based * pace
        beat = {
            "id": i,
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "narration_snippet": b["narration_snippet"].strip(),
            "role": b["role"],
            "visual_kind": b.get("visual_kind", "image"),
            "image_query": (b.get("image_query").strip() if b.get("image_query") else None),
            "video_query": (b.get("video_query").strip() if b.get("video_query") else None),
            "on_screen_text": (b.get("on_screen_text") or None),
            "text_style": b["text_style"],
            "motion": b["motion"],
            "transition": b["transition"],
            "caption_mode": b["caption_mode"],
            "pace": round(pace, 3),
        }
        if b.get("diagram"):
            beat["diagram"] = b["diagram"]
        beats.append(beat)
        cursor = beat["end"]

    print(f"[planner] plan: {len(beats)} beats, {cursor:.1f}s total "
          f"(target {config.TARGET_DURATION_SECONDS:.0f}s), "
          f"roles: {[(b['id'], b['role'], b['visual_kind']) for b in beats]}")
    return beats


def plan(script: str) -> list[dict]:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set — required for planner.py.")

    import time
    client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)

    # Qwen3.7 Flash burns thousands of tokens on internal reasoning before
    # emitting content — a tight cap shows up as an EMPTY response (all
    # budget spent reasoning), not a truncation. The plan schema is large,
    # so give it plenty of headroom (select_images learned the same lesson).
    max_tokens = 16000
    raw_text = ""
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        response = client.chat.completions.create(
            model=config.PLANNER_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": f"Script:\n\n{script}"},
            ],
        )
        raw_text = (response.choices[0].message.content or "").strip()
        if raw_text:
            break
        # Empty content with no exception = rate-limit/transient provider
        # hiccup on OpenRouter (same pattern select_images retries on).
        if attempt < max_attempts:
            backoff = 2 ** attempt
            print(f"[planner] attempt {attempt}/{max_attempts}: empty response, retrying in {backoff}s...")
            time.sleep(backoff)

    try:
        raw_beats = _extract_json(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"planner.py: model did not return valid JSON: {e}\n\nRaw response:\n{raw_text}")

    if not isinstance(raw_beats, list) or not raw_beats:
        raise RuntimeError(f"planner.py: expected a non-empty JSON array of beats, got: {raw_beats!r}")

    return _build_beats(raw_beats, script)


def plan_from_file(script_path: Path, out_path: Path = config.BEATS_JSON_PATH) -> list[dict]:
    script = Path(script_path).read_text().strip()
    beats = plan(script)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(beats, f, indent=2)
    print(f"[planner] wrote {len(beats)} beats ({beats[-1]['end']:.1f}s total) -> {out_path}")
    return beats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plan the full edit (AI director) from a script.")
    parser.add_argument("--script", required=True)
    parser.add_argument("--out", default=str(config.BEATS_JSON_PATH))
    args = parser.parse_args()

    plan_from_file(Path(args.script), Path(args.out))
