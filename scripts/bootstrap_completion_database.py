from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.completion_database import (  # noqa: E402
    DEFAULT_COMPLETION_DATABASE_TITLE,
    build_completion_database_payload,
    extract_first_created_data_source_id,
    parent_from_schedule_database,
)
from m1_player.local_settings import LOCAL_SETTINGS_PATH  # noqa: E402
from m1_player.notion_api import NotionApiClient, first_data_source_id  # noqa: E402
from m1_player.runtime_config import schedule_view_url_with_source  # noqa: E402
from m1_player.settings_actions import set_completion_data_source  # noqa: E402
from m1_player.sync_service import notion_token  # noqa: E402
from m1_player.writeback_schema_check import check_completion_data_source_schema, result_to_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or preview the Notion completion writeback database for the m_1 player."
    )
    parent_group = parser.add_mutually_exclusive_group(required=True)
    parent_group.add_argument("--parent-page", help="Notion parent page URL or id.")
    parent_group.add_argument(
        "--parent-from-schedule",
        action="store_true",
        help="Create the completion database under the same parent as the configured schedule database.",
    )
    parent_group.add_argument("--workspace", action="store_true", help="Create as a private workspace database.")
    parser.add_argument("--title", default=DEFAULT_COMPLETION_DATABASE_TITLE)
    parser.add_argument("--full-page", action="store_true", help="Create as a full page database instead of inline.")
    parser.add_argument("--apply", action="store_true", help="Actually create the database in Notion.")
    parser.add_argument("--save", action="store_true", help="Save the created data source id to local settings.")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    token = notion_token()
    if args.apply and not token:
        return emit({"status": "blocked", "reason": "notion_token missing"}, args.json, 2)

    client = NotionApiClient(token or "dry-run-token", timeout_sec=args.timeout_sec)
    try:
        parent = resolve_parent(args, client)
    except Exception as exc:  # noqa: BLE001 - CLI reports external setup boundary.
        return emit({"status": "blocked", "reason": str(exc)}, args.json, 2)
    parent_source = str(parent.pop("source", ""))
    payload = {
        **build_completion_database_payload("workspace" if parent.get("type") == "workspace" else str(parent["page_id"])),
        "parent": parent,
        "title": [{"type": "text", "text": {"content": args.title}}],
        "is_inline": not args.full_page,
    }

    if not args.apply:
        return emit({"status": "dry_run", "parent_source": parent_source, "payload": payload}, args.json, 0)

    database = client.create_database(payload)
    data_source_id = first_data_source_id(client, extract_first_created_data_source_id(database))
    schema = client.retrieve_data_source(data_source_id)
    schema_check = check_completion_data_source_schema(schema)
    if args.save:
        set_completion_data_source(data_source_id)
    result = {
        "status": "created",
        "database_id": str(database.get("id")),
        "data_source_id": data_source_id,
        "parent_source": parent_source,
        "schema_check": result_to_payload(schema_check),
        "saved_to": str(LOCAL_SETTINGS_PATH) if args.save else None,
    }
    return emit(result, args.json, 0 if schema_check.ok else 1)


def resolve_parent(args: argparse.Namespace, client: NotionApiClient) -> dict[str, object]:
    if args.workspace:
        return {"type": "workspace", "workspace": True}
    if args.parent_page:
        parent_payload = build_completion_database_payload(args.parent_page)["parent"]
        return dict(parent_payload)
    schedule_url, schedule_source = schedule_view_url_with_source()
    if not schedule_url:
        raise RuntimeError("schedule view URL missing; provide --parent-page instead")
    parent = parent_from_schedule_database(client, schedule_url)
    parent["source"] = schedule_source
    return parent


def emit(payload: dict[str, object], as_json: bool, exit_code: int) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code
    print(f"status={payload.get('status')}")
    if payload.get("reason"):
        print(f"reason={payload['reason']}")
    if payload.get("payload"):
        print(json.dumps(payload["payload"], ensure_ascii=False, indent=2))
    if payload.get("parent_source"):
        print(f"parent_source={payload['parent_source']}")
    if payload.get("database_id"):
        print(f"database_id={payload['database_id']}")
    if payload.get("data_source_id"):
        print(f"data_source_id={payload['data_source_id']}")
    if payload.get("schema_check"):
        print(f"schema_check={payload['schema_check']['status']}")
    if payload.get("saved_to"):
        print(f"saved_to={payload['saved_to']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
