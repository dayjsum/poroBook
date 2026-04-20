"""Wrapper: run from repo root — see backend/scripts/sample_ladder_puuids.py."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_target = Path(__file__).resolve().parent.parent / "backend" / "scripts" / "sample_ladder_puuids.py"
if not _target.is_file():
    sys.exit(f"Missing {_target}")
runpy.run_path(str(_target), run_name="__main__")
