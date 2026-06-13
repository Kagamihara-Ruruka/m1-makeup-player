from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class LessonStatus(StrEnum):
    NOT_STARTED = "未開始"
    IN_PROGRESS = "補課中"
    COMPLETED = "已完成"
    REVIEW = "需重看"
    MISSING = "來源消失"


@dataclass(frozen=True)
class CoursePageRef:
    title: str
    page_id: str
    page_url: str
    course_date: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoSegment:
    stable_key: str
    course: CoursePageRef
    segment_index: int
    video_name: str
    video_block_ref: str
    source_ref: str
    transcript_ref: str | None = None


@dataclass
class PlaybackRecord:
    stable_key: str
    video_name: str
    course_page_url: str
    course_date: str | None
    segment_index: int
    video_block_ref: str
    source_ref: str
    subtitle_path: str | None = None
    last_position_sec: float = 0.0
    duration_sec: float | None = None
    progress_percent: float = 0.0
    status: LessonStatus = LessonStatus.NOT_STARTED
    completed_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_segment(cls, segment: VideoSegment) -> "PlaybackRecord":
        return cls(
            stable_key=segment.stable_key,
            video_name=segment.video_name,
            course_page_url=segment.course.page_url,
            course_date=segment.course.course_date,
            segment_index=segment.segment_index,
            video_block_ref=segment.video_block_ref,
            source_ref=segment.source_ref,
        )

    def refresh_metadata_from_segment(self, segment: VideoSegment) -> None:
        self.video_name = segment.video_name
        self.course_page_url = segment.course.page_url
        self.course_date = segment.course.course_date
        self.segment_index = segment.segment_index
        self.video_block_ref = segment.video_block_ref
        self.source_ref = segment.source_ref

    def update_position(self, position_sec: float, duration_sec: float | None) -> None:
        self.last_position_sec = max(0.0, float(position_sec))
        self.duration_sec = duration_sec if duration_sec and duration_sec > 0 else self.duration_sec
        if self.duration_sec and self.duration_sec > 0:
            self.progress_percent = min(100.0, round(self.last_position_sec / self.duration_sec * 100.0, 2))
        elif self.last_position_sec > 0:
            self.progress_percent = max(self.progress_percent, 0.01)
        if self.status == LessonStatus.NOT_STARTED and self.last_position_sec > 0:
            self.status = LessonStatus.IN_PROGRESS
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def mark_completed(self, duration_sec: float | None = None) -> None:
        if duration_sec and duration_sec > 0:
            self.duration_sec = float(duration_sec)
            self.last_position_sec = float(duration_sec)
        self.progress_percent = 100.0
        self.status = LessonStatus.COMPLETED
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self.completed_at = now
        self.updated_at = now

    def should_complete(self, position_sec: float, duration_sec: float | None, threshold: float = 0.95) -> bool:
        if not duration_sec or duration_sec <= 0:
            return False
        return position_sec >= duration_sec * threshold

    def to_json(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "PlaybackRecord":
        data = dict(value)
        data["status"] = LessonStatus(data.get("status", LessonStatus.NOT_STARTED.value))
        return cls(**data)
