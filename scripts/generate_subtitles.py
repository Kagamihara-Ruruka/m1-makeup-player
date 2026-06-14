from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.attachment_resolver import NotionAttachmentResolver  # noqa: E402
from m1_player.config import AppConfig, PROGRESS_CACHE, RESOLVED_URL_CACHE, SUBTITLE_DIR  # noqa: E402
from m1_player.models import PlaybackRecord  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.subtitle_generation import (  # noqa: E402
    SubtitleGenerationError,
    SubtitleGenerationOptions,
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_TECHNICAL_HOTWORDS,
    generate_subtitle_sidecar,
    subtitle_generation_dependency_status,
)
from m1_player.subtitle_resolver import SubtitleResolver  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local subtitle sidecars for Notion-streamed lesson videos.")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--subtitle-dir", default=str(SUBTITLE_DIR))
    parser.add_argument("--url-cache", default=str(RESOLVED_URL_CACHE))
    parser.add_argument("--local-settings", default=None)
    parser.add_argument("--key", action="append", default=[], help="Playback stable key to generate.")
    parser.add_argument("--name-contains", default=None, help="Generate records whose video name contains this text.")
    parser.add_argument("--all", action="store_true", help="Generate every selected missing subtitle.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--model", default="medium")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--max-seconds", type=float, default=None, help="Bound transcription to the first N seconds for smoke tests.")
    parser.add_argument("--initial-prompt", default=DEFAULT_INITIAL_PROMPT)
    parser.add_argument("--hotwords", default=DEFAULT_TECHNICAL_HOTWORDS)
    parser.add_argument("--format", default="srt", choices=["srt", "vtt", "md"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--progress-log", help="Optional JSONL progress log for long-running generation.")
    parser.add_argument("--check-deps", action="store_true")
    args = parser.parse_args()

    dependency = subtitle_generation_dependency_status()
    if args.check_deps:
        print(
            json.dumps(
                {
                    "ready": dependency.ready,
                    "faster_whisper_available": dependency.faster_whisper_available,
                    "cuda_runtime_available": dependency.cuda_runtime_available,
                    "message": dependency.message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if dependency.ready else 1

    store = ProgressStore(args.cache)
    store.load()
    records = select_records(list(store.records.values()), args.key, args.name_contains, args.all)
    if args.limit is not None:
        records = records[: max(0, args.limit)]
    if not records:
        print("No matching records. Use --all, --key, or --name-contains.")
        return 1

    subtitle_resolver = SubtitleResolver(args.subtitle_dir)
    resolver = NotionAttachmentResolver(
        cache=ResolvedUrlCache(args.url_cache),
        local_settings_path=args.local_settings,
    )
    options = SubtitleGenerationOptions(
        model_size=args.model,
        language=args.language or None,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=max(1, args.batch_size),
        beam_size=max(1, args.beam_size),
        overwrite=args.overwrite,
        output_suffix=f".{args.format}",
        max_duration_sec=args.max_seconds,
        initial_prompt=args.initial_prompt or None,
        hotwords=args.hotwords or None,
    )

    payloads = []
    failed = 0
    for record in records:
        write_progress_event(args.progress_log, "record_selected", record)
        existing_path, existing_cues = subtitle_resolver.load_for(record)
        if existing_path and existing_cues and not args.overwrite:
            payload = {
                "record_key": record.stable_key,
                "video_name": record.video_name,
                "status": "skipped_existing",
                "subtitle_path": str(existing_path),
                "cue_count": len(existing_cues),
            }
            payloads.append(payload)
            write_progress_event(args.progress_log, "skipped_existing", record, payload)
            print_status(payload, args.json)
            continue
        media_ref = playable_media_ref(record, resolver)
        if media_ref is None:
            failed += 1
            payload = {
                "record_key": record.stable_key,
                "video_name": record.video_name,
                "status": "failed",
                "message": "could not resolve playable media URL",
            }
            payloads.append(payload)
            write_progress_event(args.progress_log, "failed_media_resolution", record, payload)
            print_status(payload, args.json)
            continue
        write_progress_event(
            args.progress_log,
            "generation_started",
            record,
            {
                "model_size": options.model_size,
                "device": options.device,
                "compute_type": options.compute_type,
                "max_duration_sec": options.max_duration_sec,
            },
        )
        try:
            result = generate_subtitle_sidecar(record, media_ref, args.subtitle_dir, options)
        except SubtitleGenerationError as exc:
            failed += 1
            payload = {
                "record_key": record.stable_key,
                "video_name": record.video_name,
                "status": "failed",
                "message": str(exc),
            }
            payloads.append(payload)
            write_progress_event(args.progress_log, "failed_generation", record, payload)
            print_status(payload, args.json)
            continue
        if result.subtitle_path:
            record.subtitle_path = result.subtitle_path
            store.records[record.stable_key] = record
            store.save()
        payload = {
            "record_key": result.record_key,
            "video_name": record.video_name,
            "status": result.status,
            "subtitle_path": result.subtitle_path,
            "cue_count": result.cue_count,
            "elapsed_sec": result.elapsed_sec,
            "audio_duration_sec": result.audio_duration_sec,
            "handshake_elapsed_sec": result.handshake_elapsed_sec,
            "decode_loop_elapsed_sec": result.decode_loop_elapsed_sec,
            "model_load_elapsed_sec": result.model_load_elapsed_sec,
            "decode_elapsed_sec": result.decode_elapsed_sec,
            "inference_elapsed_sec": result.inference_elapsed_sec,
            "processing_elapsed_without_handshake_sec": result.processing_elapsed_without_handshake_sec,
            "processing_capacity_ratio": result.processing_capacity_ratio,
            "can_keep_up_8x_without_handshake": result.can_keep_up_8x_without_handshake,
            "model_size": result.model_size,
            "device": result.device,
            "compute_type": result.compute_type,
            "message": result.message,
        }
        if result.rolling_pipeline_plan:
            payload["rolling_pipeline_plan"] = asdict(result.rolling_pipeline_plan)
        payloads.append(payload)
        write_progress_event(args.progress_log, "generated", record, payload)
        print_status(payload, args.json)

    if args.json:
        print(json.dumps({"results": payloads, "failed": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def select_records(
    records: list[PlaybackRecord],
    keys: list[str],
    name_contains: str | None,
    include_all: bool,
) -> list[PlaybackRecord]:
    selected = sorted(records, key=lambda item: (item.course_date or "", item.segment_index, item.video_name))
    if include_all:
        return selected
    if keys:
        wanted = set(keys)
        return [record for record in selected if record.stable_key in wanted]
    if name_contains:
        needle = name_contains.lower()
        return [record for record in selected if needle in record.video_name.lower()]
    return []


def playable_media_ref(record: PlaybackRecord, resolver: NotionAttachmentResolver) -> str | None:
    source = parse_video_source(record.source_ref)
    if source.playable_url:
        return source.playable_url
    resolution = resolver.resolve(source)
    if resolution.resolved:
        return resolution.playable_url
    return None


def print_status(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        return
    status = payload.get("status")
    name = payload.get("video_name")
    message = payload.get("message") or payload.get("subtitle_path") or ""
    if status == "generated":
        message = (
            f"{message}; cues={payload.get('cue_count')} "
            f"elapsed={payload.get('elapsed_sec')}s "
            f"audio={payload.get('audio_duration_sec')}s "
            f"handshake={payload.get('handshake_elapsed_sec')}s "
            f"loop={payload.get('decode_loop_elapsed_sec')}s "
            f"model_load={payload.get('model_load_elapsed_sec')}s "
            f"decode={payload.get('decode_elapsed_sec')}s "
            f"inference={payload.get('inference_elapsed_sec')}s "
            f"capacity={payload.get('processing_capacity_ratio')}x "
            f"keep_up_8x={payload.get('can_keep_up_8x_without_handshake')} "
            f"device={payload.get('device')}/{payload.get('compute_type')}"
        )
        plan = payload.get("rolling_pipeline_plan")
        if isinstance(plan, dict):
            message += (
                f" decode_workers={plan.get('recommended_decode_workers')} "
                f"capacity={plan.get('expected_pipeline_capacity_ratio')}x "
                f"keep_up={plan.get('can_keep_up')}"
            )
    print(f"{status} {name} {message}")


def write_progress_event(
    progress_log: str | None,
    stage: str,
    record: PlaybackRecord,
    payload: dict[str, object] | None = None,
) -> None:
    if not progress_log:
        return
    path = Path(progress_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "stage": stage,
        "record_key": record.stable_key,
        "video_name": record.video_name,
    }
    if payload:
        event.update(payload)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
