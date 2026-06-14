from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import PROGRESS_CACHE, SUBTITLE_DIR  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.subtitle import load_subtitle  # noqa: E402
from m1_player.subtitle_resolver import SubtitleResolver  # noqa: E402
from m1_player.subtitle_rolling_scheduler import (  # noqa: E402
    covered_ranges_from_cues,
    plan_rolling_subtitle_windows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan rolling subtitle future/backfill jobs.")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--subtitle-dir", default=str(SUBTITLE_DIR))
    parser.add_argument("--key", help="Playback stable key.")
    parser.add_argument("--subtitle-path", help="Optional explicit subtitle sidecar path.")
    parser.add_argument("--position-sec", type=float, default=None)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--playback-rate", type=float, default=1.0)
    parser.add_argument("--window-sec", type=float, default=60.0)
    parser.add_argument("--overlap-sec", type=float, default=3.0)
    parser.add_argument("--headless-workers", type=int, default=3)
    parser.add_argument("--future-horizon-sec", type=float, default=None)
    parser.add_argument("--backfill-partitions", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    record = None
    if args.key:
        store = ProgressStore(args.cache)
        store.load()
        record = store.records.get(args.key)
        if record is None:
            print(f"record not found: {args.key}", file=sys.stderr)
            return 1

    position_sec = args.position_sec
    duration_sec = args.duration_sec
    if record is not None:
        position_sec = record.last_position_sec if position_sec is None else position_sec
        duration_sec = record.duration_sec if duration_sec is None else duration_sec
    if position_sec is None or duration_sec is None:
        print("--position-sec and --duration-sec are required without a complete --key record", file=sys.stderr)
        return 1

    subtitle_path = resolve_subtitle_path(args.subtitle_path, record, Path(args.subtitle_dir))
    cues = load_subtitle(subtitle_path) if subtitle_path is not None else []
    schedule = plan_rolling_subtitle_windows(
        playback_position_sec=position_sec,
        duration_sec=duration_sec,
        covered_ranges=covered_ranges_from_cues(cues),
        playback_rate=args.playback_rate,
        window_sec=args.window_sec,
        overlap_sec=args.overlap_sec,
        headless_worker_count=args.headless_workers,
        future_horizon_sec=args.future_horizon_sec,
        backfill_partition_count=args.backfill_partitions,
    )
    payload = schedule.to_payload()
    payload["subtitle_path"] = str(subtitle_path) if subtitle_path is not None else None
    payload["covered_cue_count"] = len(cues)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(payload)
    return 0


def resolve_subtitle_path(explicit_path: str | None, record: object | None, subtitle_dir: Path) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.exists() else None
    if record is None:
        return None
    path, _cues = SubtitleResolver(subtitle_dir).load_for(record)
    return path


def print_summary(payload: dict[str, object]) -> None:
    print(
        "schedule "
        f"T={payload['playback_position_sec']}s "
        f"rate={payload['playback_rate']}x "
        f"window={payload['window_sec']}s "
        f"future={payload['future_job_count']} "
        f"backfill={payload['backfill_job_count']} "
        f"covered_cues={payload['covered_cue_count']}"
    )
    for job in payload["jobs"]:
        assert isinstance(job, dict)
        print(
            f"{job['priority']:>3} "
            f"{job['lane']:<12} "
            f"{job['job_id']:<12} "
            f"{job['start_sec']:>8}s -> {job['end_sec']:>8}s "
            f"decode {job['decode_start_sec']:>8}s -> {job['decode_end_sec']:>8}s "
            f"slot={job['decode_worker_slot']} "
            f"asr={job['asr_device']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
