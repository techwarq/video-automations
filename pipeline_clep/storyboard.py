"""
Storyboard: feature.json -> timed shot plan.

Input feature.json (hand-written for MVP, or exported from sdk/clep.js):
{
  "name": "ai-research", "title": "AI Research",
  "url": "https://acme.ai/dashboard",
  "element": {"x":0.2,"y":0.35,"w":0.6,"h":0.3},
  "steps": [
    {"type":"focus","label":"Search input"},
    {"type":"type","text":"AI browser agents"},
    {"type":"click","target":"Research"},
    {"type":"loading","label":"Research running"},
    {"type":"result","label":"Sources appear"}
  ],
  "style": "saas", "duration": 4.2, "aspect": "16:9"
}

Output plan dict:
{
  "duration": 4.2, "fps": 30, "n_frames": 126,
  "phases": {establish:(0,.18), interact:(.18,.42), click:(.42,.55), loading:(.55,.75), result:(.75,1.0)},
  "camera": [keyframes {t, cx, cy, zoom}],
  "cursor": [waypoints {t, x, y}],
  "typing": {"text": ..., "t0": ..., "t1": ...},
  "click_at": float (seconds),
  "ui": {...labels...},
  "element": {...}, "title": ..., "url": ...
}
"""

from __future__ import annotations


def _get(steps, types, default=None):
    for s in steps:
        if s.get("type") in types:
            return s
    return default


def build_plan(feature: dict, duration: float | None = None,
               aspect: str | None = None, style: str | None = None,
               size: str | None = None) -> dict:
    steps = feature.get("steps") or []
    # Accept SDK-native interactions/states when steps absent:
    # click -> input -> submit -> loading -> result ordering is preserved.
    if not steps and feature.get("interactions"):
        order = {"click": "click", "input": "type", "submit": "click",
                 "loading": "loading", "result": "result", "state": "focus", "focus": "focus"}
        for ev in feature["interactions"]:
            t = order.get(ev.get("type", ""), "focus")
            steps.append({"type": t, "text": ev.get("text") or ev.get("label") or "",
                          "label": ev.get("label") or ev.get("state") or "",
                          "target": ev.get("target") or ""})

    dur = float(duration or feature.get("duration") or 4.2)
    dur = min(max(dur, 2.0), 60.0)

    type_step = _get(steps, ("type",)) or {}
    click_step = _get(steps, ("click", "submit")) or {}
    loading_step = _get(steps, ("loading",)) or {}
    result_step = _get(steps, ("result",)) or {}

    query_text = str(type_step.get("text") or "AI browser agents")
    button_label = str(click_step.get("target") or click_step.get("label") or "Start Research")
    if len(button_label) > 24:
        button_label = button_label[:24]
    # click target doubles as button label when it looks like one ("Research", "Generate")
    loading_label = str(loading_step.get("label") or "Working…")
    result_label = str(result_step.get("label") or "Done")

    el = feature.get("element") or {"x": 0.2, "y": 0.35, "w": 0.6, "h": 0.3}
    ecx = min(max(float(el.get("x", 0.2)) + float(el.get("w", 0.6)) / 2, 0.05), 0.95)
    ecy = min(max(float(el.get("y", 0.35)) + float(el.get("h", 0.3)) / 2, 0.05), 0.95)

    # Phase boundaries as fractions of duration (match the 0.0-4.2s board in the brief).
    phases = {
        "establish": (0.0, 0.18),
        "interact": (0.18, 0.42),
        "click": (0.42, 0.55),
        "loading": (0.55, 0.75),
        "result": (0.75, 1.0),
    }

    # Camera keyframes in app-space (0..1). Zoom 1.0 == full app visible.
    # Push toward element center, hold through interaction, pull back a touch on result.
    # Narrow canvases (portrait) already fill width with the card, so they
    # push less — same move, reframed per aspect.
    aspect_name = aspect or feature.get("aspect") or "16:9"
    if size or feature.get("size"):
        try:
            import config as _cfg

            _w, _h = _cfg.canvas(size=size or feature.get("size"))
            aspect_name = _cfg.aspect_class(_w, _h)
        except ValueError:
            pass
    if aspect_name == "9:16":
        push, pull = 1.9, 1.25
    elif aspect_name == "3:2":
        push, pull = 1.6, 1.3
    elif aspect_name == "4:5":
        push, pull = 1.6, 1.2
    elif aspect_name == "1:1":
        push, pull = 1.35, 1.18
    else:
        push, pull = 1.65, 1.32
    camera = [
        {"t": 0.00, "cx": 0.5, "cy": 0.5, "zoom": 1.0},
        {"t": 0.18, "cx": ecx, "cy": ecy, "zoom": push},
        {"t": 0.55, "cx": ecx, "cy": ecy, "zoom": push},
        {"t": 0.80, "cx": 0.5, "cy": 0.5, "zoom": pull},
        {"t": 1.00, "cx": 0.5, "cy": 0.5, "zoom": pull},
    ]

    # Cursor waypoints in app-space. Input sits left-of-center, button right-of-center
    # inside the feature card (renderer places them there — keep in sync).
    input_pos = {"x": 0.38, "y": ecy - 0.02}
    button_pos = {"x": 0.66, "y": ecy - 0.02}
    cursor = [
        {"t": 0.00, "x": 0.88, "y": 0.96},
        {"t": 0.20, "x": input_pos["x"], "y": input_pos["y"]},
        {"t": 0.42, "x": input_pos["x"], "y": input_pos["y"]},
        {"t": 0.50, "x": button_pos["x"], "y": button_pos["y"]},
        {"t": 0.75, "x": button_pos["x"], "y": button_pos["y"]},
        {"t": 0.90, "x": 0.72, "y": 0.60},
        {"t": 1.00, "x": 0.72, "y": 0.60},
    ]

    fps = 30
    return {
        "duration": dur,
        "fps": fps,
        "n_frames": int(round(dur * fps)),
        "phases": phases,
        "camera": camera,
        "cursor": cursor,
        "typing": {"text": query_text, "t0": dur * 0.20, "t1": dur * 0.42},
        "click_at": dur * 0.52,
        "ui": {
            "query_text": query_text,
            "button_label": button_label,
            "loading_label": loading_label,
            "result_label": result_label,
            "input_hint": str(type_step.get("label") or "Search input"),
        },
        "element": {"x": float(el.get("x", 0.2)), "y": float(el.get("y", 0.35)),
                    "w": float(el.get("w", 0.6)), "h": float(el.get("h", 0.3))},
        "element_center": {"x": ecx, "y": ecy},
        "title": str(feature.get("title") or feature.get("name") or "Feature"),
        "name": str(feature.get("name") or "feature"),
        "url": str(feature.get("url") or "app.clep.io"),
        "aspect": aspect or feature.get("aspect") or "16:9",
        "size": size or feature.get("size"),
        "style": style or feature.get("style") or "saas",
        "screenshot": feature.get("screenshot"),
    }


def phase_at(plan: dict, t: float) -> str:
    f = t / plan["duration"]
    for name, (a, b) in plan["phases"].items():
        if a <= f < b or (name == "result" and f >= b - 1e-9):
            return name
    return "result"
