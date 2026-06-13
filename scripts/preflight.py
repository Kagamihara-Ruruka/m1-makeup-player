from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.preflight import run_preflight  # noqa: E402
from m1_player.runtime_config import load_app_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    items = run_preflight(load_app_config())
    if args.json:
        print(json.dumps([item.__dict__ for item in items], ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{item.status.upper()} {item.key}: {item.message}")
    if args.strict and any(item.error for item in items):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
