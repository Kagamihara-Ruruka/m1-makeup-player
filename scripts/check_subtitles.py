from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE, SUBTITLE_DIR  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.subtitle_readiness import audit_subtitle_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--subtitle-dir", default=str(SUBTITLE_DIR))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-candidates", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    rows = audit_subtitle_readiness(list(store.records.values()), args.subtitle_dir)

    if args.json:
        print(json.dumps([row.to_json() for row in rows], ensure_ascii=False, indent=2))
    else:
        for row in rows:
            suffix = f" cues={row.cue_count}" if row.status == "found" else ""
            print(
                f"{row.course_date or ''} "
                f"P{row.segment_index:02d} "
                f"{row.status} "
                f"{row.video_name}"
                f"{suffix}"
            )
            if args.show_candidates:
                for candidate in row.candidates:
                    print(f"  candidate: {candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
