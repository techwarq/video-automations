"""
Motion-only planner — AI director for pure kinetic typography videos (no talking head).

Same timing logic as pipeline/planner.py but the creative brief is tuned for
the reference style: white + vivid blue (#010CCA), bold sans, UI mocks,
search typing, badge + cards, big stat, grid. No photos needed.

Beats use `visual_kind="kinetic"` with a `kinetic` spec. Legacy image/diagram
still accepted for mixed edits, but the default recommendation is kinetic.
"""

import json
import re
from pathlib import Path

from openai import OpenAI

import config

VALID_TEXT_STYLES = {"headline", "stat", "label"}
VALID_MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right"}
VALID_VISUAL_KINDS = {"image", "diagram", "video_clip", "kinetic", "clean"}
VALID_ROLES = {"hook", "body", "payoff"}
VALID_DIAGRAM_STYLES = {"dark", "whiteboard", "light", "blue", "gray", "talo", "talo_dark", "paper", "olive"}
VALID_NODE_SHAPES = {"rect", "oval", "plain"}
VALID_ARROW_PATHS = {"straight", "elbow"}
VALID_CAPTION_MODES = {"on", "off"}

VALID_KINETIC_STYLES = {"light", "blue", "gray", "dark", "whiteboard", "talo", "talo_dark", "paper", "olive", "clep", "clep_lime", "clep_dark"}
VALID_KINETIC_LAYOUTS = {"headline","search_typing","badge_cards","stat_blue","grid_cards","bars","diagram","checklist","task_box","ticker_grid","talo_hero","talo_logo","chat_slick","browser_work","spreadsheet","spreadsheet_result","chaos_cards","editorial_black","editorial_statement","clep_chaos","clep_false","clep_enter","clep_dropzone","clep_icons","clep_trust","clep_range","clep_lockup"}

TRANSITION_MENU = [
    ("cut", 0.07, "hard cut — punchy, after the hook or between rapid beats"),
    ("fade", 0.25, "neutral dissolve"),
    ("fadeblack", 0.32, "dip to black — new thought"),
    ("fadewhite", 0.32, "dip to white — impact/emphasis (useful for blue stat moments)"),
    ("dissolve", 0.35, "slow dissolve — time passing"),
    ("smoothleft", 0.30, "directional wipe — forward momentum"),
    ("smoothright", 0.30, "directional wipe"),
    ("slideleft", 0.28, "slide — playful energy"),
    ("slideright", 0.28, "slide — playful energy"),
    ("circleopen", 0.34, "circle reveal — spotlight"),
    ("hblur", 0.28, "whip-pan blur — high energy"),
    ("zoomin", 0.30, "zoom punch — slam into next visual"),
    ("distance", 0.30, "3D push — dramatic shift"),
    ("squeezev", 0.28, "vertical squeeze — snappy"),
]
VALID_TRANSITIONS = {t[0] for t in TRANSITION_MENU}

def _transition_menu_text() -> str:
    return "\n".join(f'  - "{n}" ({d}s): {desc}' for n,d,desc in TRANSITION_MENU)

