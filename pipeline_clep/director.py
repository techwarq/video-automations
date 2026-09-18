"""
Director: natural-language command -> controlled ClipSpec.

Covers the three clip kinds clep makes:
  tour     walkthrough / portfolio overview / feature showcase (scroll tour,
           no data-clep required — works on any URL)
  feature  single instrumented feature demo (type -> click -> result,
           requires [data-clep="name"])
  mockup   synthetic UI mock rendered from feature.json (no browser)

Example:
  director.parse('/clep make video showing the portfolio at http://x
                  highlighting hero, work and contact, 10s, calm')

The director also *grounds* the request: scan_content() reads the live
page (title, headings, sections, data-clep features) and maps what the
user asked for onto what the page actually has, so the plan never
hallucinates sections. plan() prints that understanding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("auto", "tour", "feature", "mockup", "launch")
MOVEMENTS = ("calm", "standard", "dynamic")
ASPECTS = ("16:9", "1:1", "4:5", "9:16")
STYLES = ("minimal", "saas", "cinematic", "apple")

_URL_RE = re.compile(r"(?:https?|file)://[^\s,'\"\)\]]+")
_BG_RE = re.compile(
    r"""\b(?:bg|background)\s+(
        blush|minimal|saas|cinematic|apple
        |solid:\#[0-9a-fA-F]{3,8}
        |\#[0-9a-fA-F]{3,8}(?:\s*,\s*\#[0-9a-fA-F]{3,8}){0,2}
    )""", re.I | re.X,
)
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s(?:ec(?:ond)?s?)?)\b", re.I)
_QUOTED_RE = re.compile(r"""["'“”‘’]([^"'“”‘’]{2,60})["'“”‘’]""")
_FEATURE_NAME_RE = re.compile(
    r"""(?:feature|demo|clip)\s+(?:of|for|called|named)\s+(?:the\s+)?([a-z0-9][a-z0-9\-_]{1,40})""",
    re.I,
)
_NAME_RE = re.compile(
    r"""(?:section|clip|called|named)\s+["']?([a-z0-9][a-z0-9\-_]{1,40})["']?""",
    re.I,
)

_TOUR_WORDS = (
    "tour", "walkthrough", "walk-through", "walk through", "showcase",
    "portfolio", "overview", "browse", "scroll", "show me", "show the",
    "showing", "showcasing", "landing", "homepage", "site", "website",
    "around", "sections",
)
_FEATURE_WORDS = (
    "feature", "demo", "click", "type", "convert", "upload", "generate",
    "submit", "button", "input", "use the", "try the", "interact",
)
_MOCKUP_WORDS = ("mockup", "mock-up", "mock up", "concept", "render", "synthetic", "static")
_LAUNCH_WORDS = ("launch", "announc", "introduc", "meet the", "teaser", "trailer")


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(w in t for w in words)


@dataclass
class ClipSpec:
    kind: str = "tour"          # tour | feature | mockup
    url: str | None = None
    name: str | None = None     # data-clep target (feature kind)
    feature_file: str | None = None  # mockup kind
    sections: list[str] = field(default_factory=list)  # tour stops, in order
    query: str | None = None
    style: str | None = None
    aspect: str | None = None
    size: str | None = None     # exact WxH canvas, e.g. "1120x640" (landing cards)
    bg: str | None = None       # backdrop override: preset, #a,#b[,#c], solid:#hex
    duration: float | None = None
    movement: str | None = None  # calm | standard | dynamic
    fps: int | None = None
    quality: str | None = None
    captions: bool = True
    raw: str = ""

    def summary(self) -> str:
        bits = [f"kind={self.kind}"]
        if self.url:
            bits.append(f"url={self.url}")
        if self.name:
            bits.append(f"feature={self.name}")
        if self.sections:
            bits.append("sections=" + ", ".join(self.sections))
        if self.duration:
            bits.append(f"{self.duration:g}s")
        if self.size:
            bits.append(self.size)
        if self.bg:
            bits.append(f"bg={self.bg}")
        if self.style:
            bits.append(self.style)
        if self.aspect:
            bits.append(self.aspect)
        if self.movement:
            bits.append(f"movement={self.movement}")
        return " ".join(bits)


