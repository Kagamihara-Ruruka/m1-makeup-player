from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import AppConfig  # noqa: E402
from m1_player.runtime_config import load_app_config  # noqa: E402
from m1_player.sync_service import NotionScheduleSync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    default_config = load_app_config()
    parser.add_argument("--view-url", default=default_config.schedule_view_url)
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--cache", default=str(ROOT / "state" / "progress_cache.json"))
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    config = AppConfig(
        schedule_view_url=args.view_url,
        progress_cache=Path(args.cache),
        page_size=args.page_size,
        max_pages=args.max_pages,
        notion_request_timeout_sec=args.timeout_sec,
    )
    try:
        result = NotionScheduleSync(config).sync()
    except TimeoutError as exc:
        print(f"Notion sync timeout: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports external sync failures.
        print(f"Notion sync failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "sync_backend": result.sync_backend,
            "course_pages": len(result.course_pages),
            "video_segments": len(result.segments),
            "cache": result.cache_path,
            "cache_metadata": result.cache_metadata.to_json(),
            "records": [
                record.to_json()
                for record in result.records
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"sync_backend={result.sync_backend}")
        print(f"course_pages={len(result.course_pages)}")
        print(f"video_segments={len(result.segments)}")
        print(f"cache={result.cache_path}")
        print(
            "last_sync="
            f"{result.cache_metadata.last_sync_backend or 'missing'} "
            f"{result.cache_metadata.last_synced_at or 'missing'} "
            f"pages={result.cache_metadata.last_course_page_count} "
            f"videos={result.cache_metadata.last_video_segment_count}"
        )
        for record in result.records:
            print(f"{record.course_date or ''} {record.segment_index:02d} {record.video_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