# ── Kinetic layout guide for the LLM ─────────────────────────────────────
_KINETIC_GUIDE = """
KINETIC LAYOUTS (pick one per beat via `kinetic: {style, layout, ...}`):
Styles: "talo" (warm paper #FDFAF3 + periwinkle #8C9BCE editorial), "talo_dark" (black for logo/problem), "paper"/"talo" same, "olive" (dark olive #2B3329 + acid lime), "light" (white+vivid blue), "blue", "gray". For Talo film prefer "talo" / "talo_dark" / "olive" — avoid "light/blue" unless price punch.

1. "talo_hero" — editorial lockup: line1 italic serif (muted), line2 bold sans with periwinkle accent underline. THE hero for Talo.
   Keys: `line1` (italic top), `line2` (bold bottom), `accent` (substring of line2 to color periwinkle).
   Example: {"style":"talo","layout":"talo_hero","line1":"Have work to do?","line2":"Hire an AI Freelancer.","accent":"Freelancer."}

2. "headline" — centered bold typography, paper bg, accent in periwinkle or acid. Keep 6-12 words.
   Keys: `text` (use \\n for 2-line), `accent` (substring).
   Example: {"style":"talo","layout":"headline","text":"We give your agent\\nsearch & fetch 100% FREE.","accent":"100% FREE."}

3. "editorial_black" — black bg editorial problem statement with flash counts (Image: YOU HAVE WORK TO DO. flashes). Use for 0:00 THE PROBLEM.
   Keys: `main` ("YOU HAVE WORK TO DO."), `flashes` (["2,431 leads to research","18,000 records to clean", ...]), `final` ("AND YOU DON'T WANT TO DO IT.")
   Example: {"style":"talo_dark","layout":"editorial_black","main":"YOU HAVE WORK TO DO.","flashes":["2,431 leads to research","18,000 records to clean","500 invoices to process","7,200 products to upload"],"final":"AND YOU DON'T WANT TO DO IT."}

4. "chaos_cards" — three tilted cards for THE OLD WAY (HIRE SOMEONE $18-30/hr etc) that freeze to THERE'S ANOTHER WAY.
   Keys: `cards` ([{title, price, foot} x3]), `freeze_text` ("THERE'S ANOTHER WAY.")
   Example: {"style":"talo","layout":"chaos_cards","cards":[{"title":"HIRE SOMEONE","price":"$18–30/hr","foot":"WAITING..."},{"title":"DO IT YOURSELF","price":"4 HOURS","foot":"YOUR TIME"},{"title":"USE AN AI TOOL","price":"YOU STILL HAVE TO","foot":"OPERATE IT"}],"freeze_text":"THERE'S ANOTHER WAY."}

5. "chat_slick" — slick chat input with faint talo watermark behind, typing query, blue START WORK button, pills TASK ACCEPTED / ESTIMATE / MAXIMUM. Use for GIVE TALO A JOB.
   Keys: `query` (user prompt), `badge` ("TASK ACCEPTED"), `estimate` ("ESTIMATE: 5–7 HOURS"), `maximum` ("MAXIMUM: $70"), `response` (optional Talo reply like "Got it.").
   Example: {"style":"talo","layout":"chat_slick","query":"Find 1,000 US SaaS companies, their founders, funding and LinkedIn profiles.","badge":"TASK ACCEPTED","estimate":"ESTIMATE: 5–7 HOURS","maximum":"MAXIMUM: $70","response":"Got it."}

6. "task_box" — alias to chat_slick (older name, still supported).

7. "browser_work" — browser window mock with checklist + ticker + runtime/cost pills. THE COOLEST PART (0:19 WATCH IT WORK).
   Keys: `items` ([{label,status} 4 items like SEARCHING COMPANIES done/active]), `count_start` (127), `count_end` (1000), `ticker_label` ("companies"), `runtime` ("03h 47m"), `cost` ("CURRENT COST $37.83"), `url` ("talo.abstraklabs.com • Working...")
   Example: {"style":"talo","layout":"browser_work","items":[{"label":"SEARCHING COMPANIES","status":"done"},{"label":"VERIFYING WEBSITES","status":"done"},{"label":"MATCHING FOUNDERS","status":"done"},{"label":"ENRICHING DATA","status":"active"}],"count_start":127,"count_end":1000,"ticker_label":"companies","runtime":"03h 47m","cost":"CURRENT COST $37.83"}

8. "spreadsheet_result" / "spreadsheet" — result spreadsheet with rows, DONE stamp, stats 4h18m $43. Use for THE RESULT.
   Keys: `headers` (["COMPANY","FOUNDER","FUNDING","LINKEDIN"]), `time` ("4h 18m"), `price` ("$43.00"), `tally` ("1,000 COMPANIES • 1,000 FOUNDERS"), `sub` ("You paid for the work actually performed.")
   Example: {"style":"talo","layout":"spreadsheet_result","headers":["COMPANY","FOUNDER","FUNDING","LINKEDIN"],"time":"4h 18m","price":"$43.00","tally":"1,000 COMPANIES • 1,000 FOUNDERS • FUNDING • LINKEDIN"}

9. "editorial_statement" — huge italic + bold statement over paper grain. Use for YOU DON'T NEED ANOTHER AI TOOL / YOU NEED THE WORK DONE.
   Keys: `line1` ("YOU DON'T NEED ANOTHER AI TOOL."), `line2` ("YOU NEED THE WORK DONE."), `accent` ("WORK DONE.")
   Example: {"style":"talo","layout":"editorial_statement","line1":"YOU DON'T NEED ANOTHER AI TOOL.","line2":"YOU NEED THE WORK DONE.","accent":"WORK DONE."}

10. "talo_logo" — black screen with white 'talo' logotype + tagline pill. FINAL BRAND.
    Keys: `text` ("talo"), `tagline` ("talo.abstraklabs.com · $10 / HOUR")
    Example: {"style":"talo_dark","layout":"talo_logo","text":"talo","tagline":"talo.abstraklabs.com  ·  $10 / HOUR · NO SUBSCRIPTION"}

11. "checklist" — vertical progress list (TALO IS WORKING) with done/active/pending. Alternative work view.
    Keys: `title` ("TALO IS WORKING"), `items` ([{label,status}])

12. "grid_cards" — 4 cards + bottom pill (summary). Keep for generic reels but prefer spreadsheet for Talo.

13. "badge_cards" / "search_typing" / "bars" / "diagram" — legacy clean layouts (white+blue). Usable but prefer Talo set for this brief.

RULES:
- Prefer kinetic over image — only use "image" when a CONCRETE photo is genuinely better than typography (rare for motion-only).
- For kinetic beats set `image_query` to null.
- For Talo film: Hook (beat 1) should be "editorial_black" with style talo_dark — huge italic YOU HAVE WORK TO DO. — not a headline.
- Follow the editorial film arc explicitly: 0:00 PROBLEM (editorial_black, talo_dark) → 0:04 OLD WAY (chaos_cards) → 0:08 TALO INTRO (talo_hero, paper) → 0:12 GIVE TALO A JOB (chat_slick) → 0:19 WATCH IT WORK (browser_work or checklist) → 0:29 RESULT (spreadsheet_result) → 0:34 CATEGORY (editorial_statement, olive or talo) → 0:40 FINAL BRAND (talo_logo, talo_dark). Even if script words vary, keep this visual order and map narration to it.
- Keep palette editorial: paper / black / dark olive / muted blue; acid-green ONLY for WORKING/DONE/price/live dot — 1-2 beats max.
- Typography contrast: huge italic serif for headlines (YOU HAVE WORK...) + monospace/utilitarian for SEARCHING / 4h 18m / $43.00 — encode via layout choice, not manual font.
- Don't explain how AI works. No model names, no LLM→browser→MCP diagrams. Mental model is: TASK → PRICE → PAY → AI FREELANCER WORKS → RESULT.
- Vary layouts: no same layout twice in a row. Keep all coordinates in 0.08-0.92.
- Overlays: every paper beat auto-adds faint rotated 'talo' watermark at ~4.5% opacity — don't try to add second logo.
"""

