from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.local_settings import load_local_settings  # noqa: E402
from m1_player.notion_api import NotionApiClient, first_data_source_id  # noqa: E402
from m1_player.sync_service import notion_token  # noqa: E402
from m1_player.writeback_schema_check import SchemaCheckResult, check_completion_data_source_schema, result_to_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-source-id")
    parser.add_argument("--fixture", help="Read a saved Notion data source JSON payload instead of calling Notion.")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    if args.fixture:
        payload = json.loads(Path(args.fixture).read_text(encoding="utf-8", errors="strict"))
        result = check_completion_data_source_schema(payload)
        return emit_result(result, args.json, args.strict)

    token = notion_token()
    data_source_id = args.data_source_id or load_local_settings().completion_database_id
    if not token:
        return emit_unavailable("notion_token missing", args.json, args.strict)
    if not data_source_id:
        return emit_unavailable("completion data source missing", args.json, args.strict)

    client = NotionApiClient(token, timeout_sec=args.timeout_sec)
    resolved_data_source_id = first_data_source_id(client, data_source_id)
    payload = client.retrieve_data_source(resolved_data_source_id)
    result = check_completion_data_source_schema(payload)
    return emit_result(result, args.json, args.strict)


def emit_unavailable(message: str, as_json: bool, strict: bool) -> int:
    payload = {
        "status": "not_applicable",
        "message": message,
        "issues": [],
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"not_applicable: {message}")
    return 2 if strict else 0


def emit_result(result: SchemaCheckResult, as_json: bool, strict: bool) -> int:
    payload = result_to_payload(result)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"status={payload['status']} required={payload['required_count']} optional={payload['optional_count']}")
        for issue in payload["issues"]:
            print(
                f"{issue['severity']} {issue['property_name']} "
                f"expected={issue['expected']} actual={issue['actual']} "
                f"{issue['message']}"
            )
    return 2 if strict and payload["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
