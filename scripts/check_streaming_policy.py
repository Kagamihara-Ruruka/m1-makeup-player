from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE, RESOLVED_URL_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.streaming_policy import (  # noqa: E402
    audit_resolved_url_cache,
    audit_streaming_sources,
    streaming_policy_passes,
    summarize_streaming_policy,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--url-cache", default=str(RESOLVED_URL_CACHE))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    source_rows = audit_streaming_sources(list(store.records.values()))
    cache_rows = audit_resolved_url_cache(ResolvedUrlCache(args.url_cache))
    summary = summarize_streaming_policy(source_rows, cache_rows)
    passes = streaming_policy_passes(source_rows, cache_rows)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "passes": passes,
            "sources": [row.to_json() for row in source_rows],
            "resolved_url_cache": [row.to_json() for row in cache_rows],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"summary: {summary}")
        for row in source_rows:
            print(
                f"{row.course_date or ''} P{row.segment_index:02d} "
                f"{row.policy_status} {row.video_name}"
            )
            print(f"  reason: {row.reason}")
        for row in cache_rows:
            print(f"cache {row.policy_status} {row.filename_hint or row.key[:12]}")
            print(f"  reason: {row.reason}")

    if args.strict and not passes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
