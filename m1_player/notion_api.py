from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from .attachment_resolver import NOTION_API_VERSION
from .models import CoursePageRef, VideoSegment
from .notion_parser import ParsedCoursePage, compact_notion_id


@dataclass(frozen=True)
class NotionApiPage:
    page_id: str
    page_url: str
    title: str
    course_date: str | None
    tags: tuple[str, ...]


class NotionApiClient:
    def __init__(self, token: str, timeout_sec: int = 45) -> None:
        self.token = token
        self.timeout_sec = timeout_sec

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.request(
            method,
            f"https://api.notion.com/v1{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_sec,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Notion API {method} {path} returned {response.status_code}: {response.text[:300]}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Notion API {method} {path} returned non-object JSON")
        return data

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self.request("GET", f"/databases/{database_id}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.request("GET", f"/data_sources/{data_source_id}")

    def query_data_source(
        self,
        data_source_id: str,
        page_size: int,
        start_cursor: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            payload["start_cursor"] = start_cursor
        return self.request("POST", f"/data_sources/{data_source_id}/query", payload)

    def retrieve_block_children(self, block_id: str, start_cursor: str | None = None) -> dict[str, Any]:
        query = f"?page_size=100"
        if start_cursor:
            query += f"&start_cursor={start_cursor}"
        return self.request("GET", f"/blocks/{block_id}/children{query}")

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            "/pages",
            {
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )

    def create_database(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/databases", payload)

    def set_page_in_trash(self, page_id: str, in_trash: bool = True) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"in_trash": in_trash})


def extract_notion_id(value: str) -> str:
    parsed = urlparse(value)
    search_target = parsed.path if parsed.scheme or parsed.netloc else value
    candidates = re.findall(r"[0-9a-fA-F]{32}", search_target.replace("-", ""))
    if not candidates:
        raise ValueError(f"Cannot find Notion id in value: {value}")
    return candidates[-1].lower()


def extract_database_id_from_url(value: str) -> str:
    try:
        return extract_notion_id(value)
    except ValueError as exc:
        raise ValueError(f"Cannot find Notion database id in schedule URL: {value}") from exc


def first_data_source_id(client: NotionApiClient, database_or_data_source_id: str) -> str:
    try:
        database = client.retrieve_database(database_or_data_source_id)
    except RuntimeError:
        return database_or_data_source_id
    data_sources = database.get("data_sources")
    if isinstance(data_sources, list) and data_sources:
        first = data_sources[0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return str(first["id"])
    return database_or_data_source_id


def query_schedule_pages(
    client: NotionApiClient,
    schedule_view_url: str,
    page_size: int,
    max_pages: int | None,
) -> list[dict[str, Any]]:
    database_id = extract_database_id_from_url(schedule_view_url)
    data_source_id = first_data_source_id(client, database_id)
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        result = client.query_data_source(data_source_id, page_size=page_size, start_cursor=cursor)
        rows = result.get("results")
        if isinstance(rows, list):
            pages.extend(row for row in rows if isinstance(row, dict))
        if max_pages is not None and len(pages) >= max_pages:
            return pages[:max_pages]
        if not result.get("has_more"):
            return pages
        cursor = result.get("next_cursor") if isinstance(result.get("next_cursor"), str) else None
        if not cursor:
            return pages


def parse_api_course_page(page: dict[str, Any], child_blocks: list[dict[str, Any]]) -> ParsedCoursePage:
    page_ref = api_page_ref(page)
    course = CoursePageRef(
        title=page_ref.title,
        page_id=page_ref.page_id,
        page_url=page_ref.page_url,
        course_date=page_ref.course_date,
        tags=page_ref.tags,
    )
    videos: list[VideoSegment] = []
    for index, block in enumerate(find_video_blocks(child_blocks), 1):
        source_ref, video_name = video_source_from_block(block, page_ref, index)
        videos.append(
            VideoSegment(
                stable_key=stable_video_key(page_ref.page_id, block_id(block), index),
                course=course,
                segment_index=index,
                video_name=video_name,
                video_block_ref=block_id(block),
                source_ref=source_ref,
            )
        )
    return ParsedCoursePage(course=course, videos=tuple(videos))


def retrieve_child_blocks_recursive(client: NotionApiClient, page_id: str, max_depth: int = 3) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def visit(block_id_value: str, depth: int) -> None:
        cursor: str | None = None
        while True:
            result = client.retrieve_block_children(block_id_value, start_cursor=cursor)
            children = result.get("results")
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        blocks.append(child)
                        if depth < max_depth and child.get("has_children"):
                            visit(block_id(child), depth + 1)
            if not result.get("has_more"):
                return
            cursor_value = result.get("next_cursor")
            cursor = cursor_value if isinstance(cursor_value, str) else None
            if not cursor:
                return

    visit(page_id, 0)
    return blocks


def api_page_ref(page: dict[str, Any]) -> NotionApiPage:
    page_id = compact_notion_id(str(page.get("id", "")))
    page_url = str(page.get("url") or f"https://www.notion.so/{page_id}")
    properties = page.get("properties") if isinstance(page.get("properties"), dict) else {}
    title = property_title(properties.get("名稱")) or property_title(properties.get("Name")) or "untitled_course"
    course_date = property_date(properties.get("日期")) or property_date(properties.get("Date"))
    tags = property_multi_select(properties.get("標籤")) or property_multi_select(properties.get("Tags"))
    return NotionApiPage(page_id=page_id, page_url=page_url, title=title, course_date=course_date, tags=tags)


def property_title(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    title_items = value.get("title")
    if not isinstance(title_items, list):
        return None
    text = "".join(str(item.get("plain_text", "")) for item in title_items if isinstance(item, dict)).strip()
    return text or None


def property_date(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    date_value = value.get("date")
    if not isinstance(date_value, dict):
        return None
    start = date_value.get("start")
    return str(start) if start else None


def property_multi_select(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    items = value.get("multi_select")
    if not isinstance(items, list):
        return ()
    return tuple(str(item.get("name")) for item in items if isinstance(item, dict) and item.get("name"))


def find_video_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [block for block in blocks if block.get("type") == "video" and isinstance(block.get("video"), dict)]


def video_source_from_block(block: dict[str, Any], page: NotionApiPage, index: int) -> tuple[str, str]:
    video = block.get("video") if isinstance(block.get("video"), dict) else {}
    caption = rich_text_plain(video.get("caption"))
    external = video.get("external") if isinstance(video.get("external"), dict) else None
    if external and isinstance(external.get("url"), str):
        url = str(external["url"])
        return url, caption or filename_from_url(url) or fallback_video_name(page, index)
    file_entry = video.get("file") if isinstance(video.get("file"), dict) else None
    if file_entry and isinstance(file_entry.get("url"), str):
        filename = caption or filename_from_url(str(file_entry["url"])) or fallback_video_name(page, index)
        return notion_attachment_marker(block_id(block), filename), filename
    return notion_attachment_marker(block_id(block), caption or fallback_video_name(page, index)), caption or fallback_video_name(page, index)


def notion_attachment_marker(block_id_value: str, filename: str) -> str:
    payload = {
        "source": f"attachment:{block_id_value}:{filename}",
        "permissionRecord": {"table": "block", "id": block_id_value},
    }
    return "file://" + quote(json.dumps(payload, ensure_ascii=False), safe="")


def block_id(block: dict[str, Any]) -> str:
    return compact_notion_id(str(block.get("id", "")))


def stable_video_key(course_page_id: str, video_block_id: str, segment_index: int) -> str:
    digest = hashlib.sha256(f"{course_page_id}|{video_block_id}|{segment_index}".encode("utf-8")).hexdigest()[:16]
    return f"{compact_notion_id(course_page_id)}:{segment_index:03d}:{digest}"


def rich_text_plain(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    text = "".join(str(item.get("plain_text", "")) for item in value if isinstance(item, dict)).strip()
    return text or None


def filename_from_url(value: str) -> str | None:
    tail = unquote(urlparse(value).path.rstrip("/").split("/")[-1]).strip()
    return tail or None


def fallback_video_name(page: NotionApiPage, index: int) -> str:
    return f"{page.title}_P{index:02d}.mp4"
