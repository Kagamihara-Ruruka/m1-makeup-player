from __future__ import annotations

import re
from pathlib import Path

from .models import PlaybackRecord
from .subtitle import SUPPORTED_SUBTITLE_SUFFIXES, SubtitleCue, load_subtitle


def safe_filename_stem(value: str) -> str:
    stem = Path(value).stem or value
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem)
    stem = re.sub(r"\s+", "_", stem).strip("._ ")
    return stem or "subtitle"


class SubtitleResolver:
    def __init__(self, subtitle_dir: str | Path) -> None:
        self.subtitle_dir = Path(subtitle_dir)

    def candidates_for(self, record: PlaybackRecord) -> list[Path]:
        explicit = Path(record.subtitle_path) if record.subtitle_path else None
        stable_safe = record.stable_key.replace(":", "_")
        video_stem = safe_filename_stem(record.video_name)
        candidates: list[Path] = []
        if explicit:
            candidates.append(explicit)
        for stem in (stable_safe, video_stem):
            candidates.extend(self.subtitle_dir / f"{stem}{suffix}" for suffix in SUPPORTED_SUBTITLE_SUFFIXES)
        return candidates

    def load_for(self, record: PlaybackRecord) -> tuple[Path | None, list[SubtitleCue]]:
        for candidate in self.candidates_for(record):
            if candidate.exists():
                return candidate, load_subtitle(candidate)
        return None, []
