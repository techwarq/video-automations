# Allore video pipelines — unified CLI

One entry point, `allore.py`, over the two engines that already live here:

```
pipeline/          talking-head explainer (9:16, top card + bottom talking-head video)
pipeline_motion/   pure motion graphics / kinetic typography (16:9 or 9:16, fullscreen)
```

`allore.py` doesn't duplicate their code — it picks whichever engine's `main.py`
to load based on `--mode`, then feeds it a script. That script can come from
either a text file (`--script`) or a raw string typed inline (`--prompt`).

## Build

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...   # only needed unless you pass --skip-planner
```

`ffmpeg`/`ffprobe` must be on PATH (or point at them via `PIPELINE_FFMPEG_BIN` /
`PIPELINE_FFPROBE_BIN`).

## Run

**Talking-head explainer** (needs a talking-head video clip):

```bash
# from a script file
python allore.py --mode talking-head --script pipeline/script.txt \
    --video talking_head.mp4 --out pipeline/output/final.mp4

# from a prompt, no .txt file needed
python allore.py --mode talking-head --prompt "We give your agent search and fetch for free." \
    --video talking_head.mp4 --out pipeline/output/final.mp4
```

**Motion graphics / kinetic typography** (no talking-head footage needed):

```bash
# from a script file, silent, reusing a canned beats.json
python allore.py --mode motion --script pipeline_motion/script_reference.txt \
    --skip-planner --out pipeline_motion/output/demo.mp4

# from a prompt, with narration audio and a portrait render
python allore.py --mode motion --prompt "Search and fetch, 100% free. No subscriptions, no quotas." \
    --audio narration.mp3 --portrait --out pipeline_motion/output/reel.mp4
```

Run `python allore.py --help` for the full flag list — flags marked
`[talking-head]` or `[motion]` in the help text only apply to that mode and
are ignored (with a warning) in the other.
