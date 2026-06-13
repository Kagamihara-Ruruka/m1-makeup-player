from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    store = ProgressStore(args.cache)
    store.load()
    rows = []
    for record in sorted(store.records.values(), key=lambda item: (item.course_date or "", item.segment_index)):
        source = parse_video_source(record.source_ref)
        rows.append({
            "course_date": record.course_date,
            "segment_index": record.segment_index,
            "video_name": record.video_name,
            "source_kind": source.source_kind,
            "is_playable": source.is_playable,
            "requires_resolution": source.requires_resolution,
            "filename_hint": source.filename_hint,
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            state = "playable" if row["is_playable"] else "needs_resolver"
            print(f"{row['course_date'] or ''} P{row['segment_index']:02d} {state} {row['source_kind']} {row['video_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

