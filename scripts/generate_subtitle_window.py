from __future__ import annotations

import argparse
import json
import sys
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
    render_srt,
    transcribe_media_with_timing,
)
from m1_player.video_source import parse_video_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate one rolling subtitle window.")
    parser.add_argument("--cache", default=str(PROGRESS_CACHE))
    parser.add_argument("--url-cache", default=str(RESOLVED_URL_CACHE))
    parser.add_argument("--local-settings", default=None)
    parser.add_argument("--key", required=True)
    parser.add_argument("--start-sec", type=float, required=True)
    parser.add_argument("--duration-sec", type=float, default=60.0)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="cuda", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--output-dir", default=str(ROOT / "tmp" / "subtitle_windows"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    store = ProgressStore(args.cache)
    store.load()
    record = store.records.get(args.key)
    if record is None:
        print(f"record not found: {args.key}", file=sys.stderr)
        return 1
    media_ref = playable_media_ref(
        record.source_ref,
        NotionAttachmentResolver(
            cache=ResolvedUrlCache(args.url_cache),
            local_settings_path=args.local_settings,
        ),
    )
    if media_ref is None:
        print("could not resolve playable media URL", file=sys.stderr)
        return 1
    options = SubtitleGenerationOptions(
        model_size=args.model,
        language=args.language or None,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=max(1, args.batch_size),
        beam_size=max(1, args.beam_size),
        max_duration_sec=max(1.0, args.duration_sec),
        start_sec=max(0.0, args.start_sec),
        initial_prompt=DEFAULT_INITIAL_PROMPT,
        hotwords=DEFAULT_TECHNICAL_HOTWORDS,
    )
    transcription = transcribe_media_with_timing(media_ref, options, args.device, args.compute_type)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_window_name(record.stable_key, options.start_sec, options.max_duration_sec or 0)}.srt"
    output_path.write_text(render_srt(transcription.segments), encoding="utf-8", newline="\n")
    processing_elapsed = round(transcription.decode_loop_elapsed_sec + transcription.inference_elapsed_sec, 3)
    cold_elapsed = round(processing_elapsed + transcription.model_load_elapsed_sec, 3)
    capacity = round(transcription.audio_duration_sec / processing_elapsed, 3) if processing_elapsed > 0 else None
    cold_capacity = round(transcription.audio_duration_sec / cold_elapsed, 3) if cold_elapsed > 0 else None
    payload = {
        "record_key": record.stable_key,
        "video_name": record.video_name,
        "status": "generated_window",
        "output_path": str(output_path),
        "start_sec": options.start_sec,
        "duration_sec": options.max_duration_sec,
        "audio_duration_sec": transcription.audio_duration_sec,
        "cue_count": len(transcription.segments),
        "handshake_elapsed_sec": transcription.handshake_elapsed_sec,
        "decode_loop_elapsed_sec": transcription.decode_loop_elapsed_sec,
        "model_load_elapsed_sec": transcription.model_load_elapsed_sec,
        "inference_elapsed_sec": transcription.inference_elapsed_sec,
        "cold_processing_elapsed_without_handshake_sec": cold_elapsed,
        "processing_elapsed_without_handshake_sec": processing_elapsed,
        "cold_processing_capacity_ratio": cold_capacity,
        "processing_capacity_ratio": capacity,
        "can_keep_up_8x_without_handshake": bool(capacity is not None and capacity >= 8.0),
        "cold_can_keep_up_8x_without_handshake": bool(cold_capacity is not None and cold_capacity >= 8.0),
        "model_size": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload)
    return 0


def playable_media_ref(source_ref: str, resolver: NotionAttachmentResolver) -> str | None:
    source = parse_video_source(source_ref)
    if source.playable_url:
        return source.playable_url
    resolution = resolver.resolve(source)
    if resolution.resolved:
        return resolution.playable_url
    return None


def safe_window_name(stable_key: str, start_sec: float, duration_sec: float) -> str:
    safe_key = "".join(ch if ch.isalnum() else "_" for ch in stable_key).strip("_")
    return f"{safe_key}_{int(start_sec):06d}_{int(duration_sec):04d}"


if __name__ == "__main__":
    raise SystemExit(main())
