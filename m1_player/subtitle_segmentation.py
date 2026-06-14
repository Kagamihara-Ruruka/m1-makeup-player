from __future__ import annotations

import re

from .subtitle_generation import GeneratedSubtitleSegment


DEFAULT_MAX_CUE_CHARS = 38
DEFAULT_MAX_CUE_DURATION_SEC = 7.0
_PUNCTUATED_CHUNK_RE = re.compile(r"[^，。！？；,.!?;]+[，。！？；,.!?;]*")


def split_long_subtitle_segments(
    segments: list[GeneratedSubtitleSegment],
    max_chars: int = DEFAULT_MAX_CUE_CHARS,
    max_duration_sec: float = DEFAULT_MAX_CUE_DURATION_SEC,
) -> list[GeneratedSubtitleSegment]:
    refined: list[GeneratedSubtitleSegment] = []
    for segment in segments:
        pieces = split_subtitle_text(segment.text, max_chars=max_chars)
        if len(pieces) <= 1 and segment_duration(segment) <= max_duration_sec:
            refined.append(segment)
            continue
        refined.extend(distribute_segment_timing(segment, pieces))
    return reindex_segments(refined)


def split_subtitle_text(text: str, max_chars: int = DEFAULT_MAX_CUE_CHARS) -> list[str]:
    normalized = collapse_spaces(text)
    if not normalized:
        return []
    pieces: list[str] = []
    for chunk in _punctuated_chunks(normalized):
        if len(chunk) <= max_chars:
            pieces.append(chunk)
        else:
            pieces.extend(fixed_width_chunks(chunk, max_chars=max_chars))
    return merge_tiny_chunks(pieces, max_chars=max_chars)


def distribute_segment_timing(
    segment: GeneratedSubtitleSegment,
    pieces: list[str],
) -> list[GeneratedSubtitleSegment]:
    if not pieces:
        return []
    duration = max(0.1, segment_duration(segment))
    total_weight = sum(max(1, len(piece)) for piece in pieces)
    cursor = segment.start_sec
    refined: list[GeneratedSubtitleSegment] = []
    for index, piece in enumerate(pieces, 1):
        if index == len(pieces):
            end_sec = segment.end_sec
        else:
            weight = max(1, len(piece))
            end_sec = cursor + duration * (weight / total_weight)
            end_sec = min(segment.end_sec, max(cursor + 0.1, end_sec))
        refined.append(
            GeneratedSubtitleSegment(
                index=index,
                start_sec=round(cursor, 3),
                end_sec=round(max(cursor + 0.1, end_sec), 3),
                text=piece,
            )
        )
        cursor = end_sec
    return refined


def reindex_segments(segments: list[GeneratedSubtitleSegment]) -> list[GeneratedSubtitleSegment]:
    return [
        GeneratedSubtitleSegment(
            index=index,
            start_sec=segment.start_sec,
            end_sec=segment.end_sec,
            text=segment.text,
        )
        for index, segment in enumerate(segments, 1)
    ]


def fixed_width_chunks(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars].strip() for index in range(0, len(text), max_chars) if text[index : index + max_chars].strip()]


def merge_tiny_chunks(pieces: list[str], max_chars: int) -> list[str]:
    merged: list[str] = []
    for piece in pieces:
        if not merged:
            merged.append(piece)
            continue
        if len(piece) <= 6 and len(merged[-1]) + len(piece) <= max_chars:
            merged[-1] = f"{merged[-1]}{piece}"
        else:
            merged.append(piece)
    return merged


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def segment_duration(segment: GeneratedSubtitleSegment) -> float:
    return max(0.0, float(segment.end_sec) - float(segment.start_sec))


def _punctuated_chunks(text: str) -> list[str]:
    chunks = [match.group(0).strip() for match in _PUNCTUATED_CHUNK_RE.finditer(text)]
    return [chunk for chunk in chunks if chunk]
