from __future__ import annotations

import re

from .subtitle_generation import GeneratedSubtitleSegment


_TOKEN_SPLIT_RE = re.compile(r"[，,、。！？；,.!?;\s]+")


def filter_hallucinated_segments(
    segments: list[GeneratedSubtitleSegment],
) -> list[GeneratedSubtitleSegment]:
    return [
        segment
        for segment in segments
        if not is_repetitive_hallucination(segment.text)
    ]


def is_repetitive_hallucination(text: str) -> bool:
    if is_prompt_echo(text):
        return True
    tokens = meaningful_tokens(text)
    if len(tokens) < 10:
        return False
    unique_tokens = set(tokens)
    unique_ratio = len(unique_tokens) / len(tokens)
    top_frequency = max(tokens.count(token) for token in unique_tokens) / len(tokens)
    return unique_ratio <= 0.35 and top_frequency >= 0.25


def is_prompt_echo(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    return "請只轉寫實際聽到的語音" in normalized


def meaningful_tokens(text: str) -> list[str]:
    return [
        token
        for token in (part.strip() for part in _TOKEN_SPLIT_RE.split(text))
        if 2 <= len(token) <= 16
    ]
