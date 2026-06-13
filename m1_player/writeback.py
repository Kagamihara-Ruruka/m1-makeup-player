from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PlaybackRecord


@dataclass(frozen=True)
class WritebackEvent:
    event_type: str
    stable_key: str
    video_name: str
    course_page_url: str
    course_date: str | None
    segment_index: int
    last_position_sec: float
    duration_sec: float | None
    progress_percent: float
    status: str
    completed_at: str | None
    generated_at: str
    video_block_ref: str | None = None
    source_ref: str | None = None
    subtitle_path: str | None = None

    @classmethod
    def from_record(cls, record: PlaybackRecord, event_type: str) -> "WritebackEvent":
        return cls(
            event_type=event_type,
            stable_key=record.stable_key,
            video_name=record.video_name,
            course_page_url=record.course_page_url,
            course_date=record.course_date,
            segment_index=record.segment_index,
            last_position_sec=record.last_position_sec,
            duration_sec=record.duration_sec,
            progress_percent=record.progress_percent,
            status=record.status.value,
            completed_at=record.completed_at,
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            video_block_ref=record.video_block_ref,
            source_ref=record.source_ref,
            subtitle_path=record.subtitle_path,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "stable_key": self.stable_key,
            "video_name": self.video_name,
            "course_page_url": self.course_page_url,
            "course_date": self.course_date,
            "segment_index": self.segment_index,
            "last_position_sec": self.last_position_sec,
            "duration_sec": self.duration_sec,
            "progress_percent": self.progress_percent,
            "status": self.status,
            "completed_at": self.completed_at,
            "generated_at": self.generated_at,
            "video_block_ref": self.video_block_ref,
            "source_ref": self.source_ref,
            "subtitle_path": self.subtitle_path,
        }


class WritebackOutbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: WritebackEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_json(), ensure_ascii=False, sort_keys=True) + "\n")

    def append_completion(self, record: PlaybackRecord) -> None:
        self.append(WritebackEvent.from_record(record, "completed"))

    def has_event(self, event_type: str, stable_key: str) -> bool:
        return any(
            event.event_type == event_type and event.stable_key == stable_key
            for event in self.load_events()
        )

    def load_events(self) -> list[WritebackEvent]:
        if not self.path.exists():
            return []
        events: list[WritebackEvent] = []
        with self.path.open("r", encoding="utf-8", errors="strict") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(writeback_event_from_json(json.loads(line)))
        return events

    def count_events(self) -> int:
        return len(self.load_events())

    def replace_events(self, events: list[WritebackEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event.to_json(), ensure_ascii=False, sort_keys=True) + "\n")


def writeback_event_from_json(value: dict[str, Any]) -> WritebackEvent:
    return WritebackEvent(
        event_type=str(value.get("event_type", "")),
        stable_key=str(value.get("stable_key", "")),
        video_name=str(value.get("video_name", "")),
        course_page_url=str(value.get("course_page_url", "")),
        course_date=str(value["course_date"]) if value.get("course_date") is not None else None,
        segment_index=int(value.get("segment_index", 0)),
        last_position_sec=float(value.get("last_position_sec", 0.0)),
        duration_sec=float(value["duration_sec"]) if value.get("duration_sec") is not None else None,
        progress_percent=float(value.get("progress_percent", 0.0)),
        status=str(value.get("status", "")),
        completed_at=str(value["completed_at"]) if value.get("completed_at") is not None else None,
        generated_at=str(value.get("generated_at", "")),
        video_block_ref=str(value["video_block_ref"]) if value.get("video_block_ref") is not None else None,
        source_ref=str(value["source_ref"]) if value.get("source_ref") is not None else None,
        subtitle_path=str(value["subtitle_path"]) if value.get("subtitle_path") is not None else None,
    )
