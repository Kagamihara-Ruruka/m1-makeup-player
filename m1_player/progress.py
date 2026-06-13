from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import PlaybackRecord, VideoSegment


@dataclass(frozen=True)
class ProgressMetadata:
    last_sync_backend: str | None = None
    last_synced_at: str | None = None
    last_course_page_count: int = 0
    last_video_segment_count: int = 0

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: Any) -> "ProgressMetadata":
        if not isinstance(value, dict):
            return cls()
        return cls(
            last_sync_backend=_optional_str(value.get("last_sync_backend")),
            last_synced_at=_optional_str(value.get("last_synced_at")),
            last_course_page_count=_non_negative_int(value.get("last_course_page_count")),
            last_video_segment_count=_non_negative_int(value.get("last_video_segment_count")),
        )


class ProgressStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.records: dict[str, PlaybackRecord] = {}
        self.metadata = ProgressMetadata()

    def load(self) -> None:
        if not self.path.exists():
            self.records = {}
            self.metadata = ProgressMetadata()
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.metadata = ProgressMetadata.from_json(data.get("metadata"))
        self.records = {
            key: PlaybackRecord.from_json(value)
            for key, value in data.get("records", {}).items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": self.metadata.to_json(),
            "records": {
                key: record.to_json()
                for key, record in sorted(self.records.items())
            }
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def sync_segments(self, segments: list[VideoSegment]) -> list[PlaybackRecord]:
        synced: list[PlaybackRecord] = []
        for segment in segments:
            record = self.records.get(segment.stable_key)
            if record is None:
                record = PlaybackRecord.from_segment(segment)
                self.records[segment.stable_key] = record
            else:
                record.refresh_metadata_from_segment(segment)
            synced.append(record)
        return synced

    def record_sync_metadata(self, sync_backend: str, course_page_count: int, video_segment_count: int) -> None:
        self.metadata = ProgressMetadata(
            last_sync_backend=sync_backend,
            last_synced_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            last_course_page_count=max(0, int(course_page_count)),
            last_video_segment_count=max(0, int(video_segment_count)),
        )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