def parse(prompt: str, url: str | None = None, name: str | None = None) -> ClipSpec:
    """Parse freeform text into a ClipSpec. Never raises on weird input."""
    text = (prompt or "").strip()
    # Strip a leading slash-command ("/clep ...").
    text = re.sub(r"^/clep\b\s*", "", text, flags=re.I).strip()
    low = text.lower()
    spec = ClipSpec(raw=text)

    m = _URL_RE.search(text)
    if m:
        spec.url = m.group(0).rstrip(".,;!")
    elif url:
        spec.url = url
    if name:
        spec.name = name

    # ── kind ──────────────────────────────────────────────────────────
    tour_hit = _has_any(low, _TOUR_WORDS)
    feat_hit = _has_any(low, _FEATURE_WORDS)
    mock_hit = _has_any(low, _MOCKUP_WORDS)
    launch_hit = _has_any(low, _LAUNCH_WORDS)
    # "demo of the X feature" / "feature X" names a real target -> feature.
    explicit_feature = bool(_FEATURE_NAME_RE.search(text))
    text_no_url = _URL_RE.sub(" ", text)
    m = _BG_RE.search(text_no_url)
    if m:
        spec.bg = re.sub(r"\s+", "", m.group(1))
    # The bg clause is production talk, not a tour stop — strip it before
    # section/name extraction (like URLs).
    text_no_url = _BG_RE.sub(" ", text_no_url)
    low_no_url = text_no_url.lower()
    if mock_hit and not spec.url:
        spec.kind = "mockup"
    elif launch_hit and not mock_hit:
        # "feature/product launch video" is a whole-product story, not one
        # widget demo — it beats the feature match on the word "feature".
        spec.kind = "launch"
    elif explicit_feature:
        spec.kind = "feature"
    elif tour_hit and not feat_hit:
        spec.kind = "tour"
    elif feat_hit and not tour_hit:
        spec.kind = "feature"
    elif tour_hit and feat_hit:
        # "walkthrough of the checkout feature" -> feature-led tour.
        # Default to tour (works without instrumentation); the planner
        # narrows to feature when a data-clep name resolves.
        spec.kind = "tour"
    else:
        if spec.name:
            spec.kind = "feature"
        elif spec.url:
            spec.kind = "tour"
        else:
            spec.kind = "mockup" if re.search(r"\.feature\.json", text) else "tour"

    m = re.search(r"([\w\-./]+\.feature\.json)", text)
    if m:
        spec.feature_file = m.group(1)
        if spec.kind == "tour" and not spec.url:
            spec.kind = "mockup"

    if not spec.name:
        m = _FEATURE_NAME_RE.search(text_no_url) or _NAME_RE.search(text_no_url)
        if m:
            cand = m.group(1).strip("-_")
            # Durations ("clip 4s") and bare numbers are never feature names.
            if cand.lower() not in ("the", "showing", "a", "my", "our", "this") \
                    and re.search(r"[a-z]", cand.lower()):
                spec.name = cand

    # ── sections ("showing/highlighting/featuring X, Y and Z") ────────
    # URLs are stripped first so "tour of http://x showing a, b" never
    # yields URL fragments as stops.
    spec.sections = _extract_sections(text_no_url)

    # ── query text to type (feature kind) ─────────────────────────────
    for q in _QUOTED_RE.findall(text):
        if spec.url and q in spec.url:
            continue
        if len(q.split()) >= 2 and not spec.query:
            spec.query = q[:80]
            break

    # ── style / aspect / quality / movement / duration ────────────────
    for s in STYLES:
        if re.search(rf"\b{s}\b", low):
            spec.style = s
            break
    if re.search(r"\b(vertical|portrait|9:16|reel|short|tiktok)\b", low):
        spec.aspect = "9:16"
    elif re.search(r"\bsquare\b|\b1:1\b", low):
        spec.aspect = "1:1"
    elif re.search(r"\b4:5\b", low):
        spec.aspect = "4:5"
    elif re.search(r"\b(widescreen|landscape|youtube|16:9)\b", low):
        spec.aspect = "16:9"
    if re.search(r"\b720p\b", low):
        spec.quality = "720p"
    elif re.search(r"\b1080p\b", low):
        spec.quality = "1080p"
    m = re.search(r"\b(\d{3,4})\s*x\s*(\d{3,4})\b", text)
    if m:
        try:
            w, h = int(m.group(1)), int(m.group(2))
            if 160 <= w <= 4096 and 160 <= h <= 4096:
                spec.size = f"{w // 2 * 2}x{h // 2 * 2}"
        except ValueError:
            pass
    elif re.search(r"\b(landing[\s-]?card|feature[\s-]?card|video[\s-]?card|"
                     r"card[\s-]?clip|card[\s-]?size|card[\s-]?loop)\b", low):
        spec.size = "1120x640"  # sensible card default; override with --size WxH
    if re.search(r"\b(30\s?fps|30fps)\b", low):
        spec.fps = 30
    elif re.search(r"\b(60\s?fps|60fps)\b", low):
        spec.fps = 60
    if re.search(r"\b(calm|still|gentle|stable|smooth|controlled|subtle)\b", low):
        spec.movement = "calm"
    elif re.search(r"\b(dynamic|dramatic|punchy|fast|snappy)\b", low):
        spec.movement = "dynamic"
    m = _DUR_RE.search(low)
    if m:
        try:
            spec.duration = min(max(float(m.group(1)), 2.0), 60.0)
        except ValueError:
            pass
    if re.search(r"\bno\s+(captions?|titles?|labels?)\b", low):
        spec.captions = False
    elif re.search(r"\b(captions?|titles?|labels?|narrat\w*)\b", low):
        spec.captions = True

    # Sensible defaults per kind (caller flags still override).
    if spec.movement is None:
        spec.movement = "calm" if spec.kind in ("tour", "launch") else "standard"
    return spec


