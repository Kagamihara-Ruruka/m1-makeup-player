from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .local_settings import load_local_settings
from .models import PlaybackRecord, VideoSegment
from .notion_api import NotionApiClient, parse_api_course_page, query_schedule_pages, retrieve_child_blocks_recursive
from .notion_mcp import NotionMcpClient, extract_tool_json, extract_tool_text
from .notion_parser import ParsedCoursePage, parse_course_page
from .progress import ProgressMetadata, ProgressStore


@dataclass(frozen=True)
class SyncResult:
    sync_backend: str
    course_pages: tuple[ParsedCoursePage, ...]
    segments: tuple[VideoSegment, ...]
    records: tuple[PlaybackRecord, ...]
    cache_path: str
    cache_metadata: ProgressMetadata


def query_schedule(
    client: NotionMcpClient,
    view_url: str,
    page_size: int,
    max_pages: int | None,
    timeout_sec: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cursor = None
    while True:
        args: dict[str, object] = {"view_url": view_url, "page_size": page_size}
        if cursor:
            args["start_cursor"] = cursor
        result = extract_tool_json(client.call_tool("query-database-view", args, timeout=timeout_sec))
        rows.extend(result.get("results", []))
        if max_pages is not None and len(rows) >= max_pages:
            return rows[:max_pages]
        if not result.get("has_more"):
            return rows
        cursor = result.get("next_cursor")
        if not cursor:
            return rows


class NotionScheduleSync:
    def __init__(self, config: AppConfig, local_settings_path: str | Path | None = None) -> None:
        self.config = config
        self.local_settings_path = local_settings_path

    def sync(self) -> SyncResult:
        token = notion_token(self.local_settings_path)
        if token:
            return self.sync_via_api(token)
        return self.sync_via_mcp()

    def sync_via_api(self, token: str) -> SyncResult:
        client = NotionApiClient(token, timeout_sec=self.config.notion_request_timeout_sec)
        rows = query_schedule_pages(
            client,
            self.config.schedule_view_url,
            page_size=self.config.page_size,
            max_pages=self.config.max_pages,
        )
        parsed_pages: list[ParsedCoursePage] = []
        segments: list[VideoSegment] = []
        for row in rows:
            page_id = str(row.get("id", ""))
            if not page_id:
                continue
            blocks = retrieve_child_blocks_recursive(client, page_id)
            parsed = parse_api_course_page(row, blocks)
            parsed_pages.append(parsed)
            segments.extend(parsed.videos)
        return self.save_records("official_notion_api", parsed_pages, segments)

    def sync_via_mcp(self) -> SyncResult:
        client = NotionMcpClient(request_timeout_sec=self.config.notion_request_timeout_sec)
        try:
            client.start()
            rows = query_schedule(
                client,
                self.config.schedule_view_url,
                self.config.page_size,
                self.config.max_pages,
                self.config.notion_request_timeout_sec,
            )
            parsed_pages: list[ParsedCoursePage] = []
            segments: list[VideoSegment] = []
            for row in rows:
                url = str(row.get("url", ""))
                if not url:
                    continue
                text = extract_tool_text(
                    client.call_tool("fetch", {"id": url}, timeout=self.config.notion_request_timeout_sec)
                )
                parsed = parse_course_page(text, page_id=url)
                parsed_pages.append(parsed)
                segments.extend(parsed.videos)
            return self.save_records("notion_mcp_fallback", parsed_pages, segments)
        finally:
            client.close()

    def save_records(self, sync_backend: str, parsed_pages: list[ParsedCoursePage], segments: list[VideoSegment]) -> SyncResult:
        store = ProgressStore(self.config.progress_cache)
        store.load()
        records = store.sync_segments(segments)
        store.record_sync_metadata(sync_backend, len(parsed_pages), len(segments))
        store.save()
        return SyncResult(
            sync_backend=sync_backend,
            course_pages=tuple(parsed_pages),
            segments=tuple(segments),
            records=tuple(records),
            cache_path=str(self.config.progress_cache),
            cache_metadata=store.metadata,
        )


def notion_token(local_settings_path: str | Path | None = None) -> str | None:
    settings = load_local_settings(local_settings_path) if local_settings_path is not None else load_local_settings()
    return os.environ.get("M1_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN") or settings.notion_token