SYSTEM_PROMPT = """You are a top-tier editorial motion director for Talo (the AI freelancer marketplace, talo.abstraklabs.com). Your film feels like an old editorial film about the future of work — not a polished SaaS commercial. Palette: warm paper #FDFAF3, near-black, dark olive #2B3329, muted periwinkle #8C9BCE, tiny acid-lime #D6FF2C only for WORKING/DONE/price. Typography: huge italic serif for human headlines (YOU HAVE WORK TO DO. / HIRE AN AI FREELANCER.) + clean monospace/utilitarian for SEARCHING / VERIFYING / 4h 18m / $43.00. Pipeline executes literally.

TALO FILM STRUCTURE (burn this in, even if script wording drifts — map narration to these visuals):
- Beat 1 is HOOK: role="hook", layout "editorial_black" style talo_dark, 3-4s. Huge italic YOU HAVE WORK TO DO. + rapid flashes (2,431 leads / 18,000 records etc) + final AND YOU DON'T WANT TO DO IT. Black screen, keyboard pings, blinking cursor. This is non-negotiable — don't use headline.
- Beat 2 is OLD WAY: role="body", layout "chaos_cards" style talo. Three cards HIRE SOMEONE / DO IT YOURSELF / USE AN AI TOOL scattered then freeze THERE'S ANOTHER WAY.
- Beat 3 is TALO INTRO: role="body", layout "talo_hero" style talo/paper. Cream paper + faint watermark talo behind. Hero lockup: HIRE AN AI FREELANCER. / GIVE THEM THE WORK. / THEY GET IT DONE.
- Beat 4 is GIVE TALO A JOB: role="body", layout "chat_slick" style talo. Slick chat box with faint talo watermark overlay, query types itself, blue START WORK button, pills TASK ACCEPTED / ESTIMATE / MAXIMUM and Talo reply Got it. / $70.
- Beat 5 is WATCH IT WORK: role="body", layout "browser_work" style talo (THE COOLEST PART). Browser window + typography overlays SEARCHING/ VERIFYING / MATCHING / ENRICHING + ticker 127 → 1000 + runtime 03h47m + CURRENT COST $37.83 acid dot. Must feel like Talo isn't chatting — it's doing.
- Beat 6 is RESULT: role="body", layout "spreadsheet_result" style talo. Quiet spreadsheet 1,000 QUALIFIED / FOUNDERS / FUNDING / LINKEDIN, huge DONE stamp, stats 4h 18m $43.00 (acid price), sub "You paid for the work actually performed."
- Beat 7 is CATEGORY: role="body", layout "editorial_statement" style talo or olive. Grainy paper: YOU DON'T NEED ANOTHER AI TOOL. → YOU NEED THE WORK DONE. (accent WORK DONE. in periwinkle or acid if olive).
- Last beat is PAYOFF: role="payoff", layout "talo_logo" style talo_dark. Black screen, white talo wordmark with huge ghost watermark behind, tagline $10 / HOUR + CTA GIVE US A TASK →.
- If script is longer/shorter than 8 beats, merge/split but KEEP the 8-beat editorial spine and visual order; compress category if needed. Never drop PROBLEM or FINAL BRAND.
- Beats 1 and last are hook/payoff. Middle is body.

NARRATION (non-negotiable):
- `narration_snippet` fields concatenated with single spaces must reconstruct ENTIRE input script EXACTLY (every word, nothing added/skipped/reordered). Captions sync to these.
- Split at sentence/clause boundaries, 4-14 words per beat (hook/payoff can be shorter).
- `duration_hint`: seconds estimate, nudges pacing within ±15%.

VISUALS — primary is kinetic (pure typography/UI), fallback is image/diagram/video_clip:
- Use "kinetic" for almost every beat in this pipeline (see KINETIC LAYOUTS guide below).
- Use "image" only if a concrete photo beats typography (rare). Give `image_query` short/concrete.
- Use "diagram" (animated system boxes/arrows) for flows — style light/blue/gray.
- Use "video_clip" only if local screen recording is stronger; give `video_query` keywords and fallback `image_query`.

{KINETIC_GUIDE}

MOTION (image beats only): `motion` = zoom_in|zoom_out|pan_left|pan_right. Vary consecutive beats.

TRANSITIONS — `transition` on every beat except last:
{transition_menu}

ON-SCREEN TEXT: For kinetic beats, text lives inside `kinetic` spec, so set `on_screen_text` null. For image beats, `on_screen_text` is 2-5 word phrase (or null). HOOK must have either kinetic headline or on_screen_text.

CAPTIONS: `caption_mode` "on" (default) or "off" — usually "off" for motion-only since typography is the caption; only enable if you want lower-third captions too.

Respond with ONLY raw JSON array (no markdown fences), objects with exactly:
narration_snippet, role, visual_kind, image_query, video_query, on_screen_text, text_style, motion, transition {{"type","duration"}}, caption_mode, duration_hint,
plus `kinetic` object when visual_kind="kinetic" (or "clean"), plus `diagram` when visual_kind="diagram".

Use null for fields that don't apply. Target runtime {target_seconds}s.
"""

