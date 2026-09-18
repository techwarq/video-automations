"""
Agent: automated Screen-Studio cameraman for data-clep features.

Two jobs:
  discover(url) -> registry of instrumented features (the "Feature Registry"):
      name, title, url, viewport, element bbox, component path,
      inputs/buttons found in the subtree, SDK interactions/states.

  record(url, name, out_dir, query=None) -> raw screen recording + trace.json:
      drives the REAL feature in Chromium (video recording on), performs
      focus -> type -> click -> loading -> result, logs every cursor move,
      click, keystroke window and bbox in trace.json for the polish pass.

No SDK required: falls back to plain `[data-clep="name"]` DOM lookup.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWPORT = {"width": 1440, "height": 900}


def _launch(p, out_dir: Path, record: bool, chromium_args: list | None = None):
    # chromium_args is test-only (e.g. ["--disable-web-security"] when the
    # backend's CORS doesn't allow localhost). Never use in production.
    browser = p.chromium.launch(args=chromium_args or [])
    kwargs: dict = {"viewport": VIEWPORT, "accept_downloads": True}
    if record:
        kwargs["record_video_dir"] = str(out_dir / "raw")
        kwargs["record_video_size"] = VIEWPORT
    ctx = browser.new_context(**kwargs)
    return browser, ctx


def _sdk_export(page, name: str | None = None):
    """Pull SDK registry when instrumented; else None."""
    try:
        if name:
            return page.evaluate("(n) => (window.__CLEP__ && window.__CLEP__.export(n)) || null", name)
        return page.evaluate("() => (window.__CLEP__ && window.__CLEP__.exportAll()) || null")
    except Exception:
        return None


def _node_info(page, name: str) -> dict:
    """Fallback DOM inspection for one [data-clep] subtree (no SDK needed)."""
    js = """(name) => {
      const root = document.querySelector(`[data-clep="${name}"]`);
      if (!root) return null;
      const r = root.getBoundingClientRect();
      const q = (sel) => {
        const el = root.querySelector(sel);
        if (!el) return null;
        const b = el.getBoundingClientRect();
        const val = (el.value !== undefined && el.value !== null) ? String(el.value) : "";
        return {x: b.x, y: b.y, w: b.width, h: b.height,
                text: (val || el.innerText || el.placeholder || el.name || '').trim().slice(0, 40)};
      };
      const qInput = () => {
        const cands = [...root.querySelectorAll('input, textarea, [contenteditable]')];
        const vis = cands.find(e => {
          const t = (e.getAttribute('type') || 'text').toLowerCase();
          if (t === 'file' || t === 'hidden') return false;
          const b = e.getBoundingClientRect();
          return b.width > 4 && b.height > 4;
        });
        return vis || null;
      };
      const qBtn = (sel) => {
        const el = root.querySelector(sel);
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return {x: b.x, y: b.y, w: b.width, h: b.height,
                text: (el.innerText || '').trim().slice(0, 40)};
      };
      const qWrap = (el) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        const val = (el.value !== undefined && el.value !== null) ? String(el.value) : "";
        return {x: b.x, y: b.y, w: b.width, h: b.height,
                text: (val || el.innerText || el.placeholder || el.name || '').trim().slice(0, 40)};
      };
      const path = [];
      let cur = root;
      for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
        path.unshift(cur.tagName.toLowerCase()
          + (cur.getAttribute('data-clep-state') ? '[state=' + cur.getAttribute('data-clep-state') + ']' : ''));
        cur = cur.parentElement;
      }
      return {
        box: {x: r.x, y: r.y, w: r.width, h: r.height},
        input: qWrap(qInput()),
        button: qBtn('[data-clep-action="primary"], button, input[type=submit], [type=button]'),
        component: path,
        state: root.getAttribute('data-clep-state'),
      };
    }"""
    return page.evaluate(js, name)


def discover(url: str, headless: bool = True) -> dict:
    """List every data-clep feature on the page (SDK export preferred)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(800)

        sdk_all = _sdk_export(page)
        names: list[str] = []
        if sdk_all:
            names = sorted(sdk_all.keys())
        else:
            try:
                names = page.evaluate(
                    "() => [...new Set([...document.querySelectorAll('[data-clep]')]"
                    ".map(e => e.getAttribute('data-clep')))]")
            except Exception:
                names = []
        vw, vh = VIEWPORT["width"], VIEWPORT["height"]
        features = []
        for name in names:
            sdk = (sdk_all.get(name) or {}) if sdk_all else {}
            info = _node_info(page, name) or {}
            box = (info.get("box") or {})
            el = sdk.get("element") or {
                "x": round(max(box.get("x", 0), 0) / vw, 4),
                "y": round(max(box.get("y", 0), 0) / vh, 4),
                "w": round(box.get("w", 0) / vw, 4),
                "h": round(box.get("h", 0) / vh, 4),
            }
            features.append({
                "name": name,
                "title": sdk.get("title") or name.replace("-", " ").title(),
                "url": url, "viewport": {"w": vw, "h": vh},
                "element": el,
                "component": sdk.get("component") or info.get("component") or [],
                "has_input": bool(info.get("input")),
                "has_button": bool(info.get("button")),
                "input_hint": (info.get("input") or {}).get("text", ""),
                "button_label": (info.get("button") or {}).get("text", ""),
                "interactions": sdk.get("interactions") or [],
                "states": sdk.get("states") or [],
            })
        browser.close()
    return {"url": url, "count": len(features), "features": features}


