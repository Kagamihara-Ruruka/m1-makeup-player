from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.writeback_schema import completion_record_properties  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stable_key", nargs="?")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    args = parser.parse_args()
    store = ProgressStore(args.cache)
    store.load()
    records = list(store.records.values())
    if args.stable_key:
        records = [record for record in records if record.stable_key == args.stable_key]
    payload = [
        {
            "stable_key": record.stable_key,
            "properties": completion_record_properties(record),
        }
        for record in records
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