def _system_prompt() -> str:
    return (SYSTEM_PROMPT
            .replace("{target_seconds}", str(int(config.TARGET_DURATION_SECONDS)))
            .replace("{transition_menu}", _transition_menu_text())
            .replace("{KINETIC_GUIDE}", _KINETIC_GUIDE))

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

def _normalize_diagram(raw: dict, index: int) -> dict:
    cleaned: dict = {"style": raw.get("style") if raw.get("style") in VALID_DIAGRAM_STYLES else "light",
                     "nodes":[], "arrows":[], "callouts":[]}
    seen=set()
    for i, n in enumerate(raw.get("nodes",[]) or []):
        if not isinstance(n, dict) or not str(n.get("label","")).strip():
            print(f"[planner] beat {index}: dropping invalid node {i}"); continue
        nid=str(n.get("id") or f"n{len(cleaned['nodes'])}")
        if nid in seen: nid=f"{nid}_{i}"
        seen.add(nid)
        cleaned["nodes"].append({
            "id":nid, "label":str(n["label"]).strip().upper()[:28],
            "x": min(max(float(n.get("x",0.5)),0.02),0.98),
            "y": min(max(float(n.get("y",0.4)),0.02),0.98),
            "w": min(max(float(n.get("w",0.24)),0.08),0.6),
            "h": min(max(float(n.get("h",0.18)),0.06),0.5),
            "shape": n.get("shape") if n.get("shape") in VALID_NODE_SHAPES else "rect",
            "emph": bool(n.get("emph")),
        })
    for a in raw.get("arrows",[]) or []:
        if not isinstance(a,dict): continue
        src,dst=str(a.get("from","")), str(a.get("to",""))
        if src not in seen or dst not in seen or src==dst:
            print(f"[planner] beat {index}: dropping arrow {src!r}->{dst!r}"); continue
        cleaned["arrows"].append({"from":src,"to":dst,
                                   "label": (str(a["label"]).strip().upper()[:18] if a.get("label") else None),
                                   "path": a.get("path") if a.get("path") in VALID_ARROW_PATHS else "straight"})
    for c in raw.get("callouts",[]) or []:
        if isinstance(c,dict) and str(c.get("text","")).strip():
            cleaned["callouts"].append({"text":str(c["text"]).strip().upper()[:32],
                                         "x": min(max(float(c.get("x",0.5)),0.05),0.95),
                                         "y": min(max(float(c.get("y",0.85)),0.05),0.95),
                                         "big":bool(c.get("big")),"emph":bool(c.get("emph"))})
    if not cleaned["nodes"] and not cleaned["callouts"]:
        raise ValueError(f"Beat {index}: diagram has no usable nodes/callouts.")
    return cleaned

