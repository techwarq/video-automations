"""
Word-synced kinetic captions: turns each beat's narration_snippet into timed
caption groups (default 3 words each) that reveal in step with the spoken
line — the "exactly what the speaker says" short-form caption layer.

Timing is derived by distributing the beat's [start, end] window across its
words proportionally to word length (longer words take longer to say). That
matches how planner.py derives beat timing from word counts, so caption and
beat clocks stay consistent. If a forced-alignment stage is added later it
can inject `word_timings` ([{"word","start","end"}, ...]) onto a beat and
build_captions() will use those exact times instead of the estimate.

Output per group: {"text", "start", "end", "accent"} where accent marks
groups containing numbers/stats — compositor renders those in the accent
color so figures punch like kinetic typography.
"""

import config


def _word_weight(word: str) -> float:
    """Speaking-cost proxy: character length, floored so tiny words still register."""
    return max(len(word.strip(",.!?;:—-\"'")), 1) + 1.0


def _chunk_words(words: list[str], per_group: int) -> list[list[str]]:
    return [words[i:i + per_group] for i in range(0, len(words), per_group)]


def _is_accent(text: str) -> bool:
    """Groups containing digits (stats, figures) get the accent color."""
    return any(ch.isdigit() for ch in text)


def build_captions(beat: dict, per_group: int | None = None) -> list[dict]:
    """
    Returns timed caption groups covering [beat.start, beat.end].
    Honors beat["word_timings"] if present (exact alignment), else estimates
    from word lengths.
    """
    snippet = (beat.get("narration_snippet") or "").strip()
    if not snippet:
        return []
    per_group = per_group or config.CAPTION_WORDS_PER_GROUP
    start_total, end_total = float(beat["start"]), float(beat["end"])
    window = max(end_total - start_total, 0.2)

    timings = beat.get("word_timings")
    if timings:
        # Exact mode: trust provided word windows verbatim; groups inherit
        # first-word-start / last-word-end.
        spans = []
        for t in timings:
            w = str(t.get("word", "")).strip()
            if not w:
                continue
            spans.append((w, float(t["start"]), float(t["end"])))
    else:
        # Estimate mode: proportional-to-length distribution over the window.
        words = snippet.split()
        weights = [_word_weight(w) for w in words]
        total_w = sum(weights)
        cursor = start_total
        spans = []
        for w, wt in zip(words, weights):
            dur = window * wt / total_w
            spans.append((w, cursor, cursor + dur))
            cursor += dur

    groups = []
    for chunk in _chunk_words(spans, per_group):
        text = " ".join(w for w, _, _ in chunk)
        if config.CAPTIONS_UPPERCASE:
            text = text.upper()
        groups.append({
            "text": text,
            "start": round(chunk[0][1], 3),
            "end": round(chunk[-1][2], 3),
            "accent": _is_accent(chunk[0][0]) or any(
                ch.isdigit() for w, _, _ in chunk for ch in w),
        })

    # Clamp the last group's end to the beat end (rounding drift guard) and
    # guarantee no zero-length groups.
    for g in groups:
        g["end"] = min(g["end"], end_total)
        g["start"] = min(g["start"], g["end"] - 0.05)
    return groups


def fit_caption_fontsize(text: str, canvas_w: int) -> tuple[str, int]:
    """
    Shrink fontsize until the single-line caption fits CAPTION_MAX_WIDTH_FRAC
    of the canvas. Captions are ≤4 short words, so wrapping is almost never
    needed; if even the floor size doesn't fit, fall back to two balanced
    lines at the floor size (drawtext honors an embedded newline).
    """
    import compositor  # local import: avoids a circular import at module load

    font_path = config.CAPTION_FONT
    max_width = int(canvas_w * config.CAPTION_MAX_WIDTH_FRAC)
    fontsize = config.CAPTION_FONTSIZE
    while fontsize > config.CAPTION_MIN_FONTSIZE:
        if compositor._text_width(text, font_path, fontsize) <= max_width:
            return text, fontsize
        fontsize -= 2

    fontsize = config.CAPTION_MIN_FONTSIZE
    if compositor._text_width(text, font_path, fontsize) <= max_width:
        return text, fontsize
    words = text.split()
    mid = len(words) // 2
    wrapped = f"{' '.join(words[:mid])}\n{' '.join(words[mid:])}"
    return wrapped, fontsize
