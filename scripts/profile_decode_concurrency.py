from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.attachment_resolver import NotionAttachmentResolver  # noqa: E402
from m1_player.config import PROGRESS_CACHE, RESOLVED_URL_CACHE  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.subtitle_generation import decode_audio_window_with_timing  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile concurrent remote audio decode pressure.")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--url-cache", default=str(RESOLVED_URL_CACHE))
    parser.add_argument("--local-settings", default=None)
    parser.add_argument("--key", required=True, help="Playback stable key to profile.")
    parser.add_argument("--windows", default="30,60,120")
    parser.add_argument("--concurrency", default="1,2,3,4")
    parser.add_argument("--start-offset-sec", type=float, default=0.0)
    parser.add_argument("--stride-factor", type=float, default=1.0)
    parser.add_argument("--cooldown-sec", type=float, default=1.0)
    parser.add_argument("--playback-rates", default="1,2,4,8")
    parser.add_argument("--safety-factor", type=float, default=1.35)
    parser.add_argument("--output", help="Optional JSON output path for the profiling payload.")
    parser.add_argument(
        "--max-overall-window-sec",
        type=float,
        default=60.0,
        help="Prefer overall recommendations within this window length; use 0 to disable the cap.",
    )
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
    for window_sec in parse_float_list(args.windows):
        for concurrency in parse_int_list(args.concurrency):
            row = profile_group(
                media_ref=media_ref,
                window_sec=window_sec,
                concurrency=concurrency,
                start_offset_sec=args.start_offset_sec,
                stride_factor=args.stride_factor,
            )
            rows.append(row)
            if not args.json:
                print_group(row)
            if args.cooldown_sec > 0:
                time.sleep(args.cooldown_sec)

    max_overall_window_sec = args.max_overall_window_sec if args.max_overall_window_sec > 0 else None
    payload = {
        "record_key": record.stable_key,
        "video_name": record.video_name,
        "playback_rates": parse_float_list(args.playback_rates),
        "safety_factor": args.safety_factor,
        "max_overall_window_sec": max_overall_window_sec,
        "results": rows,
        "recommended": choose_decode_concurrency(
            rows,
            parse_float_list(args.playback_rates),
            args.safety_factor,
            max_overall_window_sec=max_overall_window_sec,
        ),
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_recommendations(payload["recommended"])
    return 0 if any(row["success_count"] for row in rows) else 1


def playable_media_ref(source_ref: str, resolver: NotionAttachmentResolver) -> str | None:
    source = parse_video_source(source_ref)
    if source.playable_url:
        return source.playable_url
    resolution = resolver.resolve(source)
    if resolution.resolved:
        return resolution.playable_url
    return None


def profile_group(
    *,
    media_ref: str,
    window_sec: float,
    concurrency: int,
    start_offset_sec: float,
    stride_factor: float,
) -> dict[str, Any]:
    offsets = [start_offset_sec + index * window_sec * stride_factor for index in range(concurrency)]
    group_started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(decode_probe, media_ref, window_sec, offset, index)
            for index, offset in enumerate(offsets)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    wall_elapsed = round(time.perf_counter() - group_started, 3)
    successes = [item for item in results if item["status"] == "decoded"]
    failures = [item for item in results if item["status"] != "decoded"]
    elapsed_values = [float(item["elapsed_sec"]) for item in successes]
    handshake_values = [float(item["handshake_elapsed_sec"]) for item in successes]
    decode_loop_values = [float(item["decode_loop_elapsed_sec"]) for item in successes]
    aggregate_audio_sec = window_sec * len(successes)
    capacity_ratio = aggregate_audio_sec / wall_elapsed if wall_elapsed > 0 else 0.0
    return {
        "window_sec": window_sec,
        "concurrency": concurrency,
        "wall_elapsed_sec": wall_elapsed,
        "success_count": len(successes),
        "failure_count": len(failures),
        "error_rate": round(len(failures) / max(1, concurrency), 3),
        "mean_decode_sec": round(sum(elapsed_values) / len(elapsed_values), 3) if elapsed_values else None,
        "mean_handshake_sec": round(sum(handshake_values) / len(handshake_values), 3) if handshake_values else None,
        "mean_decode_loop_sec": round(sum(decode_loop_values) / len(decode_loop_values), 3) if decode_loop_values else None,
        "p95_decode_sec": percentile(elapsed_values, 95),
        "p95_handshake_sec": percentile(handshake_values, 95),
        "p95_decode_loop_sec": percentile(decode_loop_values, 95),
        "max_decode_sec": round(max(elapsed_values), 3) if elapsed_values else None,
        "capacity_ratio": round(capacity_ratio, 3),
        "offsets_sec": [round(value, 3) for value in offsets],
        "workers": sorted(results, key=lambda item: item["index"]),
    }


def decode_probe(media_ref: str, window_sec: float, start_sec: float, index: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = decode_audio_window_with_timing(media_ref, max_duration_sec=window_sec, start_sec=start_sec)
    except Exception as exc:  # noqa: BLE001 - profiler reports remote/decode failure classes.
        return {
            "index": index,
            "start_sec": round(start_sec, 3),
            "status": "failed",
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
    return {
        "index": index,
        "start_sec": round(start_sec, 3),
        "status": "decoded",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "handshake_elapsed_sec": result.handshake_elapsed_sec,
        "decode_loop_elapsed_sec": result.decode_loop_elapsed_sec,
        "sample_count": result.sample_count,
    }


def choose_decode_concurrency(
    rows: list[dict[str, Any]],
    playback_rates: list[float],
    safety_factor: float,
    max_overall_window_sec: float | None = 60.0,
) -> list[dict[str, Any]]:
    recommendations = []
    by_window: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        by_window.setdefault(float(row["window_sec"]), []).append(row)
    for playback_rate in playback_rates:
        required_capacity = playback_rate * safety_factor
        window_recommendations = []
        for window_sec, window_rows in sorted(by_window.items()):
            viable = [
                row
                for row in window_rows
                if row["failure_count"] == 0
                and row["capacity_ratio"] >= required_capacity
                and (row["p95_decode_sec"] is None or row["p95_decode_sec"] <= window_sec)
            ]
            if not viable:
                window_recommendations.append(
                    {
                        "window_sec": window_sec,
                        "status": "no_viable_concurrency",
                        "required_capacity_ratio": round(required_capacity, 3),
                    }
                )
                continue
            viable.sort(key=lambda row: (row["concurrency"], -row["capacity_ratio"]))
            best = viable[0]
            window_recommendations.append(
                {
                    "window_sec": window_sec,
                    "status": "recommended",
                    "concurrency": best["concurrency"],
                    "capacity_ratio": best["capacity_ratio"],
                    "p95_decode_sec": best["p95_decode_sec"],
                    "mean_handshake_sec": best.get("mean_handshake_sec"),
                    "p95_handshake_sec": best.get("p95_handshake_sec"),
                    "mean_decode_loop_sec": best.get("mean_decode_loop_sec"),
                    "p95_decode_loop_sec": best.get("p95_decode_loop_sec"),
                    "required_capacity_ratio": round(required_capacity, 3),
                    "startup_prewarm_sec": best.get("p95_handshake_sec"),
                }
            )
        overall = choose_overall_window(window_recommendations, max_overall_window_sec)
        recommendations.append(
            {
                "playback_rate": playback_rate,
                "required_capacity_ratio": round(required_capacity, 3),
                "by_window": window_recommendations,
                "overall": overall,
            }
        )
    return recommendations


def choose_overall_window(
    recommendations: list[dict[str, Any]],
    max_window_sec: float | None = 60.0,
) -> dict[str, Any] | None:
    viable = [row for row in recommendations if row["status"] == "recommended"]
    if not viable:
        return None
    bounded = [row for row in viable if max_window_sec is None or row["window_sec"] <= max_window_sec]
    pool = bounded or viable
    pool.sort(key=lambda row: (row["concurrency"], row["window_sec"], -row["capacity_ratio"]))
    selected = dict(pool[0])
    if max_window_sec is not None:
        selected["max_overall_window_sec"] = max_window_sec
        selected["used_window_cap_fallback"] = not bool(bounded)
    return selected


def percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((pct / 100) * (len(ordered) - 1)))
    return round(ordered[index], 3)


def parse_float_list(value: str) -> list[float]:
    parsed = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not parsed or any(item <= 0 for item in parsed):
        raise ValueError("all float list values must be positive")
    return parsed


def parse_int_list(value: str) -> list[int]:
    parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not parsed or any(item <= 0 for item in parsed):
        raise ValueError("all integer list values must be positive")
    return parsed


def print_group(row: dict[str, Any]) -> None:
    print(
        f"window={row['window_sec']}s "
        f"concurrency={row['concurrency']} "
        f"wall={row['wall_elapsed_sec']}s "
        f"mean={row['mean_decode_sec']}s "
        f"handshake={row['mean_handshake_sec']}s "
        f"loop={row['mean_decode_loop_sec']}s "
        f"p95={row['p95_decode_sec']}s "
        f"errors={row['failure_count']} "
        f"capacity={row['capacity_ratio']}x"
    )


def print_recommendations(recommendations: list[dict[str, Any]]) -> None:
    for item in recommendations:
        overall = item.get("overall")
        if not overall:
            print(f"playback={item['playback_rate']}x no viable concurrency")
            continue
        print(
            f"playback={item['playback_rate']}x "
            f"window={overall['window_sec']}s "
            f"concurrency={overall['concurrency']} "
            f"capacity={overall['capacity_ratio']}x "
            f"prewarm={overall.get('startup_prewarm_sec')}s "
            f"required={overall['required_capacity_ratio']}x"
            + (" window_cap_fallback" if overall.get("used_window_cap_fallback") else "")
        )


if __name__ == "__main__":
    raise SystemExit(main())
