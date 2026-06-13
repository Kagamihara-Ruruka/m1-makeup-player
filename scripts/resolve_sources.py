from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.attachment_resolver import NotionAttachmentResolver  # noqa: E402
from m1_player.config import PROGRESS_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.runtime_config import load_app_config  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--no-url-cache", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-reason", action="store_true")
    args = parser.parse_args()
    store = ProgressStore(args.cache)
    store.load()
    app_config = load_app_config()
    url_cache = None if args.no_url_cache else ResolvedUrlCache(app_config.resolved_url_cache)
    resolver = NotionAttachmentResolver(cache=url_cache)
    rows = []
    for record in sorted(store.records.values(), key=lambda item: (item.course_date or "", item.segment_index)):
        source = parse_video_source(record.source_ref)
        resolution = resolver.resolve(source)
        rows.append({
            "course_date": record.course_date,
            "segment_index": record.segment_index,
            "video_name": record.video_name,
            "source_kind": source.source_kind,
            "resolution_status": resolution.status,
            "resolved": resolution.resolved,
            "reason": resolution.reason,
            "expires_at": resolution.expires_at,
            "cache_hit": resolution.cache_hit,
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(
                f"{row['course_date'] or ''} P{row['segment_index']:02d} "
                f"{row['resolution_status']} {row['video_name']}"
            )
            if args.show_reason:
                print(f"  reason: {row['reason']}")
                print(f"  cache_hit: {row['cache_hit']} expires_at: {row['expires_at'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
