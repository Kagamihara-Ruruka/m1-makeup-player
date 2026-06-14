from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attachment_resolver import AttachmentResolution
from .models import PlaybackRecord
from .playability import PlayabilityStatus
from .video_source import VideoSourceInfo


@dataclass(frozen=True)
class VideoDetailSummary:
    lines: tuple[str, ...]

    def to_text(self) -> str:
        return "\n".join(self.lines)


def build_video_detail_summary(
    *,
    record: PlaybackRecord,
    source: VideoSourceInfo,
    resolution: AttachmentResolution,
    playability: PlayabilityStatus,
    subtitle_path: Path | None,
    cue_count: int,
    display_position_sec: float | None = None,
    display_progress_percent: float | None = None,
) -> VideoDetailSummary:
    duration_text = format_duration(record.duration_sec)
    position_sec = record.last_position_sec if display_position_sec is None else display_position_sec
    progress_percent = record.progress_percent if display_progress_percent is None else display_progress_percent
    position_text = format_duration(position_sec)
    subtitle_line = subtitle_status_line(subtitle_path, cue_count)
    completed_line = record.completed_at or "尚未完成"
    updated_line = record.updated_at or "尚未更新"
    loading_hint_line = playability.loading_hint or "無"

    return VideoDetailSummary(
        (
            f"影片：{record.video_name}",
            f"課程日期：{record.course_date or 'no-date'}",
            f"段落：P{record.segment_index:02d}",
            f"補課狀態：{record.status.value}",
            f"播放進度：{position_text} / {duration_text}（{progress_percent:.2f}%）",
            f"影片來源：{source.source_kind}",
            f"來源檔名：{source.filename_hint or '未知'}",
            f"解析狀態：{resolution.status} - {resolution.reason}",
            f"播放狀態：{playability.state}",
            f"載入提示：{loading_hint_line}",
            subtitle_line,
            f"完成時間：{completed_line}",
            f"最後更新：{updated_line}",
        )
    )


def subtitle_status_line(path: Path | None, cue_count: int) -> str:
    if path and cue_count > 0:
        return f"字幕：{path.name}（{cue_count} cues）"
    if path:
        return f"字幕：{path.name}（沒有可用 cue）"
    return "字幕：缺少本地字幕"


def format_duration(value: float | None) -> str:
    if value is None:
        return "--:--"
    total = max(0, int(value))
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
