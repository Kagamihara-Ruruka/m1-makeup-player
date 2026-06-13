from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.local_settings import LOCAL_SETTINGS_PATH  # noqa: E402
from m1_player.settings_actions import set_schedule_view_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schedule_view_url", help="Notion 課程安排 database view URL.")
    args = parser.parse_args()
    schedule_view_url = args.schedule_view_url.strip()
    if not schedule_view_url:
        print("No schedule view URL written.")
        return 1
    set_schedule_view_url(schedule_view_url)
    print(f"Schedule view URL saved to {LOCAL_SETTINGS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
