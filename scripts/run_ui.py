from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.app_qt import run_app  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run_app())