def _normalize_kinetic(beat: dict, index: int) -> dict:
    raw = beat.get("kinetic") or beat.get("clean") or beat.get("mgfx") or {}
    if not isinstance(raw, dict):
        raw = {}
    layout = raw.get("layout") or raw.get("type") or "headline"
    if layout not in VALID_KINETIC_LAYOUTS:
        print(f"[planner] beat {index}: unknown kinetic layout {layout!r} -> headline")
        layout = "headline"
    style = raw.get("style") if raw.get("style") in VALID_KINETIC_STYLES else "talo"
    cleaned = {"style": style, "layout": layout}
    # pass through layout-specific keys with light validation
    if layout == "headline":
        txt = str(raw.get("text") or raw.get("headline") or beat.get("on_screen_text") or beat.get("narration_snippet","")).strip()
        if not txt:
            txt = "UNTITLED"
        cleaned["text"] = txt[:180]
        acc = raw.get("accent") or raw.get("accent_phrase")
        if acc:
            if isinstance(acc, list):
                cleaned["accent"] = [str(a)[:28] for a in acc if str(a).strip()][:4]
            else:
                cleaned["accent"] = str(acc)[:36]
    elif layout == "editorial_black":
        cleaned["main"] = str(raw.get("main") or raw.get("line1") or raw.get("text") or "YOU HAVE WORK TO DO.")[:60]
        flashes = raw.get("flashes") or raw.get("items") or []
        if isinstance(flashes, list):
            cleaned["flashes"] = [str(f)[:40] for f in flashes[:6] if str(f).strip()]
        else:
            cleaned["flashes"] = []
        if not cleaned["flashes"]:
            cleaned["flashes"] = ["2,431 leads to research","18,000 records to clean","500 invoices to process","7,200 products to upload"]
        cleaned["final"] = str(raw.get("final") or raw.get("line2") or "AND YOU DON'T WANT TO DO IT.")[:60]
        if cleaned["style"] not in ("talo_dark","olive","dark"):
            cleaned["style"] = "talo_dark"
    elif layout == "chaos_cards":
        cards_in = raw.get("cards") or []
        cards=[]
        for c in (cards_in[:4] if isinstance(cards_in, list) else []):
            if not isinstance(c, dict): continue
            cards.append({"title": str(c.get("title") or c.get("text") or "")[:28], "price": str(c.get("price") or c.get("value") or "")[:28], "foot": str(c.get("foot") or c.get("sub") or "")[:20]})
        if len(cards)<3:
            cards= [{"title":"HIRE SOMEONE","price":"$18–30/hr","foot":"WAITING..."},{"title":"DO IT YOURSELF","price":"4 HOURS","foot":"YOUR TIME"},{"title":"USE AN AI TOOL","price":"YOU STILL HAVE TO","foot":"OPERATE IT"}]
        cleaned["cards"]=cards[:3]
        cleaned["freeze_text"] = str(raw.get("freeze_text") or "THERE'S ANOTHER WAY.")[:36]
    elif layout == "chat_slick":
        cleaned["query"] = str(raw.get("query") or raw.get("text") or beat.get("narration_snippet","")).strip()[:160]
        cleaned["badge"] = str(raw.get("badge") or "TASK ACCEPTED")[:24]
        cleaned["estimate"] = str(raw.get("estimate") or "ESTIMATE: 5–7 HOURS")[:28]
        cleaned["maximum"] = str(raw.get("maximum") or "MAXIMUM: $70")[:24]
        resp = raw.get("response") or raw.get("talo_response")
        if resp:
            cleaned["response"] = str(resp)[:40]
    elif layout == "browser_work":
        items_in = raw.get("items") or raw.get("steps") or []
        items=[]
        for it in (items_in[:6] if isinstance(items_in, list) else []):
            if isinstance(it, dict):
                items.append({"label": str(it.get("label") or it.get("text") or "")[:36], "status": str(it.get("status") or "pending")[:10]})
            elif isinstance(it, str):
                items.append({"label": it[:36], "status": "pending"})
        if not items:
            items=[{"label":"SEARCHING COMPANIES","status":"done"},{"label":"VERIFYING WEBSITES","status":"done"},{"label":"MATCHING FOUNDERS","status":"done"},{"label":"ENRICHING DATA","status":"active"}]
        cleaned["items"]=items
        cleaned["count_start"] = int(raw.get("count_start") or raw.get("start") or 127)
        cleaned["count_end"] = int(raw.get("count_end") or raw.get("count") or raw.get("end") or 1000)
        cleaned["ticker_label"] = str(raw.get("ticker_label") or raw.get("label") or "companies")[:20]
        cleaned["runtime"] = str(raw.get("runtime") or "03h 47m")[:16]
        cleaned["cost"] = str(raw.get("cost") or "CURRENT COST $37.83")[:24]
        cleaned["url"] = str(raw.get("url") or "talo.abstraklabs.com  •  Working...")[:36]
        if raw.get("sequence"):
            seq = raw.get("sequence")
            if isinstance(seq, list):
                cleaned["sequence"] = [int(x) for x in seq[:6] if str(x).strip().isdigit() or isinstance(x,int)]
    elif layout == "spreadsheet" or layout == "spreadsheet_result":
        layout = "spreadsheet_result"
        cleaned["layout"] = "spreadsheet_result"
        cleaned["headers"] = [str(h)[:16] for h in (raw.get("headers") or ["COMPANY","FOUNDER","FUNDING","LINKEDIN"])[:4]]
        cleaned["time"] = str(raw.get("time") or "4h 18m")[:12]
        cleaned["price"] = str(raw.get("price") or "$43.00")[:12]
        cleaned["tally"] = str(raw.get("tally") or "1,000 COMPANIES • 1,000 FOUNDERS • FUNDING • LINKEDIN")[:80]
        cleaned["sub"] = str(raw.get("sub") or "You paid for the work actually performed.")[:60]
    elif layout == "editorial_statement":
        cleaned["line1"] = str(raw.get("line1") or "YOU DON'T NEED ANOTHER AI TOOL.")[:60]
        cleaned["line2"] = str(raw.get("line2") or "YOU NEED THE WORK DONE.")[:60]
        acc = raw.get("accent") or "WORK DONE."
        cleaned["accent"] = str(acc)[:28] if acc else "WORK DONE."
        if cleaned["style"] not in ("talo","olive","paper"):
            cleaned["style"] = "talo"
    elif layout == "clep_chaos":
        cleaned["flashes"] = [str(x)[:40] for x in (raw.get("flashes") or [])[:4]] if isinstance(raw.get("flashes"), list) else []
        if not cleaned["flashes"]:
            cleaned["flashes"] = ["Messy desk","Blurry PDF","Red errors"]
        if cleaned["style"] not in ("clep","clep_lime","clep_dark"):
            cleaned["style"] = "clep"
    elif layout == "clep_false":
        cleaned["wrong"] = str(raw.get("wrong") or "$12,340.00")[:20]
        cleaned["correct"] = str(raw.get("correct") or "$23,450.00")[:20]
        if cleaned["style"] not in ("clep","clep_dark"):
            cleaned["style"] = "clep"
    elif layout == "clep_enter":
        cleaned["tagline"] = str(raw.get("tagline") or "just... works.")[:40]
        if cleaned["style"] not in ("clep","clep_lime"):
            cleaned["style"] = "clep_lime"
    elif layout == "clep_dropzone":
        cleaned["url"] = str(raw.get("url") or "app.clep.io — live conversion")[:40]
        if cleaned["style"] not in ("clep",):
            cleaned["style"] = "clep"
    elif layout == "clep_icons":
        items = raw.get("items") or []
        if isinstance(items, list) and items:
            cleaned["items"] = [str(x)[:28] for x in items[:3]]
        else:
            cleaned["items"] = ["No templates","No hidden fees","No re-checking"]
        if cleaned["style"] not in ("clep",):
            cleaned["style"] = "clep"
    elif layout == "clep_trust":
        cleaned["row"] = str(raw.get("row") or "check row 14")[:40]
        cleaned["tooltip"] = str(raw.get("tooltip") or "This doesn't match your statement total — check row 14.")[:60]
        if cleaned["style"] not in ("clep",):
            cleaned["style"] = "clep"
    elif layout == "clep_range":
        cleaned["docs"] = [str(x)[:20] for x in (raw.get("docs") or ["Bank statement","Invoice","Receipt"])[:3]]
        if cleaned["style"] not in ("clep",):
            cleaned["style"] = "clep"
    elif layout == "clep_lockup":
        cleaned["tagline"] = str(raw.get("tagline") or "Drop it. Done.")[:30]
        cleaned["url"] = str(raw.get("url") or "clep.com")[:20]
        if cleaned["style"] not in ("clep","clep_lime"):
            cleaned["style"] = "clep"
    elif layout == "search_typing":
        cleaned["query"] = str(raw.get("query") or raw.get("text") or beat.get("narration_snippet","")).strip()[:120]
    elif layout == "badge_cards":
        cleaned["badge"] = str(raw.get("badge") or "Searching live news • • •")[:40]
        cards_in = raw.get("cards") or []
        cards=[]
        for c in (cards_in[:3] if isinstance(cards_in,list) else []):
            if not isinstance(c, dict): continue
            cards.append({"source": str(c.get("source","Source"))[:28],
                          "title": str(c.get("title") or c.get("text",""))[:140],
                          "time": str(c.get("time") or c.get("time_ago") or "")[:20]})
        if not cards:
            cards= [{"source":"GatesNotes","title":"The turbulent AI era is here. The choices we make now are critical.","time":"29 minutes ago"}]
        cleaned["cards"]=cards
    elif layout == "stat_blue":
        cleaned["stat"] = str(raw.get("stat") or raw.get("value") or "$0")[:10]
        cleaned["check"] = bool(raw.get("check", True))
        lines = raw.get("lines") or raw.get("text") or ["No subscriptions.","No quotas."]
        if isinstance(lines, str): lines=[lines]
        cleaned["lines"]=[str(l)[:28] for l in lines[:3]]
    elif layout == "grid_cards":
        cleaned["count"] = min(max(int(raw.get("count") or raw.get("cards") or 4),1),6)
        cleaned["pill"] = str(raw.get("pill") or raw.get("badge") or "1 search · 4 pages · $0.00")[:44]
    elif layout == "bars":
        cleaned["count"] = min(max(int(raw.get("count") or 4),1),6)
    elif layout == "diagram":
        # diagram inside kinetic: delegate to diagram normalizer
        d_raw = raw.get("diagram") or {}
        # if no inner diagram but raw has nodes etc directly, treat raw as diagram spec
        if not d_raw and raw.get("nodes"):
            d_raw = raw
        cleaned["diagram"] = _normalize_diagram(d_raw, index)
        # also keep style at kinetic level
    elif layout == "checklist":
        cleaned["title"] = str(raw.get("title") or raw.get("header") or "TALO IS WORKING")[:40]
        items_in = raw.get("items") or raw.get("steps") or []
        items=[]
        for it in (items_in[:8] if isinstance(items_in, list) else []):
            if isinstance(it, dict):
                items.append({"label": str(it.get("label") or it.get("text") or "")[:36], "status": str(it.get("status") or "pending")[:10]})
            elif isinstance(it, str):
                items.append({"label": it[:36], "status": "pending"})
        if not items:
            items=[{"label":"Finding companies","status":"done"},{"label":"Verifying LinkedIn profiles","status":"active"},{"label":"Enriching company data","status":"pending"}]
        cleaned["items"]=items
    elif layout == "task_box":
        cleaned["query"] = str(raw.get("query") or raw.get("text") or beat.get("narration_snippet","")).strip()[:140]
        cleaned["badge"] = str(raw.get("badge") or "TASK ACCEPTED")[:24]
        cleaned["estimate"] = str(raw.get("estimate") or "ESTIMATE: 6–8 HOURS")[:28]
        cleaned["maximum"] = str(raw.get("maximum") or "MAXIMUM: $80")[:24]
    elif layout == "ticker_grid":
        cleaned["count"] = min(max(int(raw.get("count") or 4),1),6)
        cleaned["pill"] = str(raw.get("pill") or "500 / 500")[:44]
        # ticker number sequence
        if raw.get("ticker"):
            cleaned["ticker"] = str(raw.get("ticker"))[:20]
    elif layout == "talo_hero":
        # Two-line editorial lockup: line1 italic serif, line2 bold sans with periwinkle accent
        # Accept either line1/line2 or single text with newline
        txt = str(raw.get("text") or raw.get("line2") or beat.get("narration_snippet","")).strip()
        if "\n" in txt and not raw.get("line1"):
            parts = txt.split("\n")
            cleaned["line1"] = parts[0].strip()[:60]
            cleaned["line2"] = parts[1].strip()[:60] if len(parts) > 1 else txt[:60]
        else:
            cleaned["line1"] = str(raw.get("line1") or "Have work to do?")[:60]
            cleaned["line2"] = str(raw.get("line2") or txt or "Hire an AI Freelancer.")[:60]
        acc = raw.get("accent") or "Freelancer."
        if isinstance(acc, list):
            cleaned["accent"] = [str(a)[:28] for a in acc if str(a).strip()][:2]
            # flatten to first for simplicity
            cleaned["accent"] = cleaned["accent"][0] if cleaned["accent"] else "Freelancer."
        else:
            cleaned["accent"] = str(acc)[:28] if acc else "Freelancer."
        # style should be talo for correct palette, but allow override
        if cleaned["style"] not in ("talo", "light"):
            cleaned["style"] = "talo"
    elif layout == "talo_logo":
        cleaned["text"] = str(raw.get("text") or raw.get("logo") or "talo").strip()[:20].lower()
        tag = raw.get("tagline") or raw.get("sub")
        if tag:
            cleaned["tagline"] = str(tag)[:60]
        # force black bg handling done in renderer; keep style talo but bg overridden
        cleaned["style"] = "talo"
    return cleaned

