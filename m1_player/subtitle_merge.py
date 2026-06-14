from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .subtitle import SubtitleCue, load_subtitle


def merge_subtitle_files(
    output_path: str | Path,
    input_paths: Iterable[str | Path],
) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for input_path in input_paths:
        path = Path(input_path)
        if not path.exists():
            continue
        cues.extend(load_subtitle(path))
    merged = merge_cues(cues)
    write_srt_cues(output_path, merged)
    return merged


def merge_cues(cues: Iterable[SubtitleCue]) -> list[SubtitleCue]:
    ordered = sorted(cues, key=lambda cue: (cue.start_sec, cue.end_sec, normalize_text(cue.text)))
    merged: list[SubtitleCue] = []
    seen: set[tuple[int, int, str]] = set()
    for cue in ordered:
        key = (
            int(round(cue.start_sec * 10)),
            int(round(cue.end_sec * 10)),
            normalize_text(cue.text),
        )
        if key in seen:
            continue
        seen.add(key)
        if merged and normalize_text(merged[-1].text) == normalize_text(cue.text):
            if cue.start_sec <= merged[-1].end_sec + 0.5:
                merged[-1] = replace(merged[-1], end_sec=max(merged[-1].end_sec, cue.end_sec))
                continue
        merged.append(cue)
    return [replace(cue, index=index) for index, cue in enumerate(merged, 1)]


def write_srt_cues(path: str | Path, cues: Iterable[SubtitleCue]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_timestamp(cue.start_sec)} --> {format_srt_timestamp(cue.end_sec)}",
                    cue.text,
                ]
            )
        )
    output_path.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8", newline="\n")


def format_srt_timestamp(value: float) -> str:
    value = max(0.0, float(value))
    total_ms = int(round(value * 1000))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    seconds = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def normalize_text(value: str) -> str:
    return " ".join(str(value).split())
