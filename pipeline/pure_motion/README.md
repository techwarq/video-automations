# Wrapper — see ../../pipeline_motion/README.md

This directory is a convenience alias so you can run motion-only from inside `pipeline/`:

```bash
# from here (pipeline/pure_motion)
python main.py --script ../motion_only/script_reference.txt --out output/demo.mp4 --skip-planner

# or via sibling
python ../../pipeline_motion/main.py --script ../../pipeline_motion/script_reference.txt --out ../../pipeline_motion/output/demo.mp4 --skip-planner
```

All real code lives in `../../pipeline_motion/` (motion_gfx, planner, compositor, config).
