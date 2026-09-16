"""
For each beat, search Pinterest for image_query and download the candidate
images into cache/beat_{n}/. Adds a `candidate_images` field (list of local
file paths) to each beat dict.

Quality gates before a download counts as a candidate:
  - Hi-res rewrite: Pinterest thumbnail URLs embed their size segment
    (/236x/, /474x/, /736x/...). Rewriting it to /originals/ usually yields
    the full-resolution pin; on 404 we fall back to the URL as given.
  - Dedupe: near-identical pins (same image re-pinned at different crops) are
    dropped via an average-hash (16x16 grayscale, hamming distance), so the
    vision stage doesn't waste candidate slots or tokens on duplicates.
  - Minimum resolution: anything smaller than MIN_CANDIDATE_PX is rejected —
    Ken Burns zooms magnify softness, and sub-500px sources fall apart.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse
import requests
from PIL import Image

import config
from image_source import pinterest_search

CANDIDATES_PER_BEAT = 6
MIN_CANDIDATE_PX = 480          # reject candidates narrower/taller than this
_DUP_HAMMING_THRESHOLD = 5      # ≤5 differing bits of the 64-bit hash ≈ same image
_SIZE_SEGMENT_RE = re.compile(r"/(\d+x)/")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _extension_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lstrip(".").lower()
    return ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"


def _hi_res_url(url: str) -> str | None:
    """
    Pinterest serves most pins at /<size>/ segments; /originals/ is the
    full-res variant. Returns None if the URL has no size segment.
    """
    rewritten, n = _SIZE_SEGMENT_RE.subn("/originals/", url)
    return rewritten if n else None


def _download(url: str, dest: Path, try_hi_res: bool = True) -> bool:
    """Downloads `url`, trying the /originals/ variant first when possible."""
    candidates = []
    if try_hi_res:
        hi = _hi_res_url(url)
        if hi:
            candidates.append(hi)
    candidates.append(url)

    for attempt_url in candidates:
        try:
            resp = requests.get(attempt_url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return True
        except requests.RequestException as e:
            if attempt_url == candidates[-1]:
                print(f"[fetch_assets] failed to download {url}: {e}")
            # else: hi-res variant missing — fall through to the original URL
    return False


def _avg_hash(path: Path) -> str | None:
    """64-bit average hash as a 16-char hex string; None if unreadable/too small."""
    try:
        with Image.open(path) as img:
            img = img.convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return f"{int(bits, 2):016x}"
    except Exception:
        return None


def _hamming(hex_a: str, hex_b: str) -> int:
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def _is_too_small(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            w, h = img.size
        return w < MIN_CANDIDATE_PX or h < MIN_CANDIDATE_PX
    except Exception:
        return True


def fetch_for_beat(beat: dict, count: int = CANDIDATES_PER_BEAT) -> list[str]:
    beat_dir = config.CACHE_DIR / f"beat_{beat['id']}"
    beat_dir.mkdir(parents=True, exist_ok=True)

    urls = pinterest_search(beat["image_query"], count=count)
    if not urls:
        print(f"[fetch_assets] beat {beat['id']}: no Pinterest results for {beat['image_query']!r}")
        return []

    paths: list[str] = []
    seen_hashes: list[str] = []
    i = 0
    for url in urls:
        dest = beat_dir / f"candidate_{i}.{_extension_from_url(url)}"
        if not _download(url, dest):
            continue
        if _is_too_small(dest):
            print(f"[fetch_assets] beat {beat['id']}: rejected candidate (below {MIN_CANDIDATE_PX}px)")
            dest.unlink(missing_ok=True)
            continue
        h = _avg_hash(dest)
        if h and any(_hamming(h, prev) <= _DUP_HAMMING_THRESHOLD for prev in seen_hashes):
            print(f"[fetch_assets] beat {beat['id']}: dropped duplicate candidate")
            dest.unlink(missing_ok=True)
            continue
        if h:
            seen_hashes.append(h)
        paths.append(str(dest))
        i += 1
        if len(paths) >= count:
            break
    return paths


def add_candidates(beat: dict, new_paths: list[str]) -> int:
    """
    Merges refetch results into a beat's candidate_images, skipping files
    that are near-duplicates of what's already there (vision retries call
    this). Returns how many were actually added.
    """
    existing_hashes = []
    for p in beat.get("candidate_images", []):
        h = _avg_hash(Path(p))
        if h:
            existing_hashes.append(h)

    added = 0
    next_idx = len(beat.get("candidate_images", []))
    beat_dir = config.CACHE_DIR / f"beat_{beat['id']}"
    for src in new_paths:
        src = Path(src)
        h = _avg_hash(src)
        if h and any(_hamming(h, prev) <= _DUP_HAMMING_THRESHOLD for prev in existing_hashes):
            continue
        dest = beat_dir / f"candidate_{next_idx}.{src.suffix.lstrip('.') or 'jpg'}"
        if src != dest:
            src.replace(dest)
        if h:
            existing_hashes.append(h)
        beat.setdefault("candidate_images", []).append(str(dest))
        next_idx += 1
        added += 1
    return added


def _resolve_video_clip(beat: dict) -> str | None:
    """
    Finds the best match for beat['video_query'] among files in
    config.CLIPS_DIR by keyword overlap with the filename stem. Returns a
    path or None (caller falls back to the image pipeline).
    """
    clips_dir = config.CLIPS_DIR
    if not clips_dir.exists():
        return None
    files = [p for p in clips_dir.iterdir()
             if p.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v")
             and not p.name.startswith(".")]
    query_tokens = set(re.findall(r"[a-z0-9]+",
                                  (beat.get("video_query") or beat.get("image_query") or "").lower()))
    scored = []
    for p in files:
        stem_tokens = set(re.findall(r"[a-z0-9]+", p.stem.lower()))
        overlap = len(query_tokens & stem_tokens)
        if overlap:
            scored.append((overlap, p))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1].stat().st_size))
    return str(scored[0][1])


def fetch_all(beats: list[dict], count: int = CANDIDATES_PER_BEAT) -> list[dict]:
    for beat in beats:
        if beat.get("visual_kind") == "diagram":
            beat["candidate_images"] = []
            print(f"[fetch_assets] beat {beat['id']}: diagram beat — no image search.")
            continue
        if beat.get("visual_kind") == "video_clip":
            clip = _resolve_video_clip(beat)
            if clip:
                beat["resolved_clip_path"] = clip
                beat["candidate_images"] = []
                print(f"[fetch_assets] beat {beat['id']}: video clip resolved -> {clip}")
                continue
            print(f"[fetch_assets] beat {beat['id']}: no clip matches "
                  f"{beat.get('video_query')!r} in {config.CLIPS_DIR} — falling back to image search.")
            beat["visual_kind"] = "image"
        beat["candidate_images"] = fetch_for_beat(beat, count=count)
        print(f"[fetch_assets] beat {beat['id']} ({beat['image_query']!r}): "
              f"{len(beat['candidate_images'])} candidates kept")
    return beats


def fetch_from_file(beats_json_path: Path = config.BEATS_JSON_PATH, count: int = CANDIDATES_PER_BEAT) -> list[dict]:
    with open(beats_json_path) as f:
        beats = json.load(f)
    beats = fetch_all(beats, count=count)
    with open(beats_json_path, "w") as f:
        json.dump(beats, f, indent=2)
    return beats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Pinterest candidate images for each beat.")
    parser.add_argument("--beats", default=str(config.BEATS_JSON_PATH))
    parser.add_argument("--count", type=int, default=CANDIDATES_PER_BEAT)
    args = parser.parse_args()

    fetch_from_file(Path(args.beats))
