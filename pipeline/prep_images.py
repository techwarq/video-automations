"""
Crop/scale each beat's selected_image to exactly fill the top-zone card
aspect ratio without distortion. High-res sources get a plain center-crop
("cover" fit). Low-res sources get a blurred/darkened self-fill background
(common Vox/kinetic-explainer trick) behind a minimally-upscaled foreground,
instead of plain black bars. Adds `prepped_image_path` to each beat.

If the beat has a focus_point (from select_images.py's vision call), the
crop is centered on that point instead of the image's blind center — so the
labeled detail the narration is about never gets cropped out — and the
resulting point is re-expressed in card-fractional coordinates as
`focus_point_card` for compositor.py to aim Ken Burns at. For `label`-style
beats specifically, a pointer arrow is drawn from near the label's corner
straight at that point, baked into the image.
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

import config
from compositor import _card_dims

ARROW_ANCHOR_MARGIN = 60  # px from the bottom-left corner, matches roughly where label text sits


def _cover_crop(img: Image.Image, w: int, h: int, focus: dict | None) -> tuple[Image.Image, dict | None]:
    """Cover-fit crop, centered on `focus` (source-fractional) if given, else the image center.
    Returns (cropped_image, focus_in_card_fractional_coords_or_None)."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    if focus:
        fx_px, fy_px = focus["x"] * new_w, focus["y"] * new_h
        left, top = round(fx_px - w / 2), round(fy_px - h / 2)
    else:
        left, top = (new_w - w) // 2, (new_h - h) // 2
    left = max(0, min(left, new_w - w))
    top = max(0, min(top, new_h - h))
    cropped = resized.crop((left, top, left + w, top + h))

    card_focus = None
    if focus:
        card_focus = {
            "x": min(max((fx_px - left) / w, 0.0), 1.0),
            "y": min(max((fy_px - top) / h, 0.0), 1.0),
        }
    return cropped, card_focus


def _contain_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = min(w / src_w, h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _draw_pointer_arrow(img: Image.Image, start: tuple[float, float], end: tuple[float, float]) -> None:
    """Dark-outlined white arrow so it stays legible over any background."""
    draw = ImageDraw.Draw(img)
    x0, y0 = start
    x1, y1 = end
    for color, width in ((20, 20, 20), 9), ((255, 255, 255), 4):
        draw.line([x0, y0, x1, y1], fill=color, width=width)

    angle = math.atan2(y1 - y0, x1 - x0)
    head_len, head_angle = 24, math.radians(28)
    outer = [end,
             (x1 - head_len * math.cos(angle - head_angle), y1 - head_len * math.sin(angle - head_angle)),
             (x1 - head_len * math.cos(angle + head_angle), y1 - head_len * math.sin(angle + head_angle))]
    draw.polygon(outer, fill=(20, 20, 20))
    inner_len = head_len - 6
    inner = [end,
             (x1 - inner_len * math.cos(angle - head_angle), y1 - inner_len * math.sin(angle - head_angle)),
             (x1 - inner_len * math.cos(angle + head_angle), y1 - inner_len * math.sin(angle + head_angle))]
    draw.polygon(inner, fill=(255, 255, 255))

    r = 7
    draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=(255, 255, 255), outline=(20, 20, 20), width=2)


def prep_image(beat: dict, dest_path: Path, card_w: int, card_h: int) -> dict | None:
    """Preps beat['selected_image'] -> dest_path. Returns focus_point_card (or None)."""
    img = Image.open(beat["selected_image"])
    img = ImageOps.exif_transpose(img).convert("RGB")
    src_w, src_h = img.size
    focus = beat.get("focus_point")

    if src_w >= card_w and src_h >= card_h:
        # Enough resolution to cover the card outright.
        result, card_focus = _cover_crop(img, card_w, card_h, focus)
    else:
        # Source too small: blurred/darkened self-fill background behind a
        # minimally-upscaled (not stretched, not cropped) foreground.
        bg, _ = _cover_crop(img, card_w, card_h, focus)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=max(card_w, card_h) // 40))
        bg = ImageEnhance.Brightness(bg).enhance(0.5)

        fg = _contain_fit(img, card_w, card_h)
        result = bg.copy()
        paste_x, paste_y = (card_w - fg.width) // 2, (card_h - fg.height) // 2
        result.paste(fg, (paste_x, paste_y))

        card_focus = None
        if focus:
            # Foreground isn't cropped, just scaled+offset — map straight through.
            card_focus = {
                "x": min(max((paste_x + focus["x"] * fg.width) / card_w, 0.0), 1.0),
                "y": min(max((paste_y + focus["y"] * fg.height) / card_h, 0.0), 1.0),
            }

    if card_focus and beat.get("text_style") == "label":
        anchor = (ARROW_ANCHOR_MARGIN, card_h - ARROW_ANCHOR_MARGIN)
        target = (card_focus["x"] * card_w, card_focus["y"] * card_h)
        _draw_pointer_arrow(result, anchor, target)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(dest_path, quality=92)
    return card_focus


def prep_all(beats: list[dict]) -> list[dict]:
    card_w, card_h = _card_dims()
    for beat in beats:
        if beat.get("visual_kind") == "diagram":
            beat["prepped_image_path"] = None
            beat["focus_point_card"] = None
            print(f"[prep_images] beat {beat['id']}: diagram beat — skipped (motion_gfx renders it).")
            continue
        if beat.get("visual_kind") == "video_clip" and beat.get("resolved_clip_path"):
            beat["prepped_image_path"] = None
            beat["focus_point_card"] = None
            print(f"[prep_images] beat {beat['id']}: video clip beat — skipped (compositor normalizes it).")
            continue
        if not beat.get("selected_image"):
            print(f"[prep_images] beat {beat['id']}: no selected_image, skipping.")
            beat["prepped_image_path"] = None
            beat["focus_point_card"] = None
            continue
        dest = config.ASSETS_DIR / f"beat_{beat['id']}_prepped.jpg"
        try:
            card_focus = prep_image(beat, dest, card_w, card_h)
            beat["prepped_image_path"] = str(dest)
            beat["focus_point_card"] = card_focus
            print(f"[prep_images] beat {beat['id']}: prepped -> {dest} (focus_point_card={card_focus})")
        except Exception as e:
            print(f"[prep_images] beat {beat['id']}: failed to prep {beat['selected_image']}: {e}")
            beat["prepped_image_path"] = None
            beat["focus_point_card"] = None
    return beats


def prep_from_file(beats_json_path: Path = config.BEATS_JSON_PATH) -> list[dict]:
    with open(beats_json_path) as f:
        beats = json.load(f)
    beats = prep_all(beats)
    with open(beats_json_path, "w") as f:
        json.dump(beats, f, indent=2)
    return beats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Crop/pad each beat's selected image to the top-zone card size.")
    parser.add_argument("--beats", default=str(config.BEATS_JSON_PATH))
    args = parser.parse_args()

    prep_from_file(Path(args.beats))
