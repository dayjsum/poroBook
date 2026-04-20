"""
Run from repo root (poroBook):

  python scripts/fetch_puuid.py "GameName" "TAG"

Delegates to backend/scripts/fetch_puuid.py (reads backend/.env).
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_target = _root / "backend" / "scripts" / "fetch_puuid.py"
if not _target.is_file():
    sys.exit(f"Expected backend script at {_target}")
runpy.run_path(str(_target), run_name="__main__")
