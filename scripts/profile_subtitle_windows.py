from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.attachment_resolver import NotionAttachmentResolver  # noqa: E402
from m1_player.config import PROGRESS_CACHE, RESOLVED_URL_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.subtitle_generation import (  # noqa: E402
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_TECHNICAL_HOTWORDS,
    SubtitleGenerationOptions,
    transcribe_media_with_timing,
)
from m1_player.subtitle_pipeline_planner import plan_rolling_subtitle_pipeline  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile subtitle window lengths for rolling-ahead planning.")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--url-cache", default=str(RESOLVED_URL_CACHE))
    parser.add_argument("--local-settings", default=None)
    parser.add_argument("--key", required=True, help="Playback stable key to profile.")
    parser.add_argument("--windows", default="15,30,60,120", help="Comma-separated audio window seconds.")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--playback-rate", type=float, default=1.0)
    parser.add_argument("--safety-factor", type=float, default=1.35)
    parser.add_argument("--target-capacity", type=float, default=4.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    record = store.records.get(args.key)
    if record is None:
        print(f"record not found: {args.key}", file=sys.stderr)
        return 1

    resolver = NotionAttachmentResolver(
        cache=ResolvedUrlCache(args.url_cache),
        local_settings_path=args.local_settings,
    )
    media_ref = playable_media_ref(record.source_ref, resolver)
    if media_ref is None:
        print("could not resolve playable media URL", file=sys.stderr)
        return 1

    rows = []
    failed = 0
    for window_sec in parse_windows(args.windows):
        options = SubtitleGenerationOptions(
            model_size=args.model,
            language=args.language or None,
            device=args.device,
            compute_type=args.compute_type,
            batch_size=max(1, args.batch_size),
            beam_size=max(1, args.beam_size),
            max_duration_sec=window_sec,
            initial_prompt=DEFAULT_INITIAL_PROMPT,
            hotwords=DEFAULT_TECHNICAL_HOTWORDS,
        )
        try:
            transcription = transcribe_media_with_timing(
                media_ref,
                options=options,
                device=runtime_device(args.device, args.compute_type),
                compute_type=runtime_compute_type(args.device, args.compute_type),
            )
        except Exception as exc:  # noqa: BLE001 - profiler should report every failed window.
            failed += 1
            rows.append(
                {
                    "window_sec": window_sec,
                    "status": "failed",
                    "message": str(exc),
                }
            )
            continue
        plan = plan_rolling_subtitle_pipeline(
            audio_window_sec=window_sec,
            decode_elapsed_sec=transcription.decode_elapsed_sec,
            inference_elapsed_sec=transcription.inference_elapsed_sec,
            playback_rate=args.playback_rate,
            safety_factor=args.safety_factor,
        )
        rows.append(
            {
                "window_sec": window_sec,
                "status": "profiled",
                "cue_count": len(transcription.segments),
                "decode_elapsed_sec": transcription.decode_elapsed_sec,
                "inference_elapsed_sec": transcription.inference_elapsed_sec,
                "rolling_pipeline_plan": asdict(plan),
            }
        )

    payload = {
        "record_key": record.stable_key,
        "video_name": record.video_name,
        "model": args.model,
        "device": runtime_device(args.device, args.compute_type),
        "compute_type": runtime_compute_type(args.device, args.compute_type),
        "playback_rate": args.playback_rate,
        "safety_factor": args.safety_factor,
        "target_capacity": args.target_capacity,
        "results": rows,
        "failed": failed,
        "recommended_window_sec": choose_recommended_window(rows, args.target_capacity),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(payload)
    return 1 if failed == len(rows) else 0


def playable_media_ref(source_ref: str, resolver: NotionAttachmentResolver) -> str | None:
    source = parse_video_source(source_ref)
    if source.playable_url:
        return source.playable_url
    resolution = resolver.resolve(source)
    if resolution.resolved:
        return resolution.playable_url
    return None


def parse_windows(value: str) -> list[float]:
    windows: list[float] = []
    for raw in value.split(","):
        stripped = raw.strip()
        if not stripped:
            continue
        window = float(stripped)
        if window <= 0:
            raise ValueError(f"window must be positive: {raw}")
        windows.append(window)
    if not windows:
        raise ValueError("at least one window is required")
    return windows


def runtime_device(device: str, compute_type: str) -> str:
    if device != "auto":
        return device
    from m1_player.subtitle_generation import cuda_runtime_available  # noqa: PLC0415

    return "cuda" if cuda_runtime_available() else "cpu"


def runtime_compute_type(device: str, compute_type: str) -> str:
    if compute_type != "auto":
        return compute_type
    return "float16" if runtime_device(device, compute_type) == "cuda" else "int8"


def choose_recommended_window(rows: list[dict[str, object]], target_capacity: float = 4.0) -> float | None:
    candidates = []
    fallback_candidates = []
    for row in rows:
        plan = row.get("rolling_pipeline_plan")
        if not isinstance(plan, dict) or not plan.get("can_keep_up"):
            continue
        worker_count = int(plan.get("recommended_decode_workers", 99))
        capacity = float(plan.get("expected_pipeline_capacity_ratio", 0.0))
        window_sec = float(row.get("window_sec", 0.0))
        row_key = (worker_count, window_sec, -capacity, window_sec)
        fallback_candidates.append(row_key)
        if capacity >= target_capacity:
            candidates.append(row_key)
    if not candidates:
        candidates = fallback_candidates
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def print_human(payload: dict[str, object]) -> None:
    print(f"{payload['video_name']} model={payload['model']} device={payload['device']}/{payload['compute_type']}")
    print(f"target_capacity={payload['target_capacity']}")
    for row in payload["results"]:  # type: ignore[index]
        if row.get("status") != "profiled":
            print(f"{row['window_sec']}s failed: {row.get('message')}")
            continue
        plan = row["rolling_pipeline_plan"]
        print(
            f"{row['window_sec']}s "
            f"decode={row['decode_elapsed_sec']}s "
            f"inference={row['inference_elapsed_sec']}s "
            f"decode_ratio={plan['decode_realtime_ratio']} "
            f"inference_ratio={plan['inference_realtime_ratio']} "
            f"workers={plan['recommended_decode_workers']} "
            f"capacity={plan['expected_pipeline_capacity_ratio']} "
            f"keep_up={plan['can_keep_up']}"
        )
    print(f"recommended_window_sec={payload['recommended_window_sec']}")


if __name__ == "__main__":
    raise SystemExit(main())
