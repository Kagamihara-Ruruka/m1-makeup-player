from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.notion_mcp import NotionMcpClient, extract_tool_text  # noqa: E402
from m1_player.notion_parser import parse_course_page  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("page")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    client = NotionMcpClient(request_timeout_sec=args.timeout_sec)
    try:
        client.start()
        response = client.call_tool("fetch", {"id": args.page}, timeout=args.timeout_sec)
        parsed = parse_course_page(extract_tool_text(response), page_id=args.page)
        if args.json:
            print(json.dumps({
                "course": {
                    "title": parsed.course.title,
                    "date": parsed.course.course_date,
                    "url": parsed.course.page_url,
                },
                "videos": [
                    {
                        "stable_key": video.stable_key,
                        "index": video.segment_index,
                        "name": video.video_name,
                        "transcript_ref": video.transcript_ref,
                    }
                    for video in parsed.videos
                ],
            }, ensure_ascii=False, indent=2))
        else:
            print(f"{parsed.course.title} {parsed.course.course_date or ''}")
            for video in parsed.videos:
                print(f"{video.segment_index:02d} {video.video_name}")
        return 0
    except TimeoutError as exc:
        print(f"Notion page scan timeout: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports external scan failures.
        print(f"Notion page scan failed: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
