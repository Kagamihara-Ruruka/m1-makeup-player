from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import WRITEBACK_OUTBOX  # noqa: E402
from m1_player.writeback import WritebackOutbox  # noqa: E402
from m1_player.writeback_sink import CompletionWritebackSink, flush_outbox  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outbox", default=str(WRITEBACK_OUTBOX))
    parser.add_argument("--data-source-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    outbox = WritebackOutbox(args.outbox)
    sink = CompletionWritebackSink(args.data_source_id)
    events = outbox.load_events()
    if not args.apply:
        payload = {
            "mode": "dry_run",
            "configured": sink.configured(),
            "event_count": len(events),
            "pages": [
                sink.dry_run_payload_for_event(event)
                for event in events
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"dry_run events={len(events)} configured={sink.configured()}")
            for event in events:
                print(f"{event.generated_at} {event.status} {event.video_name}")
        return 0

    result = flush_outbox(outbox, sink, dry_run=False)
    if args.json:
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    else:
        print(
            f"attempted={result.attempted} "
            f"succeeded={result.succeeded} "
            f"remaining={result.remaining} "
            f"message={result.message}"
        )
    return 0 if result.remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
