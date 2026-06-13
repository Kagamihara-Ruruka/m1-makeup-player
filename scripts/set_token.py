from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.local_settings import LOCAL_SETTINGS_PATH  # noqa: E402
from m1_player.settings_actions import set_notion_token  # noqa: E402


def main() -> int:
    token = getpass.getpass("Notion token: ").strip()
    if not token:
        print("No token written.")
        return 1
    set_notion_token(token)
    print(f"Token saved to {LOCAL_SETTINGS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