def _extract_sections(text: str) -> list[str]:
    # Prefer an explicit stop list ("highlighting X, Y") over the generic
    # "showing the portfolio ..." opener, which would otherwise swallow the
    # whole sentence as one stop.
    chunk = None
    m = re.search(
        r"(?:highlight(?:ing)?|featur(?:ing|e)|includ(?:ing|e)|cover(?:ing)?)\s+(.+?)(?:\.|$)",
        text, flags=re.I,
    )
    if m:
        chunk = m.group(1)
    else:
        m = re.search(r"(?:show(?:ing|case|casing)?|with|over)\s+(.+?)(?:\.|$)", text, flags=re.I)
        if not m:
            return []
        chunk = m.group(1)
    if re.search(r"\bquery\b", chunk, flags=re.I):
        return []
    chunk = _QUOTED_RE.sub(" ", chunk)
    # Cut trailing production talk ("in 10s, calm, ...", bare "10s", styles).
    chunk = re.split(
        r"\b(in\s+\d[\d.\s]*s(?:ec)?s?|\d[\d.\s]*s(?:ec(?:ond)?s?)?|"
        r"calm|standard|dynamic|minimal|saas|cinematic|apple|"
        r"vertical|portrait|square|widescreen|with captions?|no captions?)\b",
        chunk, flags=re.I,
    )[0]
    parts = re.split(r"\s*(?:,|;|\band\b|\bthen\b|\+|>|/|&)\s*", chunk)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = re.sub(r"^(the|my|our|your|a|an)\s+", "", p.strip(), flags=re.I).strip(" .\"'“”")
        p = re.sub(r"\s+(at|of|for|with|to|in|on|and|or)$", "", p, flags=re.I).strip()
        p = re.sub(r"\s+(card\s*(clip|video|loop|size)?|clip|loop)$", "", p, flags=re.I).strip()
        if not (1 < len(p) <= 40):
            continue
        if len(p.split()) > 4:  # sentences aren't stops ("take in what user wants")
            continue
        if re.search(r"https?://", p):
            continue
        if re.fullmatch(r"\d+(\.\d+)?\s*s(?:ec(?:ond)?s?)?", p, flags=re.I):
            continue
        key = p.lower()
        if key in seen or key in ("portfolio", "site", "website", "page", "everything", "it",
                                  "video", "clip", "loop", "tour", "feature", "features",
                                  "launch", "launch video", "product", "product video", "app",
                                  "at", "http", "https"):
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= 6:
            break
    return out


