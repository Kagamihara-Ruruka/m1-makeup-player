from __future__ import annotations

from typing import Any

from .writeback import WritebackEvent
from .writeback_schema import completion_event_properties


def notion_properties_for_completion_event(event: WritebackEvent) -> dict[str, Any]:
    canonical = completion_event_properties(event)
    properties: dict[str, Any] = {
        "影片名稱": title(canonical["影片名稱"]),
        "課程頁 URL": url_or_rich_text(canonical.get("課程頁")),
        "段落序號": number(canonical["段落序號"]),
        "最後播放秒數": number(canonical["最後播放秒數"]),
        "進度百分比": number(canonical["進度百分比"]),
        "補課狀態": select(canonical["補課狀態"]),
        "最後更新時間": date(canonical["date:最後更新時間:start"]),
    }
    optional_map = {
        "影片 block id": rich_text,
        "影片來源": rich_text,
        "字幕路徑": rich_text,
        "影片總長秒數": number,
    }
    for name, converter in optional_map.items():
        value = canonical.get(name)
        if value not in (None, ""):
            properties[name] = converter(value)
    course_date = canonical.get("date:課程日期:start")
    if course_date:
        properties["課程日期"] = date(course_date)
    completed_at = canonical.get("date:完整補課時間:start")
    if completed_at:
        properties["完整補課時間"] = date(completed_at)
    return properties


def title(value: object) -> dict[str, Any]:
    return {"title": [{"text": {"content": str(value)}}]}


def rich_text(value: object) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": str(value)}}]}


def url_or_rich_text(value: object) -> dict[str, Any]:
    text = "" if value is None else str(value)
    if text.startswith(("http://", "https://")):
        return {"url": text}
    return rich_text(text)


def number(value: object) -> dict[str, Any]:
    return {"number": float(value)}


def select(value: object) -> dict[str, Any]:
    return {"select": {"name": str(value)}}


def date(value: object) -> dict[str, Any]:
    return {"date": {"start": str(value)}}
