from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.local_settings import LOCAL_SETTINGS_PATH  # noqa: E402
from m1_player.settings_actions import set_completion_data_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_source_id", help="Notion data source id or Notion URL for 補課紀錄.")
    args = parser.parse_args()
    set_completion_data_source(args.data_source_id)
    print(f"Completion data source saved to {LOCAL_SETTINGS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
