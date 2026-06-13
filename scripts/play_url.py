from __future__ import annotations

import argparse
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.playback import create_default_playback_core  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Playable http(s) URL or local media path for mpv.")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    core = create_default_playback_core()
    print(core.describe())
    if not core.available():
        return 2
    try:
        core.load(args.url)
        core.play()
        deadline = time.time() + max(1, args.seconds)
        while time.time() < deadline:
            position = core.position_sec()
            duration = core.duration_sec()
            print(f"position={position} duration={duration}")
            time.sleep(1)
        return 0
    finally:
        core.close()


if __name__ == "__main__":
    raise SystemExit(main())