def scan_content(url: str, headless: bool = True) -> dict:
    """Understand the page: title + headings/sections + data-clep features.

    This is what the director grounds a natural-language request against,
    so "show the portfolio's work section" maps to a real anchor instead
    of a guess. Never requires instrumentation — tours work on any URL.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(900)
        try:
            data = page.evaluate("""() => {
              const title = (document.title || '').slice(0, 120);
              const desc = ((document.querySelector('meta[name=description]') || {})
                .content || '').slice(0, 200);
              const h1 = [...document.querySelectorAll('h1')]
                .map(e => e.innerText.trim()).filter(Boolean).slice(0, 3);
              const seen = new Set();
              const sections = [];
              const push = (heading, el) => {
                heading = (heading || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
                if (!heading || seen.has(heading.toLowerCase())) return;
                seen.add(heading.toLowerCase());
                let anchor = null;
                const host = el.closest ? (el.closest('section[id], div[id], article[id]') || el) : el;
                if (host && host.id) anchor = '#' + host.id;
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                sections.push({heading, anchor,
                               y: r ? Math.round(r.top + window.scrollY) : 0});
              };
              [...document.querySelectorAll('section, article')]
                .slice(0, 24).forEach(sec => {
                  const h = sec.querySelector('h1, h2, h3');
                  push(h ? h.innerText : (sec.getAttribute('aria-label') || ''), h || sec);
                });
              if (!sections.length) {
                [...document.querySelectorAll('h1, h2')].slice(0, 12)
                  .forEach(h => push(h.innerText, h));
              }
              sections.sort((a, b) => a.y - b.y);
              const names = [...new Set([...document.querySelectorAll('[data-clep]')]
                .map(e => e.getAttribute('data-clep')))].slice(0, 20);
              return {title, desc, h1, sections, names,
                      height: document.body ? document.body.scrollHeight : 0};
            }""")
        except Exception:
            data = {}
        browser.close()
    data = data or {}
    return {
        "url": url,
        "title": data.get("title", ""),
        "description": data.get("desc", ""),
        "h1": data.get("h1", []),
        "sections": data.get("sections", []),
        "n_sections": len(data.get("sections", [])),
        "features": [{"name": n} for n in (data.get("names") or [])],
        "n_features": len(data.get("names") or []),
        "page_height": data.get("height", 0),
    }


def record_tour(url: str, out_dir: Path, sections: list | None = None,
                per_section: float = 2.4, max_sections: int = 6,
                headless: bool = True,
                chromium_args: list | None = None) -> dict:
    """Record a controlled scroll walkthrough (portfolio / showcase tour).

    No data-clep needed: opens the page, settles past the loader, then
    eases through each section (scroll_into_view center + hold) while the
    cursor rests out of the way. `sections` is an optional list of
    {heading, anchor} stops (already grounded by director.ground_sections);
    omitted -> evenly spaced sweep of the full page height.

    Returns a trace.json-compatible dict with kind="clep-tour" plus
    `captions`: [{t0, t1, text}] for the polish pass.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    def now() -> float:
        return round(time.monotonic() - t0, 3)

    with sync_playwright() as p:
        browser, ctx = _launch(p, out_dir, record=True, chromium_args=chromium_args)
        page = ctx.new_page()
        t0 = time.monotonic()
        cursor = [{"t": 0.0, "x": 0.88, "y": 0.94}]

        page.goto(url, wait_until="networkidle")
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        # Hide native cursor — polish draws its own.
        try:
            page.add_style_tag(content="* { cursor: none !important; }")
        except Exception:
            pass
        page.wait_for_timeout(1400)  # past loaders/hero animation
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass
        page.wait_for_timeout(600)
        establish_end = now()

        try:
            info = page.evaluate("""() => ({
              h: (document.body ? document.body.scrollHeight : 900),
              vh: window.innerHeight || 900,
              title: (document.title || '')
            })""")
        except Exception:
            info = {}
        page_h = max(float(info.get("h") or 900), 1.0)

        # Resolve stops to fractional scroll positions (top-center of each).
        stops: list[dict] = []
        if sections:
            for s in sections[:max_sections]:
                frac = None
                anchor = (s.get("anchor") or "") if isinstance(s, dict) else ""
                if anchor:
                    try:
                        y = page.evaluate(
                            """(a) => {
                              const el = document.querySelector(a);
                              if (!el) return null;
                              const r = el.getBoundingClientRect();
                              return r.top + window.scrollY;
                            }""", anchor)
                        if y is not None:
                            frac = max(min(float(y) / page_h, 0.98), 0.0)
                    except Exception:
                        frac = None
                if frac is None and isinstance(s, dict) and s.get("heading"):
                    try:
                        y = page.evaluate(
                            """(txt) => {
                              const els = [...document.querySelectorAll('h1,h2,h3,section,article')];
                              const t = txt.toLowerCase();
                              const hit = els.find(e => (e.innerText || '').toLowerCase().includes(t.slice(0, 24)));
                              if (!hit) return null;
                              const r = hit.getBoundingClientRect();
                              return r.top + window.scrollY;
                            }""", str(s["heading"])[:60])
                        if y is not None:
                            frac = max(min(float(y) / page_h, 0.98), 0.0)
                    except Exception:
                        frac = None
                heading = (s.get("heading") if isinstance(s, dict) else str(s)) or "Highlights"
                stops.append({"heading": str(heading)[:70], "frac": frac})
            # Fill unresolved as even sweep, keep page order for resolved.
            n = len(stops)
            for i, s in enumerate(stops):
                if s["frac"] is None:
                    s["frac"] = min(0.02 + 0.96 * (i + 1) / (n + 1), 0.98)
        else:
            # Even sweep: hero -> mid sections -> footer-adjacent (never the
            # footer itself — holds on content, not whitespace).
            n = 4
            stops = [{"heading": "", "frac": 0.02 + 0.90 * (i + 1) / (n + 1)} for i in range(n)]

        events: list[dict] = [{"t": establish_end, "kind": "establish",
                                "x": 0.5, "y": 0.5, "label": "top"}]
        captions: list[dict] = []
        hold = max(per_section, 1.2)
        for i, s in enumerate(stops):
            frac = float(s["frac"])
            # Ease: jump-cut-free smooth scroll, then a readable hold.
            t_scroll = now()
            try:
                page.evaluate(
                    """(f) => window.scrollTo({top: f * (document.body.scrollHeight - window.innerHeight),
                                               behavior: 'smooth'})""", frac)
            except Exception:
                pass
            page.wait_for_timeout(950)  # smooth-scroll glide
            t_hold0 = now()
            try:
                page.wait_for_timeout(int(hold * 1000))
            except Exception:
                pass
            t_hold1 = now()
            label = s["heading"] or f"Section {i + 1}"
            events.append({"t": t_hold0, "kind": "section", "step": i,
                           "x": 0.5, "y": 0.5, "label": label[:60]})
            if label:
                captions.append({"t0": t_hold0, "t1": t_hold1, "text": label[:70]})

        # Gentle return toward top for a clean closing frame (no whip-pan:
        # quick smooth glide, short hold).
        try:
            page.evaluate("() => window.scrollTo({top: 0, behavior: 'smooth'})")
        except Exception:
            pass
        page.wait_for_timeout(900)
        total = now()

        video = page.video
        vpath = None
        ctx.close()
        if video:
            vpath = Path(str(video.path()))
        browser.close()

    trace = {
        "kind": "clep-tour",
        "name": "tour",
        "title": (info.get("title") or url or "Tour")[:80],
        "url": url,
        "viewport": {"w": VIEWPORT["width"], "h": VIEWPORT["height"]},
        "video": str(vpath) if vpath else None,
        "duration": total,
        "trim_start": max(0.0, round(establish_end - 0.9, 3)),
        "element": {"x": 0.06, "y": 0.06, "w": 0.88, "h": 0.88},
        "cursor": cursor,
        "typing": {"text": "", "t0": 0.0, "t1": 0.0},
        "click": None,
        "clicks": [],
        "focuses": [],
        "events": events,
        "captions": captions,
        "states_seen": [],
        "steps": [{"action": "tour", "target": s["heading"][:40]} for s in stops],
        "button_label": "",
        "query": "",
        "result_at": total,
        "has_input": False,
        "has_button": False,
    }
    tpath = out_dir / "trace.json"
    tpath.write_text(json.dumps(trace, indent=2))
    print(f"[agent] tour -> {vpath} ({total:.1f}s, {len(stops)} stops, trim {trace['trim_start']}s)")
    print(f"[agent] trace -> {tpath}")
    return trace


def record(url: str, name: str, out_dir: Path, query: str | None = None,
           headless: bool = True, type_cps: int = 14,
           steps: list | None = None,
           chromium_args: list | None = None) -> dict:
    """Drive the feature on a live page; save raw video + trace.json.

    steps: explicit multi-step plan (chained clicks across states), e.g.
      [{"action":"type","target":"input","value":"AI agents"},
       {"action":"click","target":"primary"},
       {"action":"wait","for":"change","timeout":4},
       {"action":"upload","target":"file","file":"demo/sample.pdf"},
       {"action":"click","target":"action:export"},
       {"action":"wait","for":"state:exported","timeout":4}]
    Actions: type | click | upload | wait | goto. Targets resolve inside the
    feature scope: "input" (visible) | "primary" | "action:<name>" |
    "text:<label>" | "file" (upload only) | any CSS selector. Without a
    scope on the page (e.g. a login screen first), targets resolve globally.
    wait "for": "change" | "state:<value>" | "time:<sec>" | "url:<substring>".
    Omit for the auto single arc (type -> click -> wait).
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    def now() -> float:
        return round(time.monotonic() - t0, 3)

    cursor: list[dict] = []

    def move(pg, x: float, y: float, dur: float = 0.6):
        frm = cursor[-1] if cursor else {"x": 0.88, "y": 0.96}
        cursor.append({"t": now(), "x": frm["x"], "y": frm["y"]})
        pg.mouse.move(x, y, steps=max(2, int(dur * 40)))
        cursor.append({"t": now(), "x": x / VIEWPORT["width"], "y": y / VIEWPORT["height"]})

    with sync_playwright() as p:
        browser, ctx = _launch(p, out_dir, record=True, chromium_args=chromium_args)
        page = ctx.new_page()
        t0 = time.monotonic()  # video starts ~here
        cursor.append({"t": 0.0, "x": 0.88, "y": 0.96})

        page.goto(url, wait_until="networkidle")
        # Hide the native cursor — polish draws its own eased one.
        try:
            page.add_style_tag(content="* { cursor: none !important; }")
        except Exception:
            pass
        sel = f'[data-clep="{name}"]'
        has_scope = True
        try:
            page.wait_for_selector(sel, timeout=10000)
        except Exception:
            # No instrumented scope (yet) — allowed when the plan starts
            # elsewhere (e.g. a login screen) via goto steps; targets then
            # resolve globally until the scope appears.
            wants_goto = any(isinstance(s, dict) and str(s.get("action") or "").lower() == "goto"
                             for s in (steps or []))
            if not wants_goto:
                browser.close()
                raise RuntimeError(f"[agent] no element [data-clep=\"{name}\"] at {url}")
            has_scope = False
        page.wait_for_timeout(700)  # settle
        establish_end = now()

        sdk = _sdk_export(page, name) or {}
        info = _node_info(page, name) or {}
        box = info.get("box") or {}
        el = {"x": max(box.get("x", 0), 0) / VIEWPORT["width"],
              "y": max(box.get("y", 0), 0) / VIEWPORT["height"],
              "w": box.get("w", 0) / VIEWPORT["width"],
              "h": box.get("h", 0) / VIEWPORT["height"]}

        # Query text: explicit > SDK steps > input hint > default.
        q = query
        if not q:
            for s in (sdk.get("steps") or []):
                if isinstance(s, dict) and s.get("type") == "type" and s.get("text"):
                    q = s["text"]
                    break
        q = q or (info.get("input") or {}).get("text") or "AI browser agents"

        # ── Steps plan: explicit multi-step across states, or auto ──────
        # Step schema: {"action": "type"|"click"|"wait", "target": ..., ...}
        #   type:  {"target": "input"|css, "value": "text"}
        #   click: {"target": "primary"|"action:export"|"text:Export"|css}
        #   wait:  {"for": "change"|"state:completed"|"time:1.5"|"url:/dashboard"|"appear:text=Download", "timeout": 4}
        # Target shortcuts resolve inside [data-clep=name]; anything else is
        # treated as a CSS selector (scoped, then global fallback).
        in_sel = f'{sel} input:not([type="file"]), {sel} textarea, {sel} [contenteditable]'
        file_sel = f'{sel} input[type="file"]'
        btn_sel = f'{sel} [data-clep-action="primary"], {sel} button, {sel} input[type=submit]'
        has_input = page.locator(in_sel).count() > 0
        has_button = page.locator(btn_sel).count() > 0

        def _auto_steps() -> list:
            auto = []
            if has_input:
                auto.append({"action": "type", "target": "input", "value": q})
            if has_button:
                auto.append({"action": "click", "target": "primary"})
            auto.append({"action": "wait", "for": "change", "timeout": 4.0})
            return auto

        plan_steps = steps or _auto_steps()

        # Subtree signature: any data-clep-state change, node churn, or text
        # growth inside the feature counts as a state transition (the state
        # often lives on inner nodes, not the [data-clep] root).
        sig_js = """(name) => {
          const root = document.querySelector(`[data-clep="${name}"]`);
          if (!root) return '';
          const states = [...root.querySelectorAll('[data-clep-state]')]
            .map(e => e.getAttribute('data-clep-state')).join(',');
          return root.getAttribute('data-clep-state') + '|' + states + '|'
            + root.childElementCount + '|' + root.innerText.length;
        }"""
        states_js = """(name) => {
          const root = document.querySelector(`[data-clep="${name}"]`);
          if (!root) return [];
          const all = [root, ...root.querySelectorAll('[data-clep-state]')];
          return [...new Set(all.map(e => e.getAttribute('data-clep-state')).filter(Boolean))];
        }"""

        def _sig():
            try:
                s = page.evaluate(sig_js, name)
            except Exception:
                s = ""
            if s:
                return "S:" + s
            # No [data-clep] root (uninstrumented page): fall back to a
            # document-level signature so change-waits still fire.
            try:
                d = page.evaluate(
                    "() => document.body ? (document.body.innerText.length + '|' + "
                    "document.body.getElementsByTagName('*').length) : ''")
                return ("D:" + d) if d else None
            except Exception:
                return None

        def _states() -> list:
            try:
                return page.evaluate(states_js, name) or []
            except Exception:
                return []

        def _center(loc):
            bb = loc.bounding_box() or {}
            return (bb.get("x", 0) + bb.get("width", 0) / 2,
                    bb.get("y", 0) + bb.get("height", 0) / 2)

        def _resolve(target: str | None):
            """-> (locator|None, label) scoped to the feature, else global."""
            pre = f"{sel} " if has_scope else ""
            t = (target or "").strip()
            if t in ("", "input"):
                # first VISIBLE text-ish field (skips hidden file inputs)
                loc = page.locator(f'{pre}input:visible, {pre}textarea:visible, '
                                   f'{pre}[contenteditable]:visible')
                return (loc, "input")
            if t in ("file", "input-file"):
                return (page.locator(f"{pre}input[type=file]"), "file")
            if t == "primary":
                loc = page.locator(f'{pre}[data-clep-action="primary"], {pre}button, '
                                   f'{pre}input[type=submit]')
                try:
                    return (loc, (loc.first.inner_text() or "").strip()[:24] or "primary")
                except Exception:
                    return (loc, "primary")
            if t.startswith("action:"):
                loc = page.locator(f'{sel} [data-clep-action="{t[7:]}"]') if has_scope else page.locator(
                    f'[data-clep-action="{t[7:]}"]')
                return (loc, t[7:] or t)
            if t.startswith("text:"):
                needle = t[5:]
                scope = sel if has_scope else "body"
                loc = page.locator(scope + " button", has_text=needle)
                if loc.count() == 0:
                    loc = page.locator(scope, has_text=needle)
                return (loc, needle[:24])
            loc = page.locator(f"{sel} {t}") if has_scope else page.locator(t)
            if has_scope and loc.count() == 0:
                loc = page.locator(t)
            return (loc, t[:24])

        def _wait_step(spec: dict) -> tuple[float | None, list]:
            want = str(spec.get("for") or "change")
            timeout = float(spec.get("timeout") or 4.0)
            if want.startswith("time:"):
                try:
                    page.wait_for_timeout(int(float(want[5:]) * 1000))
                except Exception:
                    pass
                return now(), _states()
            if want.startswith("url:"):
                goal = want[4:]
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        if goal in page.url:
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=3000)
                            except Exception:
                                pass
                            return now(), _states()
                    except Exception:
                        pass
                    page.wait_for_timeout(250)
                return None, _states()
            if want.startswith("appear:"):
                asel = want[7:]
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        loc = page.locator(asel)
                        if loc.count() > 0:
                            try:
                                loc.first.scroll_into_view_if_needed(timeout=1500)
                            except Exception:
                                pass
                            return now(), _states()
                    except Exception:
                        pass
                    page.wait_for_timeout(400)
                return None, _states()
            if want.startswith("state:"):
                goal = want[6:]
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    st = _states()
                    if goal in st:
                        return now(), st
                    page.wait_for_timeout(200)
                return None, _states()
            # default: any subtree change
            before = _sig()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                cur = _sig()
                if cur and cur != before:
                    return now(), _states()
                page.wait_for_timeout(200)
            return None, _states()

        try:
            sig_before = page.evaluate(sig_js, name)
        except Exception:
            sig_before = ""
        _ = sig_before
        events: list[dict] = []
        clicks: list[dict] = []
        focuses: list[dict] = []
        states_seen: list[str] = []
        for _s in _states():
            if _s not in states_seen:
                states_seen.append(_s)
        typing = {"text": "", "t0": 0.0, "t1": 0.0}
        first_click = None
        first_input_xy = first_button_xy = None
        btn_label = ""
        query_text = ""
        NX, NY = VIEWPORT["width"], VIEWPORT["height"]

        for i, st in enumerate(plan_steps):
            act = str(st.get("action") or "").lower()
            if act == "goto":
                dest = st.get("url") or ""
                page.goto(dest, wait_until="domcontentloaded")
                page.wait_for_timeout(900)
                # The scope may appear on this page — refresh everything that
                # depends on it (selectors, bbox, states).
                has_scope = page.locator(sel).count() > 0
                if has_scope:
                    try:
                        nb = _node_info(page, name) or {}
                        nbox = nb.get("box") or {}
                        if nbox.get("w"):
                            el.update({"x": max(nbox.get("x", 0), 0) / NX,
                                       "y": max(nbox.get("y", 0), 0) / NY,
                                       "w": nbox.get("w", 0) / NX,
                                       "h": nbox.get("h", 0) / NY})
                    except Exception:
                        pass
                for _s in _states():
                    if _s not in states_seen:
                        states_seen.append(_s)
                events.append({"t": now(), "kind": "goto", "step": i,
                               "x": 0.5, "y": 0.5, "label": dest[:60]})
            elif act == "type":
                loc, _lbl = _resolve(st.get("target") or "input")
                if loc is None or loc.count() == 0:
                    events.append({"t": now(), "kind": "skip", "step": i,
                                   "x": 0.5, "y": 0.5, "label": f"type:{st.get('target')}?missing"})
                    continue
                loc.first.scroll_into_view_if_needed()
                x, y = _center(loc.first)
                move(page, x, y)
                page.mouse.click(x, y)
                page.wait_for_timeout(250)
                val = st.get("value") or q
                try:
                    secret = (loc.first.get_attribute("type") or "").lower() == "password"
                except Exception:
                    secret = False
                t_start = now()
                try:
                    loc.first.fill("")
                except Exception:
                    page.keyboard.press("ControlOrMeta+a")
                    page.keyboard.press("Backspace")
                page.keyboard.type(val, delay=int(1000 / max(type_cps, 1)))
                t_end = now()
                shown = "••••••" if secret else val[:40]
                if not typing["text"]:
                    typing.update({"text": "" if secret else val, "t0": t_start, "t1": t_end})
                    query_text = "" if secret else val
                if not first_input_xy:
                    first_input_xy = {"x": round(x / NX, 4), "y": round(y / NY, 4)}
                focuses.append({"t": t_start, "x": x / NX, "y": y / NY, "label": "type"})
                events.append({"t": t_end, "kind": "type", "step": i,
                               "x": x / NX, "y": y / NY, "label": shown})
            elif act == "click":
                loc, lbl = _resolve(st.get("target") or "primary")
                if loc is None or loc.count() == 0:
                    events.append({"t": now(), "kind": "skip", "step": i,
                                   "x": 0.5, "y": 0.5, "label": f"click:{st.get('target')}?missing"})
                    continue
                loc.first.scroll_into_view_if_needed()
                x, y = _center(loc.first)
                try:
                    lbl = (loc.first.inner_text() or "").strip()[:24] or lbl
                except Exception:
                    pass
                move(page, x, y)
                t_click = now()
                page.mouse.click(x, y)
                ev = {"t": t_click, "x": x / NX, "y": y / NY}
                clicks.append(ev)
                cursor.append({"t": t_click, **ev, "click": True})
                focuses.append({"t": t_click, "x": x / NX, "y": y / NY, "label": lbl})
                events.append({"t": t_click, "kind": "click", "step": i,
                               "x": x / NX, "y": y / NY, "label": lbl})
                if not first_click:
                    first_click = ev
                if not btn_label:
                    btn_label = lbl
                if not first_button_xy:
                    first_button_xy = {"x": round(x / NX, 4), "y": round(y / NY, 4)}
            elif act == "upload":
                loc, _lbl = _resolve(st.get("target") or "file")
                fpath = st.get("file")
                p = Path(fpath) if fpath else None
                if p is not None and not p.is_absolute():
                    p = REPO_ROOT / p
                if loc is None or loc.count() == 0 or p is None or not p.exists():
                    events.append({"t": now(), "kind": "skip", "step": i,
                                   "x": 0.5, "y": 0.5,
                                   "label": f"upload:{st.get('target') or st.get('file')}?missing"})
                else:
                    # Cursor rides to the visible dropzone; the file goes to
                    # the (possibly hidden) input. Real OS-level upload on tape.
                    try:
                        proxy = page.locator(f"{sel} .dz-card, {sel} .dropzone, {sel} [role=button]").first
                        px, py = _center(proxy) if proxy.count() else (NX / 2, NY / 2)
                    except Exception:
                        px, py = NX / 2, NY / 2
                    move(page, px, py)
                    page.wait_for_timeout(300)
                    t_up = now()
                    loc.first.set_input_files(str(p))
                    focuses.append({"t": t_up, "x": px / NX, "y": py / NY, "label": "upload"})
                    events.append({"t": t_up, "kind": "upload", "step": i,
                                   "x": px / NX, "y": py / NY, "label": p.name[:40]})
            elif act == "wait":
                changed_at, st8 = _wait_step(st)
                for _s in st8:
                    if _s not in states_seen:
                        states_seen.append(_s)
                t_ev = changed_at if changed_at is not None else now()
                events.append({"t": t_ev, "kind": "state", "step": i,
                               "x": 0.5, "y": 0.5,
                               "label": ",".join(st8[-2:]) if st8 else "settled"})
                if changed_at is not None:
                    page.wait_for_timeout(1000)  # let the new state settle on tape
                else:
                    page.wait_for_timeout(700)
            else:
                events.append({"t": now(), "kind": "skip", "step": i,
                               "x": 0.5, "y": 0.5, "label": f"unknown:{act}"})

        state_evts = [e for e in events if e["kind"] == "state"]
        result_at = state_evts[-1]["t"] if state_evts else now()

        # Rest cursor out of the way for the closing hold.
        try:
            move(page, VIEWPORT["width"] * 0.80, VIEWPORT["height"] * 0.72, dur=0.5)
        except Exception:
            pass
        page.wait_for_timeout(500)
        total = now()

        video = page.video
        vpath = None
        ctx.close()
        if video:
            vpath = Path(str(video.path()))
        browser.close()

    # Consolidate: drop the synthetic start point once real moves exist.
    trace = {
        "kind": "clep-trace",
        "name": name,
        "title": (sdk.get("title") if isinstance(sdk, dict) else None) or name.replace("-", " ").title(),
        "url": url,
        "viewport": {"w": VIEWPORT["width"], "h": VIEWPORT["height"]},
        "video": str(vpath) if vpath else None,
        "duration": total,
        "trim_start": max(0.0, round(establish_end - 1.1, 3)),
        "element": {k: round(float(v), 4) for k, v in el.items()},
        "input_xy": first_input_xy,
        "button_xy": first_button_xy,
        "cursor": cursor,
        "typing": typing,
        "click": first_click,
        "clicks": clicks,
        "focuses": focuses,
        "events": events,
        "states_seen": states_seen,
        "steps": plan_steps,
        "button_label": btn_label,
        "query": query_text or q,
        "result_at": result_at,
        "has_input": has_input,
        "has_button": has_button,
    }
    tpath = out_dir / "trace.json"
    tpath.write_text(json.dumps(trace, indent=2))
    print(f"[agent] raw video -> {vpath} ({total:.1f}s, trim {trace['trim_start']}s)")
    print(f"[agent] trace -> {tpath}")
    return trace
