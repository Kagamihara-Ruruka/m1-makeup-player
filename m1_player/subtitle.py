from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUBTITLE_SUFFIXES = (".srt", ".vtt", ".md")
TIMESTAMP_PATTERN = r"\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?"
MARKDOWN_TIMESTAMP_RE = re.compile(
    rf"""
    ^\s*
    (?:[-*+]\s*)?
    (?:\#{1,6}\s*)?
    \[?
    (?P<start>{TIMESTAMP_PATTERN})
    \]?
    (?:
        \s*(?:-->|~|至|到)\s*
        \[?
        (?P<end>{TIMESTAMP_PATTERN})
        \]?
    )?
    (?P<body>.*)
    $
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_sec: float
    end_sec: float
    text: str

    def contains(self, position_sec: float) -> bool:
        return self.start_sec <= position_sec < self.end_sec


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt_or_vtt(text: str) -> list[SubtitleCue]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"^WEBVTT.*?\n\n", "", text, flags=re.DOTALL)
    blocks = re.split(r"\n{2,}", text)
    cues: list[SubtitleCue] = []
    next_index = 1
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if "-->" in lines[0]:
            time_line = lines[0]
            body_lines = lines[1:]
        elif len(lines) >= 2 and "-->" in lines[1]:
            time_line = lines[1]
            body_lines = lines[2:]
        else:
            continue
        left, right = [part.strip() for part in time_line.split("-->", 1)]
        right = right.split()[0]
        text_body = "\n".join(body_lines).strip()
        if not text_body:
            continue
        cues.append(SubtitleCue(next_index, parse_timestamp(left), parse_timestamp(right), text_body))
        next_index += 1
    return cues


def parse_markdown_transcript(text: str, default_duration_sec: float = 8.0) -> list[SubtitleCue]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    entries: list[tuple[float, float | None, str]] = []
    current_start: float | None = None
    current_end: float | None = None
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = MARKDOWN_TIMESTAMP_RE.match(line)
        if match:
            if current_start is not None and current_lines:
                entries.append((current_start, current_end, "\n".join(current_lines).strip()))
            current_start = parse_timestamp(match.group("start"))
            current_end = parse_timestamp(match.group("end")) if match.group("end") else None
            body = clean_markdown_cue_text(match.group("body"))
            current_lines = [body] if body else []
            continue
        if current_start is not None:
            current_lines.append(line)
    if current_start is not None and current_lines:
        entries.append((current_start, current_end, "\n".join(current_lines).strip()))

    cues: list[SubtitleCue] = []
    for index, (start, end, body) in enumerate(entries, 1):
        next_start = entries[index][0] if index < len(entries) else None
        cue_end = end if end is not None else inferred_end_time(start, next_start, default_duration_sec)
        if cue_end <= start:
            cue_end = start + 0.5
        cues.append(SubtitleCue(index, start, cue_end, body))
    return cues


def clean_markdown_cue_text(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^\]\s*", "", value)
    value = re.sub(r"^[:：\-–—]\s*", "", value)
    return value.strip()


def inferred_end_time(start: float, next_start: float | None, default_duration_sec: float) -> float:
    if next_start is not None and next_start > start:
        return next_start
    return start + default_duration_sec


def parse_subtitle_text(text: str, suffix: str = "") -> list[SubtitleCue]:
    if suffix.lower() == ".md":
        return parse_markdown_transcript(text)
    return parse_srt_or_vtt(text)


def load_subtitle(path: str | Path) -> list[SubtitleCue]:
    subtitle_path = Path(path)
    return parse_subtitle_text(
        subtitle_path.read_text(encoding="utf-8", errors="strict"),
        subtitle_path.suffix,
    )


def active_cue(cues: list[SubtitleCue], position_sec: float) -> SubtitleCue | None:
    for cue in cues:
        if cue.contains(position_sec):
            return cue
    return None
