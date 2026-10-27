"""Do not execute application code until the supervisor has established containment."""
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    if sys.stdin.buffer.read(1) != b"1":
        raise SystemExit(125)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    mode = sys.argv[1]
    sys.argv = sys.argv[2:]
    if mode == "hath":
        runpy.run_path(sys.argv[0], run_name="__main__")
    elif mode == "server":
        runpy.run_module("exh_rec.app", run_name="__main__")
    else:
        raise SystemExit(125)
