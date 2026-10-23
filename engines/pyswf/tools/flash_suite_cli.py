"""Source checkout wrapper for the packaged flash-suite console entry point."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from py_swf.suite_cli import main


if __name__ == '__main__':
    raise SystemExit(main())
