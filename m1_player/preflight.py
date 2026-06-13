from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .attachment_resolver import NotionAttachmentResolver
from .config import AppConfig
from .local_settings import LOCAL_SETTINGS_PATH, load_local_settings
from .playback import find_mpv
from .progress import ProgressStore
from .resolved_url_cache import ResolvedUrlCache
from .subtitle import SUPPORTED_SUBTITLE_SUFFIXES
from .video_source import parse_video_source


@dataclass(frozen=True)
class PreflightItem:
    key: str
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    @property
    def error(self) -> bool:
        return self.status == "error"


def run_preflight(config: AppConfig) -> list[PreflightItem]:
    items: list[PreflightItem] = []
    availability = find_mpv()
    if availability.available and availability.mpv_path:
        items.append(PreflightItem("mpv", "ok", f"mpv ready: {availability.mpv_path}"))
    else:
        items.append(PreflightItem("mpv", "error", "mpv.exe not found; install mpv or set M1_MPV_PATH"))

    local_settings = load_local_settings()
    token = os.environ.get("M1_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN") or local_settings.notion_token
    if token:
        source = "environment" if os.environ.get("M1_NOTION_TOKEN") or os.environ.get("NOTION_TOKEN") else str(LOCAL_SETTINGS_PATH)
        items.append(PreflightItem("notion_token", "ok", f"Notion API token present for attachment resolver ({source})"))
        items.append(PreflightItem("sync_backend", "ok", "startup sync will use official Notion API"))
    else:
        items.append(PreflightItem("notion_token", "warning", "Notion API token missing; attachment resolver will report missing_token"))
        items.append(PreflightItem("sync_backend", "warning", "startup sync will fall back to Notion MCP and may require browser auth"))

    if local_settings.completion_database_id:
        items.append(PreflightItem("completion_database", "ok", "completion writeback data source configured"))
    else:
        items.append(PreflightItem("completion_database", "warning", "completion_database_id missing; flush_writeback will dry-run only"))

    if config.progress_cache.exists():
        store = ProgressStore(config.progress_cache)
        store.load()
        records = list(store.records.values())
        items.append(PreflightItem("progress_cache", "ok", f"cache records: {len(records)}"))
        source_counts = _source_counts(records)
        items.append(PreflightItem("video_sources", source_counts_status(source_counts), source_counts_message(source_counts)))
    else:
        items.append(PreflightItem("progress_cache", "warning", "progress cache missing; run sync first"))

    url_cache = ResolvedUrlCache(config.resolved_url_cache)
    url_cache.load()
    url_stats = url_cache.stats()
    if url_stats["total"]:
        items.append(
            PreflightItem(
                "resolved_url_cache",
                "ok" if url_stats["valid"] else "warning",
                f"resolved URL cache total={url_stats['total']} valid={url_stats['valid']} expired={url_stats['expired']}",
            )
        )
    else:
        items.append(PreflightItem("resolved_url_cache", "warning", "resolved URL cache empty"))

    subtitle_count = _count_files(config.subtitle_dir, set(SUPPORTED_SUBTITLE_SUFFIXES))
    if subtitle_count:
        items.append(PreflightItem("subtitles", "ok", f"local subtitles: {subtitle_count}"))
    else:
        items.append(PreflightItem("subtitles", "warning", "no local subtitle files found"))

    outbox_count = _line_count(config.writeback_outbox)
    if outbox_count:
        items.append(PreflightItem("writeback_outbox", "warning", f"queued writeback events: {outbox_count}"))
    else:
        items.append(PreflightItem("writeback_outbox", "ok", "no queued writeback events"))

    if config.schedule_view_url:
        items.append(PreflightItem("schedule_view", "ok", "schedule view URL configured"))
    else:
        items.append(PreflightItem("schedule_view", "error", "schedule view URL missing"))

    return items


def _source_counts(records: list[object]) -> dict[str, int]:
    counts = {"playable": 0, "needs_resolver": 0, "unknown": 0}
    for record in records:
        source = parse_video_source(getattr(record, "source_ref", ""))
        if source.playable_url:
            counts["playable"] += 1
        elif source.requires_resolution:
            counts["needs_resolver"] += 1
        else:
            counts["unknown"] += 1
    return counts


def source_counts_status(counts: dict[str, int]) -> str:
    if counts["unknown"]:
        return "warning"
    if counts["needs_resolver"] and not NotionAttachmentResolver().token:
        return "warning"
    return "ok"


def source_counts_message(counts: dict[str, int]) -> str:
    resolver = NotionAttachmentResolver()
    token_note = "token present" if resolver.token else "token missing"
    return (
        f"playable={counts['playable']} "
        f"needs_resolver={counts['needs_resolver']} "
        f"unknown={counts['unknown']} "
        f"({token_note})"
    )


def _count_files(path: Path, suffixes: set[str]) -> int:
    if not path.exists():
        return 0
    return sum(1 for file_path in path.rglob("*") if file_path.is_file() and file_path.suffix.lower() in suffixes)


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        return sum(1 for line in handle if line.strip())
