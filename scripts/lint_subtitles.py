from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE, SUBTITLE_DIR  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.subtitle_manifest import build_subtitle_manifest  # noqa: E402
from m1_player.subtitle_lint import lint_subtitle_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--subtitle-dir", default=str(SUBTITLE_DIR))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    manifest = build_subtitle_manifest(list(store.records.values()), args.subtitle_dir)
    results = []
    for row in manifest:
        if not row.existing_path:
            continue
        results.append(lint_subtitle_file(row.existing_path))

    payload = {
        "subtitle_dir": str(Path(args.subtitle_dir)),
        "checked_files": len(results),
        "passing_files": sum(1 for result in results if result.passes),
        "failing_files": sum(1 for result in results if not result.passes),
        "results": [result.to_json() for result in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"subtitle_dir={payload['subtitle_dir']}")
        print(
            "checked="
            f"{payload['checked_files']} "
            f"passing={payload['passing_files']} "
            f"failing={payload['failing_files']}"
        )
        for result in results:
            print(f"{result.status} cues={result.cue_count} {result.path}")
            for issue in result.issues:
                cue = "" if issue.cue_index is None else f" cue={issue.cue_index}"
                print(f"  {issue.severity} {issue.code}{cue}: {issue.message}")
    return 1 if args.strict and any(not result.passes for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
