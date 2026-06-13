from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.writeback_schema_check import (  # noqa: E402
    check_completion_data_source_schema,
    completion_data_source_markdown_table,
    expected_completion_data_source_fixture,
    result_to_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--fixture-only",
        action="store_true",
        help="Print only the direct Notion data source fixture JSON accepted by check_writeback_schema.py --fixture.",
    )
    parser.add_argument("--output", help="Write JSON output to a UTF-8 file instead of stdout.")
    args = parser.parse_args()
    if args.markdown and args.fixture_only:
        parser.error("--markdown and --fixture-only cannot be used together")
    if args.markdown and args.output:
        parser.error("--output is only supported for JSON output")
    if args.check and args.fixture_only:
        parser.error("--check and --fixture-only cannot be used together; run check_writeback_schema.py --fixture on the saved fixture")

    fixture = expected_completion_data_source_fixture()
    if args.markdown:
        print("# m_1 completion writeback data source template")
        print()
        print(completion_data_source_markdown_table())
        if args.check:
            result = check_completion_data_source_schema(fixture)
            print()
            print(f"schema_check={result.status}")
        return 0

    if args.fixture_only:
        payload: dict[str, object] = fixture
    else:
        payload = {"template": fixture}
    if args.check and not args.fixture_only:
        payload["schema_check"] = result_to_payload(check_completion_data_source_schema(fixture))
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
