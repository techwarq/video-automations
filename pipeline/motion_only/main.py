"""Wrapper: run motion-only pipeline from inside pipeline/ directory.

Usage identical to pipeline_motion/main.py — just `python main.py ...`.
Delegates to ../../pipeline_motion/main.py with adjusted paths.
"""
import sys
from pathlib import Path
import subprocess

# Resolve sibling pipeline_motion
HERE = Path(__file__).resolve().parent
MOTION_ROOT = HERE.parent.parent / "pipeline_motion"
MAIN = MOTION_ROOT / "main.py"

if __name__ == "__main__":
    # Forward all args to the real main
    import os
    # Ensure we use the venv python
    py = HERE.parent / "venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    # Build command: python pipeline_motion/main.py <args>
    cmd = [str(py), str(MAIN)] + sys.argv[1:]
    # Preserve cwd so relative paths resolve as user typed them
    print(f"[wrapper:{HERE.name}] delegating to {MAIN} ...")
    raise SystemExit(subprocess.call(cmd))
