from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE, SUBTITLE_DIR  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.subtitle_manifest import build_subtitle_manifest, write_missing_markdown_placeholders  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--subtitle-dir", default=str(SUBTITLE_DIR))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-missing-md", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    records = list(store.records.values())
    rows = build_subtitle_manifest(records, args.subtitle_dir)
    write_result = None
    if args.write_missing_md:
        write_result = write_missing_markdown_placeholders(records, args.subtitle_dir, overwrite=args.overwrite)
        rows = build_subtitle_manifest(records, args.subtitle_dir)

    if args.json:
        payload: dict[str, object] = {
            "subtitle_dir": str(Path(args.subtitle_dir)),
            "records": len(records),
            "manifest": [row.to_json() for row in rows],
        }
        if write_result:
            payload["write_result"] = write_result.to_json()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"subtitle_dir={Path(args.subtitle_dir)}")
        print(f"records={len(records)}")
        if write_result:
            print(f"written={len(write_result.written)} skipped_existing={len(write_result.skipped_existing)}")
        for row in rows:
            print(f"{row.course_date or ''} P{row.segment_index:02d} {row.status} {row.video_name}")
            print(f"  md: {row.preferred_markdown_path}")
            if row.existing_path:
                print(f"  existing: {row.existing_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