def _validate_and_fix(beat: dict, index: int) -> dict:
    kind = beat.get("visual_kind", "kinetic")
    if kind not in VALID_VISUAL_KINDS:
        print(f"[planner] warning: beat {index} invalid visual_kind {kind!r} -> kinetic")
        kind = beat["visual_kind"] = "kinetic"
    # alias clean -> kinetic
    if kind == "clean":
        kind = beat["visual_kind"] = "kinetic"
        if beat.get("clean") and not beat.get("kinetic"):
            beat["kinetic"] = beat.pop("clean")
        elif beat.get("mgfx") and not beat.get("kinetic"):
            beat["kinetic"] = beat.pop("mgfx")

    if kind == "kinetic":
        try:
            beat["kinetic"] = _normalize_kinetic(beat, index)
        except Exception as e:
            print(f"[planner] warning: kinetic normalize failed beat {index}: {e} -> headline fallback")
            beat["kinetic"] = {"style":"light","layout":"headline","text": (beat.get("on_screen_text") or beat.get("narration_snippet","") or "HELLO")[:120] }
        # kinetic beats don't need on_screen_text (it's inside kinetic)
        if not beat.get("on_screen_text"):
            beat["on_screen_text"] = None
        # image_query optional for kinetic beats
        if not beat.get("image_query"):
            beat["image_query"] = None
        if not beat.get("video_query"):
            beat["video_query"] = None
    elif kind == "diagram":
        try:
            beat["diagram"] = _normalize_diagram(beat.get("diagram") or {}, index)
        except ValueError as e:
            print(f"[planner] warning: {e} -> kinetic fallback beat {index}")
            kind = beat["visual_kind"] = "kinetic"
            beat["kinetic"] = {"style":"light","layout":"headline","text": (beat.get("narration_snippet") or "")[:120]}
            beat.pop("diagram",None)
        if kind == "diagram":
            beat["on_screen_text"]=None
    elif kind in ("image","video_clip"):
        if not beat.get("image_query"):
            if kind=="video_clip":
                beat["image_query"]= beat.get("video_query") or "clean product screenshot minimal"
            else:
                raise ValueError(f"Beat {index} missing image_query")
    # common
    role = beat.get("role","body")
    beat["role"]= role if role in VALID_ROLES else "body"
    beat["caption_mode"]= beat.get("caption_mode") if beat.get("caption_mode") in VALID_CAPTION_MODES else "off"
    if not beat.get("narration_snippet","").strip():
        raise ValueError(f"Beat {index} empty narration_snippet")
    if beat.get("text_style") not in VALID_TEXT_STYLES:
        beat["text_style"]="headline"
    if beat.get("motion") not in VALID_MOTIONS:
        beat["motion"]="zoom_in"
    _normalize_transition(beat, index)
    return beat

