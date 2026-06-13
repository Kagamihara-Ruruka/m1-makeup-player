from __future__ import annotations

import hashlib
import html
import json
import re
from urllib.parse import unquote
from dataclasses import dataclass

from .models import CoursePageRef, VideoSegment
from .video_source import parse_video_source


VIDEO_RE = re.compile(r"<video\s+src=\"(?P<src>[^\"]+)\"[^>]*></video>", re.IGNORECASE)
PAGE_RE = re.compile(r"<page\s+url=\"(?P<url>[^\"]+)\"[^>]*>")
PROP_RE = re.compile(r"<properties>\s*(?P<json>\{.*?\})\s*</properties>", re.DOTALL)
MEETING_RE = re.compile(r"<meeting-notes\s+readOnlyViewMeetingNoteUrl=\"(?P<url>[^\"]+)\"", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedCoursePage:
    course: CoursePageRef
    videos: tuple[VideoSegment, ...]


def compact_notion_id(value: str) -> str:
    match = re.search(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    if match:
        return match.group(0)
    return value


def stable_video_key(course_page_id: str, segment_index: int, source_ref: str) -> str:
    digest = hashlib.sha256(f"{course_page_id}|{segment_index}|{source_ref}".encode("utf-8")).hexdigest()[:16]
    return f"{compact_notion_id(course_page_id)}:{segment_index:03d}:{digest}"


def parse_properties(fetch_text: str) -> dict[str, object]:
    match = PROP_RE.search(fetch_text)
    if not match:
        return {}
    return json.loads(match.group("json"))


def parse_page_url(fetch_text: str) -> str:
    match = PAGE_RE.search(fetch_text)
    return html.unescape(match.group("url")) if match else ""


def infer_video_name(source_ref: str, index: int) -> str:
    source_info = parse_video_source(source_ref)
    if source_info.filename_hint:
        return source_info.filename_hint
    decoded = unquote(html.unescape(source_ref))
    filename_match = re.search(r"attachment:[^:]+:([^\"}]+)", decoded)
    if filename_match:
        return filename_match.group(1)
    tail = decoded.rstrip("/").split("/")[-1]
    return tail or f"video_{index:02d}"


def parse_course_page(fetch_text: str, page_id: str | None = None) -> ParsedCoursePage:
    props = parse_properties(fetch_text)
    page_url = parse_page_url(fetch_text)
    resolved_page_id = page_id or compact_notion_id(page_url)
    title = str(props.get("名稱") or props.get("title") or "untitled_course")
    course_date = props.get("date:日期:start")
    tags = props.get("標籤")
    if isinstance(tags, str):
        tag_tuple: tuple[str, ...] = tuple(x.strip() for x in tags.split(",") if x.strip())
    elif isinstance(tags, list):
        tag_tuple = tuple(str(x) for x in tags)
    else:
        tag_tuple = ()
    course = CoursePageRef(
        title=title,
        page_id=resolved_page_id,
        page_url=page_url,
        course_date=str(course_date) if course_date else None,
        tags=tag_tuple,
    )
    meeting_refs = [html.unescape(match.group("url")) for match in MEETING_RE.finditer(fetch_text)]
    videos: list[VideoSegment] = []
    for index, match in enumerate(VIDEO_RE.finditer(fetch_text), 1):
        source_ref = html.unescape(match.group("src"))
        video_name = infer_video_name(source_ref, index)
        transcript_ref = meeting_refs[index - 1] if index - 1 < len(meeting_refs) else None
        videos.append(
            VideoSegment(
                stable_key=stable_video_key(resolved_page_id, index, source_ref),
                course=course,
                segment_index=index,
                video_name=video_name,
                video_block_ref=f"{resolved_page_id}#video-{index:03d}",
                source_ref=source_ref,
                transcript_ref=transcript_ref,
            )
        )
    return ParsedCoursePage(course=course, videos=tuple(videos))
