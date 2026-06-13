from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.progress_overview import collect_progress_overview  # noqa: E402
from m1_player.runtime_config import load_app_config  # noqa: E402
from m1_player.writeback import WritebackOutbox  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Show local makeup lesson progress overview.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    config = load_app_config()
    store = ProgressStore(config.progress_cache)
    store.load()
    outbox = WritebackOutbox(config.writeback_outbox)
    overview = collect_progress_overview(list(store.records.values()), queued_writebacks=outbox.count_events())
    if args.json:
        print(json.dumps(overview.to_json(), ensure_ascii=False, indent=2))
    else:
        print(overview.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
