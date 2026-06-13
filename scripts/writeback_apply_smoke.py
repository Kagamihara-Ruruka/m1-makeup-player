from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.local_settings import load_local_settings  # noqa: E402
from m1_player.notion_api import NotionApiClient, first_data_source_id  # noqa: E402
from m1_player.notion_property_adapter import notion_properties_for_completion_event  # noqa: E402
from m1_player.sync_service import notion_token  # noqa: E402
from m1_player.writeback import WritebackEvent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a synthetic completion row in Notion and trash it unless --keep is set."
    )
    parser.add_argument("--apply", action="store_true", help="Actually call Notion. Without this, print dry-run payload.")
    parser.add_argument("--keep", action="store_true", help="Keep the smoke-test row visible instead of moving it to trash.")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = load_local_settings()
    token = notion_token()
    if not token:
        return emit({"status": "blocked", "reason": "notion_token missing"}, args.json, 2)
    if not settings.completion_database_id:
        return emit({"status": "blocked", "reason": "completion data source missing"}, args.json, 2)

    event = synthetic_completion_event()
    properties = notion_properties_for_completion_event(event)
    if not args.apply:
        return emit(
            {
                "status": "dry_run",
                "data_source_id": settings.completion_database_id,
                "properties": properties,
            },
            args.json,
            0,
        )

    client = NotionApiClient(token, timeout_sec=args.timeout_sec)
    data_source_id = first_data_source_id(client, settings.completion_database_id)
    page = client.create_page(data_source_id, properties)
    page_id = str(page["id"])
    trashed = False
    if not args.keep:
        client.set_page_in_trash(page_id, True)
        trashed = True
    return emit(
        {
            "status": "created",
            "data_source_id": data_source_id,
            "page_id": page_id,
            "moved_to_trash": trashed,
            "video_name": event.video_name,
        },
        args.json,
        0,
    )


def synthetic_completion_event() -> WritebackEvent:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return WritebackEvent(
        event_type="completed",
        stable_key="m1_writeback_apply_smoke",
        video_name="m_1 writeback apply smoke test",
        course_page_url="https://www.notion.so/m1-writeback-apply-smoke",
        course_date=None,
        segment_index=0,
        last_position_sec=1.0,
        duration_sec=1.0,
        progress_percent=100.0,
        status="已完成",
        completed_at=now,
        generated_at=now,
        video_block_ref="smoke-test",
        source_ref="smoke-test",
        subtitle_path=None,
    )


def emit(payload: dict[str, object], as_json: bool, exit_code: int) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code
    print(f"status={payload.get('status')}")
    for key in ("reason", "data_source_id", "page_id", "moved_to_trash", "video_name"):
        if key in payload:
            print(f"{key}={payload[key]}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