def ground_sections(requested: list[str], page_sections: list[dict],
                    limit: int = 6) -> list[dict]:
    """Map requested section words onto real page headings (fuzzy substring).

    Returns ordered stops: {heading, anchor, matched}. Unmatched requests
    are kept as caption-only stops so nothing the user asked for is dropped.
    Unknown pages (no headings found) fall back to the raw requests.
    """
    if not requested:
        return [{"heading": s.get("heading", ""), "anchor": s.get("anchor"),
                 "matched": True} for s in (page_sections or [])[:limit]]
    stops: list[dict] = []
    pool = list(page_sections or [])
    for req in requested:
        needle = req.lower()
        best = None
        for cand in pool:
            hay = f"{cand.get('heading','')} {cand.get('anchor','')} {cand.get('id','')}".lower()
            if needle in hay or any(w in hay for w in needle.split() if len(w) > 3):
                best = cand
                break
        if best is not None:
            pool.remove(best)
            stops.append({"heading": best.get("heading") or req, "anchor": best.get("anchor"),
                          "matched": True, "asked": req})
        else:
            stops.append({"heading": req, "anchor": None, "matched": False, "asked": req})
    return stops


def build_launch_stops(requested: list[str], page_sections: list[dict],
                       limit: int = 8) -> list[dict]:
    """End-to-end product map for a launch video: hero -> everything.

    Named requests go first (grounded onto real headings); then every
    remaining page section in top-to-bottom order, so the story covers the
    whole product and ends near the CTA — nothing is shot before this map
    exists. Caps at `limit` stops to keep the clip watchable.
    """
    asked = ground_sections(requested, page_sections or [], limit=limit)
    have = {str(s.get("heading", "")).lower() for s in asked}
    stops = list(asked)
    for s in page_sections or []:
        if len(stops) >= limit:
            break
        if str(s.get("heading", "")).lower() in have:
            continue
        stops.append({"heading": s.get("heading", ""), "anchor": s.get("anchor"),
                      "matched": True})
    return stops


def plan(spec: ClipSpec, page: dict | None = None) -> dict:
    """Human-readable shoot plan: what we understood + what we will do."""
    page = page or {}
    lines = [f"understood: {spec.summary() or 'empty prompt'}"]
    if page.get("title"):
        lines.append(f"page: {page['title']!r} ({page.get('n_sections', 0)} sections, "
                     f"{page.get('n_features', 0)} instrumented features)")
    feats = [f.get("name") for f in (page.get("features") or [])][:8]
    if feats:
        lines.append("features on page: " + ", ".join(feats))
    heads = [s.get("heading") for s in (page.get("sections") or []) if s.get("heading")][:8]
    if heads:
        lines.append("sections on page: " + " · ".join(h[:42] for h in heads))
    if spec.kind == "launch":
        stops = build_launch_stops(spec.sections, page.get("sections") or [])
        lines.append(f"launch map ({len(stops)} stops, end to end):")
        for i, s in enumerate(stops, 1):
            tag = "✓" if s.get("matched") else "○ caption"
            lines.append(f"  {i}. [{tag}] {s['heading'][:60]}")
        if not stops:
            lines.append("  (no sections found — full-page top → bottom sweep)")
        lines.append(f"camera: hold-wide, {spec.movement} movement, "
                     + ("captions on" if spec.captions else "captions off"))
    elif spec.kind == "tour":
        stops = ground_sections(spec.sections, page.get("sections") or [])
        if stops:
            lines.append("tour stops:")
            for i, s in enumerate(stops, 1):
                tag = "✓" if s.get("matched") else "○ caption"
                lines.append(f"  {i}. [{tag}] {s['heading'][:60]}")
        else:
            lines.append("tour stops: full-page top → bottom sweep")
        lines.append(f"camera: hold-wide, {spec.movement} movement, "
                     + ("captions on" if spec.captions else "captions off"))
    elif spec.kind == "feature":
        lines.append(f"will drive: [data-clep=\"{spec.name or '?'}\"] "
                     f"(type → click → settle), then hold on result")
    else:
        lines.append(f"will render UI mockup from {spec.feature_file or 'feature.json'}")
    return {"spec": spec, "text": "\n".join(lines)}