def assign_transitions(beats: list[dict]) -> list[dict]:
    _ROT=[("fade",0.25),("smoothleft",0.30),("fadeblack",0.32),("cut",0.07),("circleopen",0.34),("dissolve",0.35),("hblur",0.28)]
    rot=0
    for i in range(len(beats)-1):
        if isinstance(beats[i].get("transition"),dict) and beats[i]["transition"].get("type") in VALID_TRANSITIONS:
            rot+=1; continue
        dur=beats[i]["end"]-beats[i]["start"]
        if dur<=2.2: beats[i]["transition"]={"type":"cut","duration":0.07}
        else:
            t,d=_ROT[rot%len(_ROT)]; rot+=1; beats[i]["transition"]={"type":t,"duration":float(d)}
    return beats

def _build_beats(raw_beats: list[dict], script: str="") -> list[dict]:
    raw_beats=[_validate_and_fix(b,i) for i,b in enumerate(raw_beats)]
    raw_beats[0]["role"]="hook"
    raw_beats[-1]["role"]="payoff"
    # ensure hook has visible text
    if raw_beats[0].get("visual_kind")=="kinetic":
        k=raw_beats[0].get("kinetic",{})
        if k.get("layout")=="headline" and not k.get("text"):
            raw_beats[0]["kinetic"]["text"]=" ".join(raw_beats[0]["narration_snippet"].split()[:4]).upper()
    reconstructed=" ".join(b["narration_snippet"].strip() for b in raw_beats)
    norm=lambda s: re.sub(r"\s+"," ",s).strip().lower()
    if norm(reconstructed)!=norm(script):
        print("[planner] warning: narration reconstruction mismatch — review beats.json")
    beats=[]
    cursor=0.0
    for i,b in enumerate(raw_beats):
        words=_word_count(b["narration_snippet"])
        word_based=max(words/config.AVG_WPM*60.0,1.0)
        try: hint=float(b.get("duration_hint") or 0)
        except: hint=0
        pace=min(max(hint/word_based, config.PACE_MIN), config.PACE_MAX) if hint>0 else 1.0
        duration=word_based*pace
        beat={"id":i,"start":round(cursor,3),"end":round(cursor+duration,3),
              "narration_snippet":b["narration_snippet"].strip(),
              "role":b["role"],"visual_kind":b.get("visual_kind","kinetic"),
              "image_query": (b.get("image_query").strip() if b.get("image_query") else None),
              "video_query": (b.get("video_query").strip() if b.get("video_query") else None),
              "on_screen_text": (b.get("on_screen_text") or None),
              "text_style":b["text_style"],"motion":b["motion"],
              "transition":b["transition"],"caption_mode":b["caption_mode"],"pace":round(pace,3)}
        if b.get("kinetic"): beat["kinetic"]=b["kinetic"]
        if b.get("diagram"): beat["diagram"]=b["diagram"]
        beats.append(beat); cursor=beat["end"]
    print(f"[planner:motion] {len(beats)} beats, {cursor:.1f}s total (target {config.TARGET_DURATION_SECONDS:.0f}s) roles:{[(b['id'],b['role'],b['visual_kind'], b.get('kinetic',{}).get('layout')) for b in beats]}")
    return beats

