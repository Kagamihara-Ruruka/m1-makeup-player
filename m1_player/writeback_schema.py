from __future__ import annotations

from typing import Any

from .models import PlaybackRecord
from .writeback import WritebackEvent


def completion_record_properties(record: PlaybackRecord) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "影片名稱": record.video_name,
        "課程頁": record.course_page_url,
        "段落序號": record.segment_index,
        "影片 block id": record.video_block_ref,
        "影片來源": record.source_ref,
        "最後播放秒數": round(record.last_position_sec, 3),
        "進度百分比": round(record.progress_percent, 2),
        "補課狀態": record.status.value,
    }
    if record.course_date:
        properties["date:課程日期:start"] = record.course_date
        properties["date:課程日期:is_datetime"] = 0
    if record.duration_sec is not None:
        properties["影片總長秒數"] = round(record.duration_sec, 3)
    if record.completed_at:
        properties["date:完整補課時間:start"] = record.completed_at
        properties["date:完整補課時間:is_datetime"] = 1
    if record.updated_at:
        properties["date:最後更新時間:start"] = record.updated_at
        properties["date:最後更新時間:is_datetime"] = 1
    if record.subtitle_path:
        properties["字幕路徑"] = record.subtitle_path
    return properties


def completion_event_properties(event: WritebackEvent) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "影片名稱": event.video_name,
        "課程頁": event.course_page_url,
        "段落序號": event.segment_index,
        "最後播放秒數": round(event.last_position_sec, 3),
        "進度百分比": round(event.progress_percent, 2),
        "補課狀態": event.status,
    }
    if event.course_date:
        properties["date:課程日期:start"] = event.course_date
        properties["date:課程日期:is_datetime"] = 0
    if event.video_block_ref:
        properties["影片 block id"] = event.video_block_ref
    if event.source_ref:
        properties["影片來源"] = event.source_ref
    if event.subtitle_path:
        properties["字幕路徑"] = event.subtitle_path
    if event.duration_sec is not None:
        properties["影片總長秒數"] = round(event.duration_sec, 3)
    if event.completed_at:
        properties["date:完整補課時間:start"] = event.completed_at
        properties["date:完整補課時間:is_datetime"] = 1
    properties["date:最後更新時間:start"] = event.generated_at
    properties["date:最後更新時間:is_datetime"] = 1
    return properties
