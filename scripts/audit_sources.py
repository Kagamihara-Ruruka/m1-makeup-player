from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.source_readiness import (  # noqa: E402
    audit_source_readiness,
    source_readiness_passes,
    summarize_source_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--expose-block-id", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    rows = audit_source_readiness(list(store.records.values()))
    summary = summarize_source_readiness(rows)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "passes": source_readiness_passes(rows),
            "rows": [row.to_json(expose_block_id=args.expose_block_id) for row in rows],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"summary: {summary}")
        for row in rows:
            print(
                f"{row.course_date or ''} P{row.segment_index:02d} "
                f"{row.readiness} {row.video_name}"
            )
            print(f"  reason: {row.reason}")
            if row.has_permission_block:
                print(f"  block_id: {row.to_json(expose_block_id=args.expose_block_id)['block_id']}")

    if args.strict and not source_readiness_passes(rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
