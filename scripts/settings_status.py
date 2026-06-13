from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.runtime_config import load_app_config  # noqa: E402
from m1_player.settings_status import collect_settings_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = load_app_config()
    status = collect_settings_status(config)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"settings_path: {status['settings_path']}")
        print(f"notion_token: {status['notion_token']['status']} ({status['notion_token']['source']})")
        print(f"sync_backend: {status['sync_backend']}")
        last_sync = status["last_sync"]
        if last_sync["last_synced_at"]:
            print(
                "last_sync: "
                f"{last_sync['last_sync_backend']} {last_sync['last_synced_at']} "
                f"pages={last_sync['last_course_page_count']} "
                f"videos={last_sync['last_video_segment_count']}"
            )
        else:
            print("last_sync: missing")
        print(f"schedule_view: {status['schedule_view']['source']}")
        print(f"attachment_resolution: {status['attachment_resolution']}")
        print(f"completion_data_source: {status['completion_data_source']['status']}")
        print(f"writeback_mode: {status['writeback_mode']}")
        print(f"cache_records: {status['cache_records']}")
        print(f"resolved_url_cache: {status['resolved_url_cache']}")
        print(f"queued_writeback_events: {status['queued_writeback_events']}")
        for item in status["next_actions"]:
            print(f"next: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
