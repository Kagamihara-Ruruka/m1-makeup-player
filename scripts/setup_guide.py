from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.runtime_config import load_app_config  # noqa: E402
from m1_player.settings_status import collect_settings_status  # noqa: E402
from m1_player.setup_guide import build_setup_guide  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Print copy-paste setup commands for the m_1 Notion makeup player.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    config = load_app_config()
    guide = build_setup_guide(collect_settings_status(config))
    if args.json:
        print(json.dumps(guide.to_json(), ensure_ascii=False, indent=2))
    else:
        print(guide.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