def plan(script: str) -> list[dict]:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    import time
    client=OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)
    max_tokens=16000; raw_text=""
    for attempt in range(1,5):
        resp=client.chat.completions.create(model=config.PLANNER_MODEL, max_tokens=max_tokens,
            messages=[{"role":"system","content":_system_prompt()},{"role":"user","content":f"Script:\n\n{script}"}])
        raw_text=(resp.choices[0].message.content or "").strip()
        if raw_text: break
        if attempt<4:
            time.sleep(2**attempt)
    try:
        raw_beats=_extract_json(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"planner did not return valid JSON: {e}\n\nRaw:\n{raw_text}")
    if not isinstance(raw_beats,list) or not raw_beats:
        raise RuntimeError(f"expected non-empty JSON array, got {raw_beats!r}")
    return _build_beats(raw_beats, script)

def plan_from_file(script_path: Path, out_path: Path = config.BEATS_JSON_PATH) -> list[dict]:
    script=Path(script_path).read_text().strip()
    beats=plan(script)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path,"w") as f: json.dump(beats,f,indent=2)
    print(f"[planner] wrote {len(beats)} beats ({beats[-1]['end']:.1f}s) -> {out_path}")
    return beats

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description="Plan motion-only edit")
    p.add_argument("--script",required=True)
    p.add_argument("--out",default=str(config.BEATS_JSON_PATH))
    a=p.parse_args()
    plan_from_file(Path(a.script), Path(a.out))

